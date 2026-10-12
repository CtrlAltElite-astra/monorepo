"""Frozen shapes shared by every lane (single owner: Akshit).

After 14:30 this file may only GROW (new optional things). Never rename.
Pure python, no I/O, no third-party imports, so the engine can import it.
"""

CONTRACT_VERSION = 1

EVENT_TYPES = ("CREATE", "SET", "NOTE", "RESOLVE")  # RESOLVE is used from PDF 2

# Order matters only for deterministic iteration in derive().
FIELDS = [
    "title", "description", "category", "location",
    "severity", "status", "assigned_team", "people_estimate",
]

CATEGORIES = ("flood", "collapse", "medical", "fire", "road_block", "shelter", "other", "cart")  # cart: shop demo, v1.1
STATUSES = ("open", "assigned", "resolved", "closed", "cancelled")

PRIORITY_CRITICAL, PRIORITY_NORMAL, PRIORITY_BULK = 0, 1, 2

# Region level shown in /v1/status. HEALTHY/SYNCING/ISOLATED are produced today;
# CONSTRAINED and RECOVERING (degradation ladder) and OVERLOADED arrive in PDF 2.
LEVELS = ("HEALTHY", "SYNCING", "CONSTRAINED", "ISOLATED", "RECOVERING", "OVERLOADED")

# Report consistency.replication
REPLICATION_STATES = ("LOCAL_ONLY", "PARTIAL", "REPLICATED")
# Every key of a report's consistency block (contracts.md 4.0)
CONSISTENCY_KEYS = ("replication", "confirmed_by", "awaiting", "stale_possible")

# Optional, nullable /v1/status blocks (null until the owning lane fills them in).
RISK_KEYS = ("unreplicated", "critical_unreplicated", "oldest_age_ms")  # data-at-risk meter
QUEUE_KEYS = ("P0", "P1", "P2")                                         # pending push per priority lane

# Every key of an event envelope (4.1). "hash" and "sig" are excluded from the hash.
EVENT_KEYS = (
    "v", "event_id", "origin", "seq", "report_id", "type", "field", "value",
    "supersedes", "hlc", "priority", "actor", "reason", "wall", "hash", "sig",
    "op_id",     # v1.1: client operation id, null for server-made events
    "observed",  # v1.1: event ids the writer had seen; set only on close / new-info writes
)
EVENT_REQUIRED = (
    "event_id", "origin", "seq", "report_id", "type", "supersedes",
    "hlc", "hash", "value",
)


def validate_field(field: str, value):
    """Return the cleaned value or raise ValueError. Used at WRITE time only.

    A received event is never re-validated against business rules (PDF 2 6.1).
    """
    if field not in FIELDS:
        raise ValueError(f"unknown field '{field}'")
    if field == "title":
        if not isinstance(value, str) or not 1 <= len(value) <= 120:
            raise ValueError("title must be 1-120 chars")
    elif field == "description":
        if value is None:
            return ""
        if not isinstance(value, str) or len(value) > 2000:
            raise ValueError("description must be a string up to 2000 chars")
    elif field == "category":
        if value not in CATEGORIES:
            raise ValueError(f"category must be one of {CATEGORIES}")
    elif field == "severity":
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
            raise ValueError("severity must be an int 1-5")
    elif field == "status":
        if value not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
    elif field == "location":
        if value is not None:
            ok = (isinstance(value, dict) and set(value) == {"lat_e6", "lon_e6"}
                  and all(isinstance(v, int) and not isinstance(v, bool) for v in value.values()))
            if not ok:
                raise ValueError("location must be null or {lat_e6:int, lon_e6:int}")
    elif field == "assigned_team":
        if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 80):
            raise ValueError("assigned_team must be null or a short string")
    elif field == "people_estimate":
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError("people_estimate must be null or an int >= 0")
    return value
