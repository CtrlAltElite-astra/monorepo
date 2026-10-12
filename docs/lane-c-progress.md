# Lane C progress log (Miti Shah)

Source of truth for the plan: *SyncShield Main Build Plan v1.1*, section 8 "Lane C" and section 7 (frontend).
This file is updated after every finished step, so if work is interrupted you can see exactly where it stopped.

Legend: DONE = built and verified as described | PARTIAL = built, part blocked or unverified | TODO = not started | BLOCKED = needs another lane

**Nothing below is committed or pushed.** Work is in the working tree on `main` (see "Files" at the bottom).

## Status board

| # | Plan item (v1.1) | Status | Verified how |
|---|---|---|---|
| C0 | Sync with `origin/main`; rename to SyncShield | DONE | Simrit's commit `5ed2c27` pulled; only my own old rethink PDF still says Lifeboat |
| C1 | `scripts/chaos_agent.py` on :9000 (crash / start / state, CORS, X-Admin-Key) | DONE | live: 401 without key, 404 unknown region, crash killed mum while pun stayed up, start brought it back, start idempotent, CORS only for localhost:5173 |
| C2 | Fixtures from Appendix B (carts, split-brain, close-vs-new-info, op_id retry) | DONE | `pytest tests/shop`: 7 pass + 1 expected failure |
| C3 | `docs/shop-mapping.md` | DONE | written from plan section 4 |
| C4 | README skeleton with marked sections | DONE | 13 owner-marked blocks; unbuilt items listed under Status, none claimed as done |
| C5 | Shop domain adapter (section 4) | DONE | `node --test`: 6 tests (retry counts once, totals, relabel-not-recompute, catalogue matches fixture) |
| C6 | Failover SDK: outbox, carry, op_id, failback (section 6.3) | DONE | `npm test`: 12 SDK tests on a fake cluster, and live against real nodes: carried 3 unreplicated events after a hard crash |
| C7 | Failover console: storefront, cart cards per node, node headers, network panel, request timeline, judge counters, outbox, hold-to-crash | DONE | headless Edge on live nodes, link cut first: 3 writes acknowledged by mum only, hold-crash mum, 2 more adds -> 0 errors, 8 acknowledged, 8 present on survivors (confirmed by `/v1/events/has`), 0 lost, 1 failover, carried 3 new / 0 dup; restart brought mum back HEALTHY, digests and state hash equal, 8/8 signatures verify |
| C8 | Review queue, resolve form, history drawer | PARTIAL | history drawer works against the real `/history` (shows signatures); review queue and resolve UI are built and degrade to "not available yet" because `/v1/review` and `/resolve` do not exist |
| C9 | Empty / error / unreachable / recovering states | PARTIAL | unreachable and empty verified in browser; RECOVERING level is styled but only appears when Simrit's levels land |
| C10 | Metrics panel; copy `fuzz-result.json` | PARTIAL | metrics panel renders any `/v1/metrics`; no `/v1/metrics` exists yet; fuzz copy BLOCKED on Rahil's F1 |
| C11 | Prove-it (random cuts + concurrent writes + heal, compare digests, check every acknowledged write) | DONE | `dashboard/src/sdk/prove.js` + 4 tests on a fake cluster; live: 3 partitions, 18 concurrent writes, identical digests and state, 0 missing, converged 0.4 s after the last heal; it also FAILS honestly on a stuck link, a missing ack, or a node that is down |
| C13 | Tamper (edit / forge / reuse) | DONE | `scripts/chaos_agent.py` `/tamper`, `ProofTools.jsx`; live: bad_hash, bad_sig, id_reused each quarantined, event count and state hash unchanged |
| C14 | T20 failover latency | DONE | `dashboard/scripts/measure-failover.mjs`, `docs/failover-latency.md`: crashed node p50 35 ms / p95 39 ms; hung node p50 619 ms / p95 631 ms (target 1.5 s) |
| C15 | Delhi as a third node (A10) | DONE (UI and live run) | rolling join: mum and pun up, Delhi started late and empty -> HEALTHY, same cart on all three, CONVERGED; header was UNKNOWN (never green) until Delhi was up |
| C16 | Architecture diagram + doc for v1.1 | DONE | `docs/architecture.svg/.png`, `docs/architecture.md` |
| C17 | Screenshots for the README | DONE | `docs/screenshots/*.png` (6), all from real nodes |
| C12 | Final README polish, backup screen recording | PARTIAL | README has the demo section and screenshots; a screen recording cannot be produced headlessly and still has to be recorded by a person |

## What a judge can do today (real backend, nothing mocked)

