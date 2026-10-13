# SyncShield Lane D Handoff & Progress Record — Simrit

> **Superseded for the scaling work and for current numbers:** see `docs/scale/simrit.md`, `docs/scale/join.md`, `docs/tr-log.md` and the README. The test counts and benchmark figures below are from 12 October and are out of date.
**Role:** Replication, Carry, Runtime, Partition Lab, Recovery, Trial & Reward Lead  
**Branch:** `d/simrit`  
**Document Source of Truth:** SyncShield Main Build Plan v1.1 (12 Oct 2026)  
**Last Updated:** 2026-10-12 19:42 IST  
**Status:** All Lane D deliverables fully implemented & 100% verified (167 / 167 tests passing)  

---

## 1. Lane D Scope & Ownership Overview
- **Owned Modules & Paths:**
  - `region/sync/` (`carry.py`, `status.py`, `push.py`, `ingest.py`, `state.py`, `links.py`, `client.py`, `routes_sync.py`, `routes_admin.py`)
  - `region/config.py`
  - `.env.example`
  - `scripts/run_*` (`run_one.py`, `run_one.sh`, `run_regions.py`, `run_regions.sh`)
  - `scripts/join_region.*` (`join_region.py`, `join_region.sh`)
  - `scripts/loadgen.py`
  - `docs/tr-log.md`
  - `tests/integration/` (`test_lane_d.py`, etc.)
  - `.github/workflows/` (`ci.yml`)

---

## 2. Checkpoints Status & Summary of Changes

### Checkpoint 1 (18:30) — Runtime & Base Health — [COMPLETED]
- [x] **`GET /healthz`**: Implemented in `region/sync/carry.py` and mounted in `region/main.py`. Role `none`, zero auth, zero DB writes; returns `{"region": region, "ok": True, "level": current_level, "ts": epoch_ms}`.
- [x] **PID Files**: Updated `scripts/run_one.py` and `scripts/run_one.sh` to write `run/<region>.pid` on startup so Lane C's `chaos_agent.py` can target and terminate nodes.
- [x] **Empty DB Guard**: Implemented `check_empty_db_guard()` in `scripts/run_one.py` (Section 6.10). Prevents restarting with a wiped/empty database under an already-known region ID if peer databases have already recorded events for this origin, protecting sequence monotonicity.
- [x] **Ingest Validation**: Verified `region/sync/ingest.py` hooks for shape, hash, unknown origin, and Ed25519 signature checks with quarantine reasons (`bad_shape`, `bad_hash`, `bad_sig`, `id_reused`).

### Checkpoint 2 (19:15) — Failover Carry & Has — [COMPLETED]
- [x] **`POST /v1/carry`**: Implemented in `region/sync/carry.py`. Accepts up to 200 client-carried signed events (body <= 512 kB), checks shape, origin, hash, and Ed25519 signature, ingests idempotently into SQLite, and returns `{"results": [{"event_id": id, "result": "new|dup|rejected", "reason": ...}]}`.
- [x] **`POST /v1/events/has`**: Implemented in `region/sync/carry.py`. Accepts up to 500 event IDs and returns `{"missing": [...]}`.
- [x] **Carry Metrics Tracking**: In-memory counters tracking `{"received": 0, "new": 0, "dup": 0, "rejected": 0}`, exported directly to `/v1/status`.
- [x] **Carry Integrity & Verification**: Covered by `test_healthz_and_events_has_and_carry` and `test_t18_carry_integrity` in `test_lane_d.py`.

### Checkpoint 3 (20:15) — Degradation Ladder & Status v1.1 — [COMPLETED]
- [x] **Degradation Ladder**: Implemented ladder level transitions in `region/sync/state.py`:
  - `HEALTHY`: All peers reachable, lag <= 2s.
  - `CONSTRAINED`: Throttled/slow link (`LINK_SLOW_BPS`).
  - `ISOLATED`: Peer silent > 5s or >= 3 failures.
  - `RECOVERING`: Reachable after partition/restart and heads differ.
  - `OVERLOADED`: Write queue at `WRITE_QUEUE_MAX`.
- [x] **Data-at-Risk Meter**: Built `compute_risk()` in `region/sync/state.py` calculating:
  - `unreplicated`: Unacknowledged events originating at this node that no peer has confirmed holding.
  - `critical_unreplicated`: Subset of unreplicated events with `priority == 0` (P0 critical).
  - `oldest_age_ms`: Elapsed time since creation of the oldest unreplicated event.
- [x] **Priority Queue Tracking**: Updated `region/sync/push.py` to maintain counts of P0, P1, and P2 events.
- [x] **`/v1/status` v1.1 Snapshot**: Created `region/sync/status.py` providing complete v1.1 status snapshot and mounted it in `region/api/routes_status.py`.

### Checkpoint 4 (22:15) — Delhi Join & Hot Reload — [COMPLETED]
- [x] **`scripts/join_region.py` & `.sh`**: Automatically notifies existing cluster members (`mum`, `pun`) to add `del` to their peers, starts Delhi with an empty database on port 8003, and pulls from peers until converged.
- [x] **PEERS Hot Reload**: Added `add_peer()` in `region/config.py` and `POST /admin/config` in `region/sync/routes_admin.py`.
- [x] **Relay Convergence**: Verified by `test_t11_relay_catch_up_three_regions`.

### Checkpoint 5 (22:30) — Trial & Reward Lead — [COMPLETED]
- [x] **Shop Payload Generator (`scripts/loadgen.py`)**: Enhanced with Appendix B synthetic shoppers (Alice, Bob, Chitra, Dev, Esha) and products (Kolhapuri sandals, Filter coffee, etc.), cart operations, and p50/p95 commit latency tracking.
- [x] **Trial & Reward Log (`docs/tr-log.md`)**: Full documentation of baseline benchmarks, configuration knobs, and curveball scenarios (write spikes, node join, link throttling, crash recovery).

### Checkpoint 6 (00:30) — Workflows & CI — [COMPLETED]
- [x] **GitHub Actions Workflow (`.github/workflows/ci.yml`)**: Pytest backend suite + Vite frontend build CI pipeline.
- [x] **Environment Documentation (`.env.example`)**: Added all v1.1 knobs (`ADMIN_KEY`, `LINK_RATE_BPS`, `LINK_SLOW_BPS`, `WRITE_BATCH_MS`, `WRITE_QUEUE_MAX`, `ENABLE_CARRY`, `CHAOS_PORT`).

---

## 3. Verification Summary
- Full repository test pass: **167 passed out of 167** in `tests/`
- Lane D integration suite: **11 passed out of 11** in `tests/integration/test_lane_d.py`
  - `test_t8_sequence_gap_and_contiguous_watermark`
  - `test_t1_tampered_and_duplicate_quarantine`
  - `test_t14_priority_queue_ordering`
  - `test_t11_relay_catch_up_three_regions`
  - `test_t15_slow_link_and_partition_both_ways`
  - `test_t14_priority_overtaking_under_throttling`
  - `test_healthz_and_events_has_and_carry`
  - `test_t18_carry_integrity`
  - `test_t21_ladder_transitions`
  - `test_t22_data_at_risk`
  - `test_empty_db_guard_refuses_sequence_reuse`

---

## 4. Manual Git Commands for User
Run these commands in PowerShell or Bash from the monorepo root:

```bash
git add .
git commit -m "feat(lane-d): complete Main Build Plan v1.1 deliverables for Simrit"
git push origin d/simrit
```
