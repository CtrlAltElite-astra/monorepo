"""Conflict receipts for the API (owner: Akshit; the rules themselves belong to Rahil's engine).

The engine's derive() reports a conflict per field. The API shows it as a receipt (PDF 6.7): both
candidate values with their origin, actor and time, the rule that picked the provisional value, and
the policy version. If the engine already built a full receipt (it has `candidates`), it passes through
unchanged; otherwise this fills in the same shape from the stored events, so every consumer sees one
shape whichever engine version is running.
"""
import json
from typing import Optional


def _event(conn, event_id: str) -> Optional[dict]:
    row = conn.execute("SELECT body FROM events WHERE event_id=?", (event_id,)).fetchone()
    return json.loads(row[0]) if row else None


def _value_of(event: dict, field: str):
    return event["value"].get(field) if event["type"] == "CREATE" else event["value"]


def receipt(conn, view: dict, conflict: dict) -> dict:
    """The receipt for one conflict of a derived view. Extra legacy keys (values, event_ids) are kept."""
    if "candidates" in conflict:
        return conflict
    field = conflict["field"]
    candidates = []
    for event_id in conflict.get("event_ids", []):
        ev = _event(conn, event_id)
        if ev is not None:
            candidates.append({"event_id": event_id, "value": _value_of(ev, field), "origin": ev["origin"],
                               "actor": ev.get("actor"), "wall": ev.get("wall")})
    return {
        **conflict,
        "relationship": "CONCURRENT",
        "candidates": candidates,
        "rule": conflict.get("rule"),
        "policy_version": view.get("policy_version", 0),
        "provisional": view["state"].get(field),
        "needs_review": conflict.get("needs_review", True),
        "flags": conflict.get("flags", []),
    }
