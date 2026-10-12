"""The local write path (owner: Akshit, engine calls by Rahil; v1.1 additions by Miti).

Always local, always durable before the caller sees 201. Never calls a peer.

v1.1 additions (plan section 6.1):
  * op_id idempotency  - the same client operation applied twice (a retry after a lost ack, or a
                         failover retry on another node) produces ONE event. A retry that reaches a
                         node which already received the original by replication or carry returns
                         that event instead of writing a second one.
  * client report_id   - a CREATE may name its own `R-<8 hex>` id, so a retried create is safe.
  * observed           - close / new-info writes list the events the writer had seen (CLOSE_VS_NEW_INFO).
  * sig                - signed with this region's Ed25519 key when one is provisioned.
"""
import hashlib
import re
from datetime import datetime, timezone

from region import config, store
from region.engine import canonical, hlc, signing
from region.engine.derive import CLOSED, derive

CLIENT_REPORT_ID = re.compile(r"^R-[0-9a-f]{8}$")


class NotFound(Exception):
    pass


class StaleEdit(Exception):
    def __init__(self, live):
        self.live = live


class OpIdReuse(Exception):
    """Same op_id, different request."""


class ReportExists(Exception):
    """A client-chosen report_id that is already taken."""


def _wall() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def request_hash(report_id, type_, field, value, expected, bind_actor=None) -> str:
    body = [report_id, type_, field, value, sorted(expected) if expected is not None else None]
    if bind_actor is not None:   # auth mode: an op_id belongs to the actor who first used it
        body.append(bind_actor)
    return hashlib.sha256(canonical.dumps(body).encode("utf-8")).hexdigest()


def _observed(conn, report_id: str, type_: str, field, value):
    """Event ids the writer had seen, for the CLOSE_VS_NEW_INFO rule; None when not applicable."""
    if type_ in ("SET", "RESOLVE") and field == "status" and value in CLOSED:
        evs = store.events_for(conn, report_id)
        ids = [e["event_id"] for e in evs
               if e["type"] == "NOTE" or (e["field"] == "severity" and e["type"] in ("SET", "RESOLVE"))
               or (e["type"] == "CREATE" and isinstance(e["value"], dict) and "severity" in e["value"])]
        return sorted(set(ids))
    if type_ == "NOTE" or (type_ == "SET" and field == "severity"):
        evs = store.events_for(conn, report_id)
        return sorted(set(derive(evs)["live"].get("status", [])))
    return None


def local_write_ex(report_id, type_, field, value, actor, expected=None, op_id=None, new_report_id=None,
                   provenance=None, reason=None):
    """Append one event (or return the one this op_id already produced).

    `provenance` (auth mode only): {"role", "auth_mode", "credential_id"} taken from the VERIFIED session. It is
    placed in the envelope BEFORE hashing and signing, so it is part of the signed history, and it also binds
    the op_id to the actor. `reason` is required by RESOLVE.

    Returns (event, replayed). Raises NotFound, StaleEdit, OpIdReuse, ReportExists.
    """
    region = config.settings.region
    bind = actor if provenance is not None else None
    rh = request_hash(new_report_id if type_ == "CREATE" else report_id, type_, field, value, expected, bind)
    with store.tx() as conn:
        if op_id:
            hit = store.op_get(conn, op_id)
            if hit:
                if hit["req_hash"] != rh:
                    raise OpIdReuse(op_id)
                ev = store.event_by_id(conn, hit["event_id"])
                if ev:
                    return ev, True
            prior = store.event_by_op(conn, op_id)  # original arrived by replication or carry
            if prior:
                same_target = type_ == "CREATE" or prior["report_id"] == report_id
                if prior["type"] != type_ or not same_target:
                    raise OpIdReuse(op_id)
                if provenance is not None and prior.get("actor") != actor:
                    raise OpIdReuse(op_id)   # someone else's operation: never hand out their result
                return prior, True
        if type_ == "CREATE":
            if new_report_id and store.report_exists(conn, new_report_id):
                raise ReportExists(new_report_id)
        elif not store.report_exists(conn, report_id):
            raise NotFound(report_id)
        live = store.live_ids(conn, report_id, field) if (field and type_ != "CREATE") else []
        if expected is not None and sorted(expected) != sorted(live):
            raise StaleEdit(live)
        seq = store.next_seq(conn)
        if type_ == "CREATE":
            report_id = new_report_id or f"R-{region}-{seq}"
        h = hlc.tick(store.load_hlc(conn), store.now_ms())
        store.save_hlc(conn, h)
        ev = dict(
            v=1, event_id=f"{region}:{seq}", origin=region, seq=seq, report_id=report_id,
            type=type_, field=field, value=value, supersedes=live, hlc=[h[0], h[1]],
            priority=1, actor=actor, reason=reason, wall=_wall(), sig=None,
            op_id=op_id, observed=_observed(conn, report_id, type_, field, value) if type_ != "CREATE" else None,
        )
        if provenance is not None:   # before canonical hashing and signing, never attached afterwards
            ev.update(role=provenance.get("role"), auth_mode=provenance.get("auth_mode"),
                      credential_id=provenance.get("credential_id"))
            if type_ == "CREATE":
                ev["owner"] = actor   # immutable cart owner, replicated through the signed event log
        ev["hash"] = canonical.event_hash(ev)
        ev["sig"] = signing.sign(region, ev["hash"])  # None when no key is provisioned
        store.insert_event(conn, ev)
        store.save_view(conn, report_id, derive(store.events_for(conn, report_id)))
        if op_id:
            store.op_put(conn, op_id, rh, ev["event_id"])
    return ev, False  # committed


def local_write(report_id, type_, field, value, actor, expected=None, op_id=None, new_report_id=None,
                provenance=None, reason=None) -> dict:
    """Same as local_write_ex but returns only the event (kept for existing callers and tests)."""
    return local_write_ex(report_id, type_, field, value, actor, expected, op_id, new_report_id,
                          provenance, reason)[0]
