"""Replication priority (owner: Rahil). Pure. The lower number crosses a slow link first.

P0 critical: paying, placing or cancelling an order, delivery speed raised to
Same-day or above, a fulfilment centre assigned, any supervisor decision, a
cart created at Same-day or above. P2 bulk: cart activity notes and bulk seeds.
P1 normal: everything else. The write path stores the result on the event.
"""
from contracts.schemas import PRIORITY_BULK, PRIORITY_CRITICAL, PRIORITY_NORMAL

CRITICAL_SEVERITY = 4  # Same-day (4) and Priority (5) delivery
END_STATUSES = ("resolved", "closed", "cancelled")


def is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def classify(ev: dict, view=None, bulk: bool = False) -> int:
    """Lane of a new event. view: the report's view BEFORE this write (None when there is none)."""
    kind, field, value = ev.get("type"), ev.get("field"), ev.get("value")
    if bulk or kind == "NOTE":
        return PRIORITY_BULK
    if kind == "RESOLVE":
        return PRIORITY_CRITICAL
    if kind == "CREATE":
        severity = value.get("severity") if isinstance(value, dict) else None
        critical = is_number(severity) and severity >= CRITICAL_SEVERITY
    elif kind == "SET" and field == "severity":
        before = (view or {}).get("state", {}).get("severity")
        critical = is_number(value) and value >= CRITICAL_SEVERITY and not (is_number(before) and before >= value)
    elif kind == "SET" and field == "assigned_team":
        critical = value is not None
    elif kind == "SET" and field == "status":
        critical = value in END_STATUSES
    else:
        critical = False
    return PRIORITY_CRITICAL if critical else PRIORITY_NORMAL
