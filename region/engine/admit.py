"""May a received event enter the log? (owner: Rahil). Pure apart from the key registry.

One rule for every event this node did not write itself: a page pulled from a
peer, a push, and above all an outbox a browser carries after a crash
(POST /v1/carry, plan 6.2). The checks run in the order of the plan and the
first failure is the reason the caller reports:

    shape           not an event envelope derive() can use, or event_id != origin:seq
    unknown_origin  no public key for the region that claims to have written it
    bad_hash        a field changed after the origin sealed it
    bad_sig         not signed by the origin's own key (missing, forged, or another node's)

A received event is never checked against business rules: the origin decided
those at write time and the signature binds them. Duplicates are not a reason
to reject; the caller stores with INSERT OR IGNORE and answers 'dup'.
"""
from contracts.schemas import EVENT_REQUIRED
from region.engine import canonical, signing
from region.engine.derive import well_formed

REASONS = ("shape", "unknown_origin", "bad_hash", "bad_sig")


def shape_ok(ev) -> bool:
    """Every required key, an id that matches origin and seq, and the shape derive() relies on."""
    return (isinstance(ev, dict) and all(k in ev for k in EVENT_REQUIRED) and well_formed(ev)
            and isinstance(ev["hash"], str) and ev["event_id"] == f"{ev['origin']}:{ev['seq']}")


def reject_reason(ev, public_keys=None, unsigned_ok=False):
    """None when ev may be stored, else one of REASONS.

    public_keys: {region: public key}; None means this node's registry (region/trust/public_keys.json).
    unsigned_ok: let an event with no signature at all through on shape and hash alone. Only
    for peer sync while some node still writes unsigned events; /v1/carry never sets it.
    """
    if not shape_ok(ev):
        return "shape"
    if unsigned_ok and ev.get("sig") is None:
        return None if canonical.event_hash(ev) == ev["hash"] else "bad_hash"
    origin = ev["origin"]
    known = origin in public_keys if public_keys is not None else signing.has_key(origin)
    if not known:
        return "unknown_origin"
    if canonical.event_hash(ev) != ev["hash"]:
        return "bad_hash"
    if public_keys is not None:
        signed = signing.verify_with(public_keys[origin], ev["hash"], ev.get("sig"))
    else:
        signed = signing.verify(origin, ev["hash"], ev.get("sig"))
    return None if signed else "bad_sig"
