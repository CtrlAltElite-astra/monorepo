"""Building a new local event (owner: Rahil). Pure apart from signing with this node's key.

The in-memory nodes (region/engine/model.py), the F1 fuzz test and the T17 harness build every
write with new_event(). It follows the real write path (region/api/writes.py, plan 6.1) field for
field, so what the tests prove is what the regions do:

    supersedes  every live value of the field (SET and RESOLVE)
    observed    what writes._observed records (CLOSE_VS_NEW_INFO evidence), None for other writes
    priority    engine.priority.classify on the report as it was before the write
    hash, sig   canonical hash, then the origin's Ed25519 signature over it

Validation (field values, roles, sizes) stays in the write path; this only builds.
"""
from region.engine import canonical, priority, signing
from region.engine.derive import CLOSED, derive


def make_event(origin, seq, type_, field=None, value=None, supersedes=(), hlc=None, report_id="R-1", **extra) -> dict:
    """One sealed event envelope (contract 5.1): the given fields, defaults for the rest, and its hash.

    extra overrides or adds keys, e.g. op_id="..." or observed=[...] (observed is absent unless given).
    """
    ev = dict(v=1, event_id=f"{origin}:{seq}", origin=origin, seq=seq, report_id=report_id, type=type_,
              field=field, value=value, supersedes=list(supersedes), hlc=list(hlc or (1000 + seq, 0)),
              priority=1, op_id=None, actor=f"agent-{origin}-1", reason=None, wall="", sig=None)
    ev.update(extra)
    ev["hash"] = canonical.event_hash(ev)
    return ev


def observed_for(type_: str, field, value, events) -> list:
    """The evidence a new write records, exactly as region/api/writes._observed does.

    A status write to closed/cancelled lists every NOTE and every severity write the node holds (live or
    superseded, CREATE included). A NOTE or a severity write lists the live status events. Others: None.
    events: the report's events BEFORE this write.
    """
    events = list(events)
    if type_ in ("SET", "RESOLVE") and field == "status" and value in CLOSED:
        return sorted({e["event_id"] for e in events
                       if e["type"] == "NOTE" or (e.get("field") == "severity" and e["type"] in ("SET", "RESOLVE"))
                       or (e["type"] == "CREATE" and isinstance(e["value"], dict) and "severity" in e["value"])})
    if type_ == "NOTE" or (type_ in ("SET", "RESOLVE") and field == "severity"):
        return sorted(set(derive(events)["live"].get("status", [])))
    return None


def new_event(region, seq, clock, type_, report_id, field=None, value=None, events_before=(), *,
              op_id=None, reason=None, wall="", bulk=False, signer="node", **extra) -> dict:
    """The sealed, signed event for one local write.

    clock: the HLC stamp already ticked for this write. events_before: the report's events before this
    write (empty for a new cart). signer: "node" signs with this node's key file (signing.sign), None
    leaves the event unsigned, or a function hash -> signature. extra adds envelope keys (actor, role...).
    """
    events_before = list(events_before)
    before = derive(events_before) if events_before else None
    live = (before or {}).get("live", {})
    ev = make_event(
        region, seq, type_, field, value,
        supersedes=live.get(field, []) if type_ in ("SET", "RESOLVE") else [],
        hlc=clock, report_id=report_id, op_id=op_id, reason=reason, wall=wall,
        observed=observed_for(type_, field, value, events_before) if type_ != "CREATE" else None,
        priority=priority.classify({"type": type_, "field": field, "value": value}, before, bulk),
        **extra)
    if signer == "node":
        sig = signing.sign(region, ev["hash"])
    else:
        sig = signer(ev["hash"]) if signer else None
    if sig:
        ev["sig"] = sig  # outside the hash, so signing changes nothing else
    return ev
