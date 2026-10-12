"""Event builder for tests, fixtures and examples (owner: Rahil). Pure."""
from region.engine import canonical


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