1. `python scripts/run_regions.py` and `python scripts/chaos_agent.py`, then `cd dashboard && npm run dev`.
2. Open the **Shop and failover** tab. Add items as different shoppers (or tick *auto-shoppers*).
3. Hold **Crash** on a node. The shopper keeps working; the judge counters stay at 0 errors and 0 lost; the dots on the timeline flip colour with a ring on the failover.
4. **Restart** the node and watch it return HEALTHY with matching heads.
5. Use **Cut / Slow / Heal** in the Network panel to partition and reconcile; conflicts appear on the cart cards.

## Honest limits of what is shown today

- **Review and resolve are still missing.** Conflicts are detected, explained and flagged `needs_review`, but a supervisor cannot resolve them yet: `GET /v1/review` and `POST /v1/reports/{id}/resolve` do not exist. The queue UI is built and says so.
- **Carry only helps while the browser is alive.** If the tab is closed before failover, an unreplicated event still dies with its node. This is the stated limit in the plan.
- **Dev keys are local.** `keys/` and `region/trust/public_keys.json` are generated per machine and git-ignored; a multi-machine demo needs the public registry copied across.
- **Peer replication stays token-authenticated.** Unsigned peer events are accepted (a present signature is always verified). Only the untrusted browser path (carry) requires a signature.
- **No role/auth_mode in the envelope yet** (plan 5.1 lists them); roles are enforced at the API by Akshit's gate.
- **Metrics, `risk`/`queues` in status, priority queue tuning** are still Simrit's / Rahil's; the UI shows them when present.

## Phase 2: closing the four "limits" (cross-lane, started on request)

These live in other lanes' files. Everything is additive and covered by tests; coordinate before merging.

| # | Limit | Work | Status |
|---|---|---|---|
| L1 | Carry off (no `/v1/carry`, no signing) | Ed25519 signing, key generation, `/v1/carry` (hash + signature checked, own-origin guard), `/healthz` | DONE: `tests/api/test_carry_and_idempotency.py` (14 tests) + live crash run |
| L2 | "Present on survivors" estimated | `POST /v1/events/has` | DONE: tested, and the UI no longer says "estimated" |
| L3 | Retried writes duplicate | `op_id` column + idempotent write path + op_id group rule in derive | DONE: replay returns 200, reuse is 409, retry after carry writes nothing new, group rule in `tests/engine/test_policy_v1.py` |
| L4 | Conflicts use the v0 rule | Policy v1 (highest speed, earliest centre + DOUBLE_DISPATCH, status ladder, CLOSE_VS_NEW_INFO) | DONE: 19 rule tests; the plan section 10.1 fixture passes |

Design choices (so they can be challenged):
- Carry requires a registered public key AND a valid signature, because a hash alone can be forged by any browser.
  Peer-to-peer replication keeps working unsigned (peers authenticate with X-Peer-Token); a signature, when present, is always verified.
- Private keys live in `keys/` (git-ignored); the public-key registry is generated locally for dev (`region/trust/public_keys.json`, git-ignored) because teammates generate different keys.
- Stored views are rebuilt at startup when their `policy_version` is stale, so nodes never mix v0 and v1 views.

## Still owned by other lanes

| Lane | Item | Note |
|---|---|---|
| A (Akshit) | `web/` generated UI; `/v1/review` and `/resolve`; roles in the envelope | Phase 2 work in A's files was done at Miti's request: write path, routes, store migration, `schemas.py`. Needs A's review |
| B (Rahil) | policy docs (`docs/conflict-policy.md`), F1 Hypothesis convergence test, `last_result.json` | `derive.py` and `signing.py` were implemented at Miti's request. Needs B's review |
| D (Simrit) | `risk`/`queues` in status, pidfiles in `run/`, metrics | `carry.py` is new; `run_one.py` now ensures keys. Needs D's review |

## Log (newest first)

- Phase 3 (Lane C leftovers): Prove-it, Tamper, T20 numbers, Delhi run, updated diagram and screenshots. Final counts: 203 Python tests (engine, API, shop) + 10 integration, 23 JS tests.
- Real-data bug found and fixed: one close with two unseen changes produced two identical CLOSE_VS_NEW_INFO items. It is now ONE item listing the close and every unseen change (`test_one_close_with_several_unseen_changes_is_one_review_item`).
- Real-data bug found and fixed: the console said "present on survivors is estimated ... no /v1/events/has" when nothing had been acknowledged yet. It now reports an empty, non-estimated proof.
- Prove-it's convergence time was first measured from after my own 300 ms pause (0.0 s). It is now measured from the real last heal (0.4 s).
- Note: changing how CLOSE_VS_NEW_INFO groups items is a rule change inside policy version 1. In a real rollout that would bump `POLICY_VERSION`; here every node is restarted together, so views stay consistent.

