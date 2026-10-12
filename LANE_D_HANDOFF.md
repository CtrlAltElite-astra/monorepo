# Lane D Handoff Record — Simrit
**Role:** Replication, Partition Lab, Runtime, Recovery, Trial & Reward Lead  
**Branch:** `d/simrit`  
**Current Status:** Lane D basic plan implementation complete & fully verified across all 23 tests  
**Last Updated:** 2026-10-12

---

## 1. Completed
- [x] **Git Isolation & Safety:** Checked out dedicated branch `d/simrit` from `main`. Left all changes uncommitted for user review. No changes to `main` or other branches.
- [x] **Gap-Safe Contiguous Watermarks:** Verified and implemented contiguous watermark tracking in `region/sync/state.py` and `region/sync/client.py`. Out-of-order events advance `max_seen` without jumping `contiguous`. Pull requests `after=state.contiguous(origin)`.
- [x] **Push Fast Path (`region/sync/push.py`):**
  - In-memory priority queue `PushQueue` ordering `(priority, hlc, event_id)` where P0 critical < P1 normal < P2 bulk.
  - Background `push_worker` sending batches to `/internal/v1/sync/push`.
  - Automatic `rebuild_unacked(peer)` on heal or idle periods so no writes are missed even if unpushed.
- [x] **Push Ingestion Endpoint (`region/sync/routes_sync.py` & `region/sync/ingest.py`):**
  - Added `POST /internal/v1/sync/push` with `peer_guard` (authenticating `X-Peer-Token`, `X-Region`, and 503 check on link down).
  - Enhanced `ingest.py` to return `stored`, `duplicate`, `rejected`, and `rejected_list` with quarantine reasons (`bad_shape`, `bad_hash`, `id_reused`, `bad_sig`).
- [x] **Partition Lab & Traffic Shaping (`region/sync/links.py` & `region/sync/routes_admin.py`):**
  - Implemented `"slow"` mode alongside `"up"` and `"down"`.
  - Added async `TokenBucket` rate limiter (`rate_bps`) and simulated latency (`delay_ms`).
  - Updated `POST /admin/links/{peer}` to accept `{mode: "up"|"down"|"slow", delay_ms, rate_bps, drop_pct}`.
- [x] **Fast Heal & Recovery (`region/sync/client.py`):**
  - On transition from failed/isolated to healthy, immediately triggers unacked push rebuilding and full sync.
- [x] **Multi-Region Relay Support:**
  - Pull and push support relaying events from any held origin so third regions (Delhi) catch up from Pune alone.
- [x] **Trial & Reward Support & Scenarios:**
  - `scripts/loadgen.py`: synthetic workload generator measuring offered vs accepted rates and p50/p95 commit latencies.
  - `scripts/mock_partner.py`: mock Convergence partner service implementing `/trust/keys`, `/credentials/verify`, `/credentials/status-list`, and `/anchor` (ready for pairing).
  - `scripts/scenarios/`: `split_brain.py`, `restart.py`, `throttle.py`.
- [x] **Comprehensive Integration Tests (`tests/integration/test_lane_d.py`):**
  - `test_t8_sequence_gap_and_contiguous_watermark`: out-of-order gap delivery test.
  - `test_t1_tampered_and_duplicate_quarantine`: tamper and reused id quarantine test.
  - `test_t14_priority_queue_ordering`: P0 critical < P1 normal < P2 bulk queue test.
  - `test_t11_relay_catch_up_three_regions`: 3 real processes (mum -> pun -> del) multi-hop relay convergence.
  - `test_t15_slow_link_and_partition_both_ways`: slow link + bidirectional 503 partition + notes union heal.
  - `test_t14_priority_overtaking_under_throttling`: P0 critical report overtakes bulk notes backlog over a throttled link.

---

## 2. In Progress
- Verification and review of Lane D deliverables.
- Ready for integration checkpoints with Lane A (Akshit) and Lane B (Rahil).

---

## 3. Remaining Tasks
- [ ] Connect with Lane A when Ed25519 signatures are ready (`region/trust/signing.py`) to verify signature validation in `ingest.py`.
- [ ] Connect with Lane C (Miti) for any dashboard metrics display requirements (`/v1/metrics` / link matrix).
- [ ] Tag `pre-tr` when instructed before Trial & Reward round.

---

## 4. Verification Record
- **Commands Executed:**
  - `py -m pytest tests/engine` -> **13 passed** in 0.52s.
  - `py -m pytest tests/integration/test_two_regions.py` -> **4 passed** in 29.22s.
  - `py -m pytest tests/integration/test_lane_d.py` -> **6 passed** in 52.45s.
  - `py -m pytest` (full suite) -> **23 passed** in 50.86s.
  - `git status` -> on branch `d/simrit`, 0 commits made, working tree contains only Lane D changes uncommitted.

---

## 5. Design Decisions
- **Event Log as Authoritative Outbox:** Push queue is non-durable and best-effort. If push fails or the link drops, no event is ever lost because the local SQLite event log holds all committed events; pull anti-entropy repairs any missing events.
- **Priority-First Throttling:** On throttled links (`rate_bps`), the push queue prioritizes P0 critical events (`PushQueue` orders `(priority, hlc, event_id)`). This guarantees high-urgency updates (severity 5, collapses) overtake backlogs of P2 bulk notes.
- **Bidirectional Partition Guard:** `links` table in SQLite is checked both on outbound requests (skipping pull/push) and inbound requests (503 on `peer_guard`).
- **Gap-Free Watermark:** `origin_progress.contiguous` strictly advances only across contiguous sequences starting from 1. Any gap stalls `contiguous` while updating `max_seen`, causing future pull rounds to request `after=state.contiguous(origin)`.
- **Relay-Ready Replication:** `/internal/v1/sync/events` serves events for any requested origin stored in `events`, enabling a 3-region hub-and-spoke or daisy chain (Delhi pulling Mumbai events directly from Pune).

---

## 6. Integration Dependencies
- **Lane A (Akshit):**
  - Expected: Store functions (`tx()`, `read()`, `events_after()`, `insert_event_if_absent()`, `quarantine()`). All work smoothly.
  - Expected: When Lane A implements `trust.signing.verify()`, `ingest.py` will automatically invoke it if signatures are present on incoming events.
- **Lane B (Rahil):**
  - Expected: `canonical.event_hash()`, `canonical.dumps()`, and `derive.derive()`. All integrated and verified.
- **Lane C (Miti):**
  - Expected: Operator dashboard calls `/admin/links` and `/admin/links/{peer}` which now fully support `"up"`, `"down"`, and `"slow"` modes with `delay_ms` and `rate_bps`.

---

## 7. Resume Instructions
- **Branch:** `d/simrit`.
- **Run Full Test Suite:** `py -m pytest`
- **Run Lane D Specific Tests:** `py -m pytest tests/integration/test_lane_d.py -v`
- **Run Scenarios:**
  - `py scripts/scenarios/split_brain.py` (requires regions running via `py scripts/run_regions.py`)
  - `py scripts/scenarios/throttle.py`
  - `py scripts/mock_partner.py --port 8000`
