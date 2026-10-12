# SyncShield — Trial & Reward Log (`docs/tr-log.md`)
**Team:** Akshit Sharma, Rahil, Miti, Simrit (Lane D Lead)  
**Branch:** `tr/` (branched from `pre-tr`)  
**Document Reference:** SyncShield Main Build Plan v1.1 Section 13  

---

## 1. Overview & Objectives
The Trial & Reward round simulates production anomalies and resource curveballs:
- **Write spike:** Sudden influx of concurrent writes requiring group commits, bounded write queues, and backpressure.
- **Add capacity:** Dynamic node join (Delhi :8003) via hot-reloaded `PEERS` to absorb regional load.
- **Bandwidth/cost cap:** Throttled links requiring priority queuing (P0 payments/checkout overtake P2 bulk notes).
- **Region crash:** Zero-loss failover via browser-carried signed events (`/v1/carry`) and survivor convergence.

---

## 2. Configuration Knobs Available
All knobs are managed via environment variables and hot-reloadable `/admin/config`:

| Knob | Default | Purpose in Curveball |
|---|---|---|
| `WRITE_BATCH_MS` | `0` (off) | Group commit writes per transaction to increase SQLite throughput |
| `WRITE_QUEUE_MAX` | `100` | Bounded write queue depth; returns 503 `Retry-After: 1` on overload |
| `PAGE_SIZE` | `200` | Maximum events per sync page to bound memory during catch-up |
| `LINK_RATE_BPS` | `0` (unlimited) | Token bucket rate limit per link for simulating constrained networks |
| `SYNC_INTERVAL_MS`| `1000` | Pull replication interval (decreased during active sync, increased when idle) |
| `PUSH_ENABLED` | `1` (on) | Push replication fast-path toggle |
| `ENABLE_ADMIN` | `1` (on) | Dynamic admin link and config mutation toggle |

---

## 3. Baseline Benchmarks (Unconstrained)
*Target: Mumbai (:8001)*  
- **Workload:** 100 shop operations (Cart creations + ADD notes + checkout) via `scripts/loadgen.py`
- **Offered:** 100
- **Accepted (2xx):** 100
- **Rejected:** 0
- **Commit Latency p50:** ~4.2 ms
- **Commit Latency p95:** ~12.8 ms
- **Throughput:** ~95 req/s (single worker SQLite WAL)

---

## 4. Curveball Scenarios & Interventions

### Curveball A: Write Spike & Queue Overload
- **Symptom:** Burst of 300+ writes within 1 second.
- **Intervention:**
  1. Configure `WRITE_QUEUE_MAX=200` to prevent unbounded memory growth.
  2. Set `WRITE_BATCH_MS=10` to group writes into single SQLite transactions.
  3. Load shed excess requests with `503 Retry-After: 1`, allowing browser SDK backoff.
- **Result:**
  - Offered: 300
  - Accepted: 200
  - Rejected (503): 100
  - Data integrity: 0 corrupted events, sequences completely contiguous.

### Curveball B: Add Node Capacity (Delhi Join)
- **Symptom:** Need to scale geographic capacity without restarting running clusters.
- **Intervention:**
  1. Run `python scripts/join_region.py del`.
  2. Mumbai (:8001) and Pune (:8002) receive `POST /admin/config` with `add_peer: del`.
  3. Delhi starts empty and immediately pulls all origins from peers.
- **Result:**
  - Delhi catches up within 1.5 seconds.
  - Event digests across `mum`, `pun`, and `del` are identical (`CONVERGED`).

### Curveball C: Throttled Link & Priority Overtaking
- **Symptom:** Network throttled to 500 Bps; massive queue of P2 cart notes.
- **Intervention:**
  1. Push queue classifies events: P0 (checkout/urgent) vs P2 (bulk notes).
  2. High-priority P0 events jump ahead in `PushQueue` priority queue.
- **Result:**
  - P0 events cross within <300 ms.
  - Bulk notes are deferred but never dropped.

---

## 5. Verification Commands
```powershell
# Run load generator
python scripts/loadgen.py --url http://127.0.0.1:8001 -n 100

# Join Delhi node
python scripts/join_region.py del

# Verify status across all three nodes
curl http://127.0.0.1:8001/v1/status
curl http://127.0.0.1:8002/v1/status
curl http://127.0.0.1:8003/v1/status
```
