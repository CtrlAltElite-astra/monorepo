# Lane B (Rahil): handoff and cross-lane tracker

Purpose: what Lane B has finished, and what Lane B is waiting on from the other lanes. **Update the
tracker whenever an item moves**: change its status, add the commit, and note how it was verified.

Last updated: 2026-10-12, branch `lane-B-main-build` (not pushed).

## 1. Lane B status (plan section 8, Lane B)

| plan slot | task | state | evidence |
|---|---|---|---|
| 18:30 | `signing.py`, `scripts/gen_keys.py`, tests | done | `tests/engine/test_signing.py` |
| 19:00 | op_id group rule, notes collapse, retry fixture | done | `tests/engine/test_op_id.py`, `fixtures/events_op_id_retry.json` |
| 19:30 | T17 harness, T18 cases | done (in memory); live run waits on A2, A3, D2 | `tests/failover/`, `tests/engine/test_admit.py` |
| 20:15 | policy v1, derive v1, `priority.classify`, digests | done | `test_policy.py`, `test_priority.py`, `test_gates.py` (T1, T2, T5) |
| 22:00 | CLOSE_VS_NEW_INFO, receipts, `docs/conflict-policy.md` | done | `test_close_vs_new_info.py`, `test_gates.py` (T4, T6) |
| 22:30 | Trial and Reward: protect invariants | on the day, on `tr/` | run the invariant set in section 4 before and after every change |
| 00:30 | F1 fuzz, study sheet | done | `tests/fuzz/last_result.json`, `docs/study-sheet-engine.md` |
| extra | `envelope.new_event`: one builder for every local write | done | `tests/engine/test_envelope.py` |
| extra | U2 convergence badge + `scripts/prove_it.py` | done | `tests/engine/test_convergence_badge.py`; ran against 2 real nodes with `--chaos 3` |

Done-when check: every rule has a unit test (yes); README explains every rule in one line (yes);
F1 passes (yes); **T17 passes on real processes: not yet** (it skips until A2, A3 and D2 land).

## 2. Tracker: what Lane B needs from other lanes

Status values: `open`, `in progress`, `done (commit, how verified)`.

