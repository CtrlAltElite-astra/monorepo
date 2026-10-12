"""Building a new local event (owner: Rahil). Pure apart from signing with this node's key.

The write path (region/api/writes.py, plan 6.1) and the in-memory Replica both
call new_event(), so a real region fills supersedes, observed, priority, op_id,
hash and sig exactly the way the tests and fixtures do:

    before = derive(store.events_for(conn, report_id))       # the view BEFORE this write
    ev = envelope.new_event(region, seq, h, type_, report_id, field, value, before,
                            op_id=req.op_id, actor=actor, role=role, wall=_wall())
    store.insert_event(conn, ev)

Validation (field values, roles, sizes) stays in the write path; this only builds.
"""
from region.engine import canonical, policy, priority, signing


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


def new_event(region, seq, clock, type_, report_id, field=None, value=None, view_before=None, *,
              op_id=None, reason=None, wall="", bulk=False, signer=signing.sign, **extra) -> dict:
    """The sealed, signed event for one local write.

    clock: the HLC stamp already ticked for this write. view_before: derive() of the report's
    events before this write (None for a new cart). signer: hash -> signature; the default is
    this node's key, None when the node has no key. extra adds envelope keys such as actor or role.

    - SET and RESOLVE replace every live value of the field (supersedes = live ids).
    - observed: what a close, a cart-activity note or a delivery-speed write must record
      (policy.observed_for), absent for every other write.
    - priority: the replication lane (priority.classify), stored on the event.
    """
    live = (view_before or {}).get("live", {})
    observed = policy.observed_for(type_, field, value, view_before)
    if observed is not None:
        extra["observed"] = observed
    ev = make_event(
        region, seq, type_, field, value,
        supersedes=live.get(field, []) if type_ in ("SET", "RESOLVE") else [],
        hlc=clock, report_id=report_id, op_id=op_id, reason=reason, wall=wall,
        priority=priority.classify({"type": type_, "field": field, "value": value}, view_before, bulk),
        **extra)
    sig = signer(ev["hash"]) if signer else None
    if sig:
        ev["sig"] = sig  # outside the hash, so signing changes nothing else
    return ev
