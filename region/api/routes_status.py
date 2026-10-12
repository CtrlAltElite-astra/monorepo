"""GET /v1/status and /v1/health (owner: Akshit mounts; Simrit's region/sync/state.py builds the snapshot).

Status v1.1 (contract 5.4). The data-at-risk block is computed here from the peers' acknowledged heads when the
sync snapshot does not provide one, so the meter works with any snapshot version; `queues` stays null until the
sync lane supplies it and `carry` falls back to the node's own carry counters. A snapshot value always wins.
"""
import time

from fastapi import APIRouter

from contracts.schemas import CONTRACT_VERSION
from region import config, store
from region.engine import digest
from region.engine.derive import POLICY_VERSION
from region.sync import carry as carry_mod, state

router = APIRouter(prefix="/v1", tags=["status"])


@router.get("/health")
def health():
    return {"region": config.settings.region, "ok": True}


def _peers_v11(peers: dict) -> dict:
    """Add the v1.1 keys next to the snapshot's own: `acked` (what the peer last said it holds) and last_ok_ms."""
    out = {}
    for name, p in peers.items():
        last_ok_s = p.get("last_ok_s")
        out[name] = {**p, "acked": p.get("acked", p.get("their_heads", {})),
                     "last_ok_ms": p.get("last_ok_ms", None if last_ok_s is None else int(last_ok_s * 1000))}
    return out


def data_at_risk(conn, peers: dict) -> dict:
    """Events of THIS region that no peer has acknowledged: seq above every peer's acknowledged head for us.

    These are the events a crash of this node would lose unless a browser outbox carries them."""
    region = config.settings.region
    highest_ack = max((p["acked"].get(region, 0) for p in peers.values()), default=0)
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(priority = 0), 0), MIN(seq) FROM events WHERE origin=? AND seq>?",
        (region, highest_ack)).fetchone()
    unreplicated, critical, first = row[0], row[1], row[2]
    oldest_age_ms = 0
    if first is not None:
        received = conn.execute("SELECT received_at FROM events WHERE origin=? AND seq=?", (region, first)).fetchone()
        oldest_age_ms = max(0, int((time.time() - received[0]) * 1000))
    return {"unreplicated": unreplicated, "critical_unreplicated": critical, "oldest_age_ms": oldest_age_ms}


@router.get("/status")
def status():
    snap = state.snapshot()
    peers = _peers_v11(snap["peers"])
    with store.read() as conn:
        views = store.all_views(conn, include_incomplete=True)
        return {
            "region": config.settings.region,
            "schema_version": CONTRACT_VERSION,
            "policy_version": POLICY_VERSION,
            "heads": state.heads(),
            "event_count": store.event_count(conn),
            "event_digest": digest.event_digest(store.event_pairs(conn)),
            "state_hash": digest.state_hash(views),
            "peers": peers,
            "level": snap["level"],
            "quarantined": store.quarantine_count(conn),
            "incomplete_reports": sum(1 for v in views.values() if v["incomplete"]),
            "risk": snap.get("risk") or data_at_risk(conn, peers),
            "queues": snap.get("queues"),
            "carry": snap.get("carry") or carry_mod.carry_stats(),
        }
