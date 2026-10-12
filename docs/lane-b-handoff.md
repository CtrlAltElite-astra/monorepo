# Lane B (Rahil): handoff and cross-lane tracker

Purpose: what Lane B has finished, and what Lane B is waiting on from the other lanes. **Update the
tracker whenever an item moves**: change its status, add the commit, and note how it was verified.

Last updated: 2026-10-13, after merging `lane-b-rahil` into `main`.

## 1. Lane B status (plan section 8, Lane B)

| plan slot | task | state | evidence |
|---|---|---|---|
| 18:30 | signing, key generation, tests | done (main's `signing.py` API + Lane B's pure helpers) | `tests/engine/test_signing.py`, `tests/api/test_signing_registry_race.py` |
| 19:00 | op_id group rule, notes collapse, retry fixture | done | `tests/engine/test_op_id.py`, `tests/engine/test_policy_v1.py`, `fixtures/events_op_id_retry.json` |
| 19:30 | T17 harness, T18 cases | done, and T17 passes on real processes (kill -9) | `tests/failover/`, `tests/engine/test_admit.py` |
| 20:15 | policy v1, derive v1, `priority.classify`, digests | done (main's `derive.py` + Lane B fixes); the write path now stores `priority.classify` | `test_policy_v1.py`, `test_priority.py`, `test_gates.py` (T1, T2, T5) |
| 22:00 | CLOSE_VS_NEW_INFO, receipts, `docs/conflict-policy.md` | done | `test_policy_v1.py`, `test_gates.py` (T4, T6) |
| 22:30 | Trial and Reward: protect invariants | on the day, on `tr/` | run the invariant set in section 4 before and after every change |
| 00:30 | F1 fuzz, study sheet | done (211 examples on the merged engine) | `tests/fuzz/last_result.json`, `docs/study-sheet-engine.md` |
| extra | U2 convergence badge + `scripts/prove_it.py` | done | `tests/engine/test_convergence_badge.py`; ran against 2 real nodes with `--chaos 3` |

Done-when check: every rule has a unit test (yes); README explains every rule (yes, `policy` section);
F1 passes (yes); T17 passes on real processes (yes, 4 of 4 runs).

### What the merge into `main` changed in Lane B's files

- `derive.py`: kept main's version (Miti's, used by every lane). Fixed what plan 6.6 requires: two
  supervisor decisions that differ are always reviewed (`RESOLVE_VS_RESOLVE`); a malformed event is
  ignored instead of raising; a CLOSE_VS_NEW_INFO item reports the status every node shows (the F1 fuzz
  test found it could say "closed" while every screen showed "open").
- `signing.py`: kept main's API (`sign(region, hash)`, `has_key`, `ensure_keys`). Added the pure helpers
  (`generate`, `public_of`, `sign_with`, `verify_with`). Fixed a Windows race: creating the registry lock
  while another process deletes it raises `PermissionError`, which now retries like `FileExistsError`.
- `policy.py` removed (its rules live in `derive.py`); `observed_for` moved to `envelope.py` and follows
  `writes._observed`; `priority.py` holds its own constants.
- Priority: `writes._priority` calls `engine.priority.classify`; `push.classify_priority` sends by the
  priority stored on the event.

## 2. Tracker: what Lane B needs from other lanes

Status values: `open`, `done (commit or where, how verified)`.

