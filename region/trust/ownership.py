"""Cart ownership and visibility (auth mode only; SyncShield convergence).

The owner is an immutable `owner` field set at CREATE from the verified session and carried in the signed
event, so every region (and a failover target) knows it without asking anyone. It is never read from a title,
a NOTE body or anything the client types.

Visibility rule (explicit, documented):
    officer      reads and edits ONLY carts they own
    supervisor   reads every cart (review duty) and may resolve; edits only carts they own
    admin        reads every cart for administration; never writes carts (roles.py)
A cart with no recorded owner (created before auth was enabled) is visible to supervisor/admin only.
"""
import json
from typing import Optional

SEE_ALL = ("supervisor", "admin")


def report_owner(conn, report_id: str) -> Optional[str]:
    """Owner of the EARLIEST create event (by the engine's tie-break order), so every region agrees."""
    row = conn.execute(
        "SELECT body FROM events WHERE report_id=? AND type='CREATE' ORDER BY hlc_l, hlc_c, origin, seq LIMIT 1",
        (report_id,),
    ).fetchone()
    return json.loads(row[0]).get("owner") if row else None


def owners(conn) -> dict:
    """{report_id: owner or None} for every report that has a CREATE event."""
    best: dict = {}
    for rid, body, hl, hc, origin, seq in conn.execute(
            "SELECT report_id, body, hlc_l, hlc_c, origin, seq FROM events WHERE type='CREATE'"):
        key = (hl, hc, origin, seq)
        if rid not in best or key < best[rid][0]:
            best[rid] = (key, json.loads(body).get("owner"))
    return {rid: v[1] for rid, v in best.items()}


def can_read(auth, owner: Optional[str]) -> bool:
    return auth.role in SEE_ALL or (owner is not None and owner == auth.actor)


def can_write(auth, owner: Optional[str]) -> bool:
    return owner is not None and owner == auth.actor
