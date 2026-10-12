"""Status snapshot builder (owner: Simrit).

Produces /v1/status v1.1 payload conforming to PDF 2 Section 5.4, 6.8:
- Degradation ladder level (HEALTHY, CONSTRAINED, ISOLATED, RECOVERING, OVERLOADED)
- Heads & peer views with acked watermarks and latency
- Event digest & state hash
- Data-at-risk meter (unreplicated, critical_unreplicated, oldest_age_ms)
- Priority queue depth (P0, P1, P2)
- Browser carry metrics
"""
from contracts.schemas import CONTRACT_VERSION
from region import config, store
from region.engine import digest
from region.engine.derive import POLICY_VERSION
from region.sync import state


def build_status() -> dict:
    """Build complete v1.1 status snapshot."""
    snap = state.snapshot()
    with store.read() as conn:
        views = store.all_views(conn, include_incomplete=True)
        event_pairs = store.event_pairs(conn)
        ev_digest = digest.event_digest(event_pairs)
        st_hash = digest.state_hash(views)
        quarantined = store.quarantine_count(conn)
        ev_count = store.event_count(conn)
        incomplete_cnt = sum(1 for v in views.values() if v.get("incomplete"))

    return {
        "region": config.settings.region,
        "schema_version": CONTRACT_VERSION,
        "policy_version": POLICY_VERSION,
        "heads": state.heads(),
        "level": snap["level"],
        "peers": snap["peers"],
        "event_digest": ev_digest,
        "state_hash": st_hash,
        "quarantined": quarantined,
        "event_count": ev_count,
        "incomplete_reports": incomplete_cnt,
        "risk": snap["risk"],
        "queues": snap["queues"],
        "carry": snap.get("carry", {"received": 0, "new": 0, "dup": 0, "rejected": 0}),
    }