- Phase 2 verified: 212 Python tests and 18 JS tests pass; `tests/integration` (real processes) passes with signing on.
- Found and fixed my own hermeticity bug: dev keys generated by the run scripts leaked into tests; `tests/api/conftest.py` now points tests at an empty key dir by default.
- Existing tests that encoded v0 ("latest wins") were changed to policy v1 expectations on purpose: `test_engine.py` (3 assertions). `scripts/gen_fixtures.py` now emits the v1.1 envelope and `contracts/fixtures` were regenerated.
- A conflict list can now hold two entries for `status` (a status conflict and CLOSE_VS_NEW_INFO); UI keys use field + rule.

- C7 live run found and fixed two honesty issues: the network row showed "?" when one side was down (now reads the other side's view, shows "half cut" on disagreement), and the UI now states that carry is unsupported instead of showing 0/0 silently.
- `errorsShown` now counts only the case where no node answered (the one case a shopper can see a problem).
- JSON import in Node's test runner needs an import attribute; the catalogue is inlined in `shop.js` and a test fails if it drifts from `fixtures/carts.json`.
- Fixtures: derive v0 gives severity 4 / Chakan on the split-brain file; plan expects 5 / Bhiwandi. Policy v1 (Rahil) is not built yet.
- `run_regions.py` does not write pidfiles yet (Simrit), so the chaos agent finds pids by port.
- `grep` in this machine's Git Bash aborts on pipes; use Python for text checks.
- Created this file. Pulled `origin/main` at `5ed2c27`.

## Files (all uncommitted)

New:
`scripts/chaos_agent.py` · `scripts/gen_shop_fixtures.py` · `fixtures/*.json` (5) + `fixtures/README.md` ·
`tests/shop/test_shop_fixtures.py` · `docs/shop-mapping.md` · `docs/lane-c-progress.md` ·
`dashboard/src/sdk/syncshieldClient.js` + `.test.js` · `dashboard/src/domain/shop.js` + `.test.js` · `dashboard/src/ShopConsole.jsx`

Changed:
`README.md` (rewritten, SyncShield + marked sections) · `dashboard/package.json` (lowercase name, `npm test`) ·
`dashboard/src/App.jsx` (tabs) · `dashboard/src/api.js` (chaos calls, slow-link extras) ·
`dashboard/src/RegionColumn.jsx`, `components/Badge.jsx`, `components/LinkMatrix.jsx`, `styles.css` (new levels, slow mode, risk/queues, shop styles) · `.gitignore` (`run/`)

## Phase 3 files (uncommitted)

New: `dashboard/src/sdk/prove.js` + `prove.test.js` · `dashboard/src/components/ProofTools.jsx` ·
`dashboard/scripts/measure-failover.mjs` · `docs/failover-latency.md` · `docs/screenshots/*.png`

Changed: `scripts/chaos_agent.py` (`/tamper`) · `dashboard/src/api.js` · `dashboard/src/ShopConsole.jsx` ·
`dashboard/src/sdk/syncshieldClient.js` · `dashboard/src/styles.css` · `region/engine/derive.py` (one item per close) ·
`docs/architecture.svg/.png/.md` · `README.md`

## Phase 2 files (cross-lane, uncommitted)

New: `region/engine/signing.py` · `region/trust/signing.py` · `region/sync/carry.py` · `scripts/gen_keys.py` ·
`tests/engine/test_policy_v1.py` · `tests/api/test_carry_and_idempotency.py`

Changed: `region/engine/derive.py` (policy v1, op groups, CLOSE_VS_NEW_INFO) · `region/store.py` (op_id column + migration,
idempotency helpers, stale-view rebuild) · `region/api/writes.py` · `region/api/routes_reports.py` · `region/api/routes_status.py` ·
`region/main.py` · `contracts/schemas.py` · `contracts/contracts.md` · `contracts/CHANGELOG.md` · `contracts/fixtures/*` ·
`scripts/gen_fixtures.py` · `scripts/run_one.py` · `tests/engine/test_engine.py` · `tests/api/conftest.py` ·
`tests/shop/test_shop_fixtures.py` · `requirements.txt` (cryptography) · `.gitignore`

## How to re-verify

```bash
cd dashboard && npm test                      # 23 tests: SDK, shop adapter, prove-it
node dashboard/scripts/measure-failover.mjs   # T20 numbers (needs nodes + chaos agent running)
pytest                                        # 212 tests, including real-process integration
python scripts/gen_shop_fixtures.py           # regenerate fixtures
```