| id | owner | what | status |
|---|---|---|---|
| A1 | Akshit | Write path fills signature, `observed` and priority (plan 6.1) | done on main (`writes._append`); priority now from `engine.priority.classify` (merge) |
| A2 | Akshit | Client `report_id` and `op_id`, signed event in 2xx bodies, idempotent replay, 409 `op_id_reuse` | done on main; verified by `tests/failover/test_t17_live.py` |
| A3 | Akshit | Accept category `cart` | done on main; verified by the T17 live probe |
| A4 | Akshit | `tests/api/test_crash_recovery.py` failed intermittently with `disk I/O error` on this laptop | open: passed in every full run on the merged tree (4 runs); watch it |
| A5 | Akshit | `docs/lane-a-handoff.md` lines 165 and 168 name the old co-author trailers on commit `8a07981` and on `origin/lane-B-main-build` | open: team decision; `lane-b-rahil` carries none |
| D1 | Simrit | Peer ingest checks signatures | done on main (`468f5db`, fails closed) |
| D2 | Simrit | `/healthz`, `/v1/carry`, `/v1/events/has` with the checks of plan 6.2 | done on main (`region/sync/carry.py`, same order as `admit.reject_reason`); verified by T17 live |
| D3 | Simrit | Push queue sends by the stored priority | done in the merge (`push.classify_priority`) |
| D4 | Simrit | `/v1/status` peers `acked` and `risk` | done on main (`region/sync/state.py`) |
| D5 | Simrit | Keys exist on first start | done on main (`main.py` lifespan calls `signing.ensure_keys`) |
| D6 | Simrit | `test_t21_ladder_transitions` is flaky (failed 2 of 8 on main alone, 3 of 8 merged): right after start a node reports a peer it has not reached yet as ISOLATED (`state.py`: `last_ok is None` counts as silent). Plan 6.8: ISOLATED after 3 failures or 5 s of silence | open: full write-up and fix options in `fault.md` |
| C1 | Miti | `dashboard/src/App.jsx` `convergence()` shows red DIVERGED whenever digests differ, also during normal replication. Rule: `region/engine/convergence.py` (SYNCING vs DIVERGED) | open |
| C2 | Miti | Copy `tests/fuzz/last_result.json` to `web/public/fuzz-result.json` (MetricsPanel reads it; file missing) | open |
| C3 | Miti | `web/src/domain/shop.ts` `RULE_TEXT` explains `SEVERITY_HIGHEST`, `TEAM_EARLIEST`, `PEOPLE_HIGHEST`, `LOCATION_LATEST`, but the engine emits `HIGHEST`, `EARLIEST_HLC`, `LATEST_HLC`; `FLAG_TEXT` has `PINS_FAR_APART` but the engine emits `FAR_APART`, and has no text for `RESOLVE_VS_RESOLVE`. `ReviewPanel.tsx` looks for `relationship === 'CLOSE_VS_NEW_INFO'`, but the engine marks it with `rule` (relationship stays `CONCURRENT`) | open: the receipt shape is in `docs/conflict-policy.md` section 6 |

## 3. How other lanes use the engine

```python
from region.engine import admit, convergence, derive, digest, priority, risk, signing

signing.sign(region, ev["hash"])                     # write path: this node's signature (None without a key)
signing.verify(origin, ev["hash"], ev["sig"])        # ingest and carry
priority.classify(ev, view_before)                   # write path: lane stored on the event
admit.reject_reason(ev, public_keys)                 # the carry rule as a pure function (tests, in-memory nodes)
risk.data_at_risk(own_events, peer_heads, now_ms)    # status risk block
convergence.badge({region: status_or_None, ...})     # global badge (scripts/prove_it.py)
```

## 4. Invariants to re-run (Trial and Reward, before any merge to main)

```bash
pytest tests/engine tests/failover tests/fuzz tests/shop -q   # T1 T2 T4 T5 T6 T16 T17 T18 F1, every policy rule
python scripts/prove_it.py --chaos 3 --wait 20                # live: cut / write / heal, must end CONVERGED (exit 0)
```

## 5. Progress log

- 2026-10-12 20:03: F1 committed. Lane B stopped here for the demo.
- 2026-10-12: carry admission rule, T17 harness, T18, README rules, study sheet; `envelope.new_event`,
  convergence badge, `scripts/prove_it.py`, this tracker. Pushed as `lane-b-rahil` (tip `30dd242`).
- 2026-10-13: `lane-b-rahil` merged into `main` (section 1). Full suite on the merged tree: 435 passed,
  18 skipped (need Node 24 for the partner crypto); one run of two had the known T21 flake (D6).
