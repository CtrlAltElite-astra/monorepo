"""The local write path v2 (owner: Akshit; engine calls by Rahil; v1.1 first cut by Miti, merged here).

Always local, always durable before the caller sees a 2xx. Never calls a peer.
Every event is hashed and signed by THIS region inside the write transaction (plan 6.1):

  op_id        client UUID. A retry carrying the same op_id never creates a second event: the node
               replays the original event (also when that event arrived by replication or carry).
               Same op_id with a different body is refused (OpIdReuse).
  report_id    a client-chosen "R-" + 8 hex for carts; otherwise the server makes R-<region>-<seq>.
  observed     the evidence a close / new-info write had seen (CLOSE_VS_NEW_INFO, plan 6.7).
  sig          Ed25519 by this region's key (region/engine/signing.py); None when no key is provisioned.

Auth mode (AUTH_ENABLED, TRUE credential sessions) adds `provenance` = {role, auth_mode, credential_id} taken from
the VERIFIED session. It is put in the envelope BEFORE hashing and signing, so it is part of the signed history
(a CREATE also fixes the cart `owner`), and it binds an op_id to the actor who first used it.

Rate limit (contract v1.2, 8.3; region/api/ratelimit.py, off by default): every write that is not a replay spends
one token of the caller's allowance, inside the transaction, right after the replay check and before anything is
read or stored. With no token left the write raises RateLimited: nothing is stored and no sequence number is used.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from region import config, store
from region.api import ratelimit
from region.engine import canonical, hlc, priority, signing
from region.engine.derive import CLOSED, derive

# NotFound, StaleEdit, ReportExists and NoConflict raised AFTER the caller was charged carry `.rate` (the allowance
# left), so the HTTP layer can report it on a 404 / 409 as well.


class NotFound(Exception):
    pass


class StaleEdit(Exception):
    def __init__(self, live):
        self.live = live


class OpIdReuse(Exception):
    """The same op_id was already used for a different request."""


class ReportExists(Exception):
    """A CREATE named a client report_id that already exists here."""


class NoConflict(Exception):
    """resolve() was asked to settle a field that has nothing to settle."""


@dataclass(frozen=True)
class WriteResult:
    event: dict
    replay: bool = False  # True: an earlier write with the same op_id was returned, nothing new stored
    rate: Optional[ratelimit.Decision] = None  # the caller's allowance after this write; None while the limit is off


_loop = None  # the server's event loop, set by main.py; lets a worker thread hand an event to the push queue


def set_loop(loop) -> None:
    global _loop
    _loop = loop


def _wall() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _priority(conn, ev: dict) -> int:
    """0 critical / 1 normal / 2 bulk (plan 4.2): engine.priority.classify on the report as it was before
    this write, stored on the event. The push queue sends by the stored value."""
    before = derive(store.events_for(conn, ev["report_id"])) if ev["type"] != "CREATE" else None
    return priority.classify(ev, before)


def _observed(conn, report_id: str, type_: str, field: Optional[str], value) -> Optional[list]:
    """Evidence for CLOSE_VS_NEW_INFO: the event ids this node had seen when it wrote.

    A status write (SET or RESOLVE) to closed/cancelled lists every NOTE and every severity write it holds
    (all of them, live or superseded, so a later-lowered severity can never raise a false alarm);
    a NOTE or a severity write lists the live status events it holds; every other write carries none."""
    if type_ in ("SET", "RESOLVE") and field == "status" and value in CLOSED:
        ids = [e["event_id"] for e in store.events_for(conn, report_id)
               if e["type"] == "NOTE" or (e.get("field") == "severity" and e["type"] in ("SET", "RESOLVE"))
               or (e["type"] == "CREATE" and isinstance(e["value"], dict) and "severity" in e["value"])]
        return sorted(set(ids))
    if type_ == "NOTE" or (type_ in ("SET", "RESOLVE") and field == "severity"):
        return sorted(set(store.live_ids(conn, report_id, "status")))
    return None


def _same_request(prior: dict, type_: str, report_id: Optional[str], field: Optional[str], value) -> bool:
    """Is this request the one that produced `prior`? (op_id reuse check; compares what the event stores)"""
    return (prior["type"] == type_ and prior.get("field") == field and prior["value"] == value
            and (report_id is None or prior["report_id"] == report_id))


def _append(conn, *, report_id, type_, field, value, actor, role, auth_mode, op_id, supersedes, reason=None,
            provenance=None) -> dict:
    """Build, hash, sign and store one event and refresh its report view. Caller holds store.tx()."""
    region = config.settings.region
    seq = store.next_seq(conn)
    if type_ == "CREATE" and report_id is None:
        report_id = f"R-{region}-{seq}"
    h = hlc.tick(store.load_hlc(conn), store.now_ms())
    store.save_hlc(conn, h)
    ev = dict(
        v=1, event_id=f"{region}:{seq}", origin=region, seq=seq, report_id=report_id,
        type=type_, field=field, value=value, supersedes=supersedes, hlc=[h[0], h[1]],
        priority=1, op_id=op_id, observed=_observed(conn, report_id, type_, field, value) if type_ != "CREATE" else None,
        actor=actor, role=role, auth_mode=auth_mode, reason=reason, wall=_wall(), sig=None,
    )
    if provenance is not None:   # auth mode: before canonical hashing and signing, never attached afterwards
        ev.update(role=provenance.get("role"), auth_mode=provenance.get("auth_mode"),
                  credential_id=provenance.get("credential_id"))
        if type_ == "CREATE":
            ev["owner"] = actor   # immutable cart owner, replicated through the signed event log
    ev["priority"] = _priority(conn, ev)
    ev["hash"] = canonical.event_hash(ev)
    ev["sig"] = signing.sign(region, ev["hash"])  # None when no key is provisioned: carry then refuses the event
    store.insert_event(conn, ev)
    store.save_view(conn, report_id, derive(store.events_for(conn, report_id)))
    return ev


def _after_commit(ev: dict) -> None:
    """Hand the committed event to the push queue. Best effort: the log is the outbox, pull repairs any loss."""
    loop = _loop
    if loop is None or not config.settings.push_enabled:
        return
    try:
        from region.sync import push
        loop.call_soon_threadsafe(push.enqueue, ev)
    except Exception:
        pass


def _own_op(prior: dict, actor: str, provenance) -> bool:
    """Auth mode: an op_id belongs to the actor who first used it; never hand out someone else's result."""
    return provenance is None or prior.get("actor") == actor