| id | owner | what | why it matters | how to verify | status |
|---|---|---|---|---|---|
| A1 | Akshit | Write path v2 builds every event with `region.engine.envelope.new_event(...)` (pass the view before the write, `op_id`, `actor`, `role`, `wall`) instead of building the dict in `region/api/writes.py` | Without it, real nodes write unsigned events, no `observed` (so CLOSE_VS_NEW_INFO never fires live), and priority is always 1 (so the P0 lane and data-at-risk "critical" are always 0) | `GET /v1/reports/{id}/history` shows `sig_status: verified`; a closed status event has `observed`; a paid event has `priority: 0` | open |
| A2 | Akshit | `POST /v1/reports` accepts a client `report_id` and `op_id`; every write stores `op_id`; 2xx bodies return `{"report", "event"}`; the same op_id at the same node returns 200 with `Idempotent-Replay: true`; the same op_id with a different body returns 409 `op_id_reuse` (contract 5.2, plan 6.1) | The SDK retries on another node with the same op_id and keeps the returned signed event in its outbox. Without it, failover can double items and nothing can be carried | `pytest tests/failover/test_t17_live.py -rs` no longer lists A items in its skip reason; T16 API half | open |
| A3 | Akshit | Accept category `cart` (additive contract change; today `POST /v1/reports` answers 422) | The shop skin always sends `category: "cart"` (plan section 4) | T17 live probe stops reporting 422 | open |
| A4 | Akshit | `tests/api/test_crash_recovery.py` fails intermittently with `sqlite3.OperationalError: disk I/O error` in `read_db()` (the test's own read of the killed process's WAL file on Windows). Seen on the pre-change commit `4d964b2` (1 pass, 2 fails), so it is not caused by Lane B changes. Likely the read opens the DB while the killed process's WAL/shm are still locked; retry the open a few times or `PRAGMA journal_mode` read-only with a short wait | A flaky gate test hides a real regression (T9 is a G-C gate) | 10 consecutive runs of the test pass | open |
| D1 | Simrit | `region/sync/ingest.py` imports `region.trust.signing`, which does not exist (signing is `region.engine.signing`). The `ImportError` is swallowed, so **peer ingest never checks signatures**. Fix: `reason = admit.reject_reason(ev, unsigned_ok=True)` and quarantine with that reason | E3 says "verified on ingest". Today a forged signed event arriving by pull or push is stored | Ingest of an event re-signed with another node's key is quarantined as `bad_sig` | open |
| D2 | Simrit | `GET /healthz`, `POST /v1/carry`, `POST /v1/events/has` (contract 5.3). Carry must use `admit.reject_reason(ev)` with **no** `unsigned_ok` and answer `new` / `dup` / `rejected` + reason. Reference behaviour: `Node._carry_one` in `tests/failover/test_t17_in_memory.py` | This is the zero-loss failover path (U4, gate G-A) | `pytest tests/failover/test_t17_live.py -rs` runs instead of skipping, and passes | open |
| D3 | Simrit | `region/sync/push.py` `classify_priority` re-derives priority with old rules. It should send by the `priority` stored on the event (computed by `engine.priority.classify` at write time). Differences today: (1) it still makes categories `collapse`/`medical`/`fire` critical (v1.1 dropped that); (2) any severity of 4+ is P0 even when it was not raised; (3) an event stored as P1 is re-classified | A node and its push queue would disagree about which events are critical; T14 and the data-at-risk meter would measure a different rule from the one documented | `push.classify_priority(ev) == ev["priority"]` for every event written by A1 | open |
| D4 | Simrit | `/v1/status` peers block: contract 5.4 names the peer's acknowledged heads `acked`; the builder reports them as `their_heads`. Add `acked` (keep `their_heads` if the dashboard uses it). Fill `risk` with `engine.risk.data_at_risk` | The SDK drops outbox events by `peers[p].acked` (plan 6.3). The T17 harness accepts both names; the web SDK may not | `GET /v1/status` has `peers.<p>.acked` and a non-null `risk` | open |
| D5 | Simrit | Run scripts generate keys when `keys/` is missing (`python scripts/gen_keys.py`) | Without a key file a node writes unsigned events, and carry rejects them as `bad_sig` | A fresh clone started with one command writes signed events | open |
| C1 | Miti | Dashboard `convergence()` in `dashboard/src/App.jsx` shows red DIVERGED whenever digests differ, so it flashes red during normal replication. Use the rule in `region/engine/convergence.py`: different event sets = SYNCING; DIVERGED only for the same event digest with a different state hash. Apply the same rule in `web/` | U2 "never greener than the facts" must also never be redder: a false bug alarm on stage is as bad as a false green | During a heal the badge goes SYNCING then CONVERGED, never DIVERGED | open |
| C2 | Miti | Copy `tests/fuzz/last_result.json` to `web/public/fuzz-result.json` with a script (never hand-edited). The last run is 216 examples | The fuzz card must show the real run count | The card shows 216 (or the newer real count) | open |

## 3. How other lanes use the engine

```python
from region.engine import admit, envelope, convergence, priority, risk, digest

ev = envelope.new_event(region, seq, hlc_stamp, type_, report_id, field, value, view_before,
                        op_id=op_id, actor=actor, role=role, wall=wall)   # write path (A1)
reason = admit.reject_reason(ev)                    # /v1/carry (D2): None or shape|unknown_origin|bad_hash|bad_sig
reason = admit.reject_reason(ev, unsigned_ok=True)  # peer ingest while some node is unsigned (D1)
risk.data_at_risk(own_events, peer_heads, now_ms)   # status risk block (D4)
convergence.badge({region: status_or_None, ...})    # global badge (C1, scripts/prove_it.py)
```

## 4. Invariants to re-run (Trial and Reward, before any merge to main)

```bash
pytest tests/engine tests/failover tests/fuzz -q          # T1 T2 T4 T5 T6 T16 T17 T18 F1 and every policy rule
python scripts/prove_it.py --chaos 3 --wait 20            # live: cut / write / heal, must end CONVERGED (exit 0)
```

## 5. Progress log

- 2026-10-12 20:03: F1 committed (`4d964b2`). Lane B stopped here for the demo.
- 2026-10-12: carry admission rule, T17 harness (in memory, live skips), T18, README rules, study sheet (`ccd922d`, `65d9c1c`).
- 2026-10-12: `envelope.new_event`, convergence badge, `scripts/prove_it.py`, this tracker.
