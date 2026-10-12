"""GET /v1/status and /v1/health (owner: Akshit + Simrit)."""
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


@router.get("/status")
def status():
    snap = state.snapshot()
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
            "peers": snap["peers"],
            "level": snap["level"],
            "quarantined": store.quarantine_count(conn),
            "incomplete_reports": sum(1 for v in views.values() if v["incomplete"]),
            # Nullable until the owning lane supplies them (contracts.md 4.2). Lane D can add
            # "risk"/"queues" to state.snapshot() and they flow through with no change here.
            "risk": snap.get("risk"),
            "queues": snap.get("queues"),
            "carry": carry_mod.carry_stats(),
        }