def _charge(subject: str) -> Optional[ratelimit.Decision]:
    """Spend one token of `subject`'s allowance, or raise RateLimited. None while the limit is off."""
    rate = ratelimit.take(subject)
    if rate is not None and not rate.allowed:
        raise ratelimit.RateLimited(rate)
    return rate


def local_write(report_id, type_, field, value, actor, expected=None, *, op_id=None,
                role="officer", auth_mode="online", provenance=None, reason=None, rate_key=None) -> WriteResult:
    """Append one event and refresh the report view in the same transaction.

    rate_key: whose rate-limit allowance this write spends (ratelimit.key(auth)); the actor when not given."""
    subject = rate_key or actor
    rate = None
    try:
        with store.tx() as conn:
            if op_id:
                prior = store.event_by_op(conn, op_id)
                if prior is not None:  # same operation seen here already (written here, replicated or carried)
                    if not _same_request(prior, type_, report_id, field, value) or not _own_op(prior, actor, provenance):
                        raise OpIdReuse(op_id)
                    return WriteResult(prior, replay=True, rate=ratelimit.peek(subject))  # a retry is never charged
            rate = _charge(subject)
            if type_ == "CREATE":
                if report_id is not None and store.report_exists(conn, report_id):
                    raise ReportExists(report_id)
                live = []
            else:
                if not store.report_exists(conn, report_id):
                    raise NotFound(report_id)
                live = store.live_ids(conn, report_id, field) if field else []
            if expected is not None and sorted(expected) != sorted(live):
                raise StaleEdit(live)
            ev = _append(conn, report_id=report_id, type_=type_, field=field, value=value, actor=actor,
                         role=role, auth_mode=auth_mode, op_id=op_id, supersedes=live, reason=reason,
                         provenance=provenance)
    except (NotFound, StaleEdit, ReportExists) as exc:
        exc.rate = rate
        raise
    _after_commit(ev)
    return WriteResult(ev, rate=rate)


def local_write_ex(report_id, type_, field, value, actor, expected=None, op_id=None, new_report_id=None,
                   provenance=None, reason=None):
    """Compatibility shape used by Lane C's first cut: returns (event, replayed)."""
    result = local_write(new_report_id if type_ == "CREATE" else report_id, type_, field, value, actor,
                         expected, op_id=op_id, provenance=provenance, reason=reason)
    return result.event, result.replay


def resolve(report_id, field, value, candidates, reason, actor, *, role="supervisor", auth_mode="online",
            op_id=None, provenance=None, rate_key=None) -> WriteResult:
    """A supervisor settles a conflicted field (plan 6.7). The RESOLVE event supersedes exactly the live
    candidates and replicates like any event; it never hides an edit it did not see."""
    subject = rate_key or actor
    rate = None
    try:
        with store.tx() as conn:
            if op_id:
                prior = store.event_by_op(conn, op_id)
                if prior is not None:
                    if not (prior["type"] == "RESOLVE" and prior.get("field") == field and prior["value"] == value
                            and _own_op(prior, actor, provenance)):
                        raise OpIdReuse(op_id)
                    return WriteResult(prior, replay=True, rate=ratelimit.peek(subject))
            rate = _charge(subject)
            if not store.report_exists(conn, report_id):
                raise NotFound(report_id)
            live = store.live_ids(conn, report_id, field)
            if sorted(live) != sorted(candidates):
                raise StaleEdit(live)  # it changed while the supervisor was looking
            view = derive(store.events_for(conn, report_id))
            if field not in [c["field"] for c in view["conflicts"]]:
                raise NoConflict(field)
            ev = _append(conn, report_id=report_id, type_="RESOLVE", field=field, value=value, actor=actor,
                         role=role, auth_mode=auth_mode, op_id=op_id, supersedes=live, reason=reason,
                         provenance=provenance)
    except (NotFound, StaleEdit, NoConflict) as exc:
        exc.rate = rate
        raise
    _after_commit(ev)
    return WriteResult(ev, rate=rate)
