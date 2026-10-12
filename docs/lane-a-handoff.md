# Lane A (Akshit) — handoff and progress log (SyncShield main build v1.1)

Purpose: let any engineer or coding agent continue Lane A from exactly where it stopped, with no other
context. Read this file first, then `SyncShield_Main_Build_Plan_v1.1.pdf` (the single source of truth for the
main build; PDF 1 "Basic Build" still applies, read "Lifeboat" there as "SyncShield").
**Update the status board and the progress log after every milestone.**

Last updated: see the newest entry of the progress log at the bottom.

---

## 1. Project in one paragraph

**SyncShield** (CodeAstra 2.0, RGIT, PS2). A multi-region data layer: each node (Mumbai :8001, Pune :8002, Delhi
:8003) is an independent FastAPI + SQLite service that always accepts writes. Every change is an immutable, signed
event; a node's visible data is `derive(event_set)`; replication copies missing events (pull by per-origin gap-free
watermark plus a best-effort push). When a node dies the browser SDK fails over to the next node with the same
`op_id` and **carries** the signed events it holds, so no acknowledged write is lost. AP + strong eventual
consistency, no consensus. Demo domain: an online shop (carts); the backend stays domain neutral.

## 2. Lanes and ownership

| lane | person | owns |
|---|---|---|
| **A** | **Akshit** | `contracts/`, `migrations/`, `region/main.py`, `store.py`, `auth.py`, `region/api/`, `region/trust/`, `requirements.txt`, `tests/api/`, and (this build) the generated `web/` until hand-off |
| B | Rahil | `region/engine/` (derive, policy, priority, digests, signing), `tests/engine/`, `tests/fuzz/`, `docs/conflict-policy.md` |
| C | Miti | `web/` after hand-off, `scripts/chaos_agent.py`, `fixtures/`, `docs/`, README structure |
| D | Simrit | `region/sync/` (pull, push, ingest, carry, status), `region/config.py`, `.env.example`, `scripts/run_*`, `tests/integration/` |

Rules: branches `a/…`; never edit another lane's files (ask the owner; put the request in section 7);
`contracts/*` and `migrations/*` are additive only, with a CHANGELOG line; no force-push; one Uvicorn worker per
region; a write never calls a peer; no code copied from reference repositories.

**Commit identity rule (hard requirement from the user):** every commit is authored by
`Akshit Sharma <akshitsharma24@spit.ac.in>` (already in git config). No co-author trailers or auto-generated
attribution lines, and no tool names, in commit messages, PR text, code comments or docs.

## 3. Branch and repo state

- Remote `https://github.com/CtrlAltElite-astra/monorepo`, default branch `main`.
- **Lane A branch: `a/akshit-main`** (kept). It was fast-forwarded to `main` after the final merge, so it equals `main` (`121b1ec`).
  The earlier Lane A branches `a/lane-a-basic-build`, `a/phase1-roles-history` and `a/post-merge-notes` are deleted (remote and local);
  everything in them was already on `main`. Simrit's `d/simrit` is merged into `main` (her branch is left in place).
- There is an **untracked folder `frontend 2/`** in the repo root (someone's earlier generated incident-report dashboard,
  with `node_modules` inside). It is not part of this build: never `git add -A`; stage files by name.

## 4. Status board against the plan (Lane A rows)

Legend: [x] done and tested · [~] partly · [ ] not started · [D]/[B]/[C] waiting on another lane

### Backend
- [x] Contract v1.1 (`contracts/contracts.md`, `schemas.py`, `CHANGELOG.md`, status fixture) and migration `002_op_id.sql`
- [x] Migration runner in `store.py` (001 idempotent each start; later files once, in a transaction, recorded in `schema_migrations`)
- [x] Signing: Miti's `region/engine/signing.py` is the single implementation (my earlier `trust/keys.py` was dropped in the merge);
      each region provisions its own key at start, and the shared registry update is now locked + atomic (fixed a 3-regions-at-once race)
- [x] Write path v2: `op_id` idempotency (also for events that arrived by replication/carry), client `report_id`,
      signed event in every 2xx body, `observed` plumbing, roles + `auth_mode`, push hand-off after commit
- [x] `POST /v1/reports/{id}/resolve` (supervisor, 409 guards) and `GET /v1/review` with full receipts
- [x] `GET /v1/reports/{id}/history` with `sig_status` and `carried`
- [x] `/v1/status` v1.1 served by Simrit's `region/sync/status.py` builder (peers `acked`/`last_ok_ms`, `risk`, `queues` P0/P1/P2, `carry`); my duplicate risk code was removed in the merge
- [x] CORS explicit origin list, body-size bound, startup key provisioning
- [x] Tests T7, T10, T16 (A part), A9, 403/409/422 paths: `tests/api/` (see section 5)
- [ ] Partner HTTP adapter with cache and fallback + T13 (tier 2-c; interface, contract and mock already exist)
- [ ] README API samples (`docs/api.md`)
### Frontend (`web/`) — built, light + dark themes, animated
- [x] Vite + React + TypeScript project, story page `/` (animated mesh, claims, 8-step failover walkthrough, four ideas, rules, limits)
- [x] Console `/demo`: storefront, 3 node cards with the degradation ladder, request dots, 4 counters, data-at-risk meter,
      tabs Carts / Network / Review / Outbox / Security / Metrics / API / Prove it, history drawer, resolve dialog, heal banner, toasts
- [x] Browser SDK (probes, same-op_id retries, carry before first request on a new node, outbox pruning, failback, device queue)
- [x] Mock mode = in-browser simulator of 3 nodes behind the SAME REST paths as the real nodes; live mode = fetch (`?mode=live`)
- [x] Tests: 57 (derive policy v1, op_id groups, close-vs-new-info, convergence, tamper, ladder, zero-loss failover T17/T20, probe-first failover, outbox T19)
- [x] Verified in a real browser (mock): A1 add + replicate, A2 kill a node under auto-shoppers = 0 errors and "acknowledged = present on survivors",
      A3 restart -> RECOVERING -> HEALTHY -> CONVERGED, UNKNOWN (never green) while a node is down, partition button
- [x] Verified in a real browser (LIVE, real nodes :8001-8003, chaos agent :9000): Prove it, the review dialog on REAL receipts (403 for an officer, 201 for a supervisor, queue empties, digests equal), tamper (3 forged events rejected by the real node), and a real `kill -9` of the serving node under auto-shoppers: 0 errors, `acknowledged = present on survivors` 10/10, restart, RECOVERING -> HEALTHY -> CONVERGED. Light and dark.
- [x] Exercised in a browser (mock, light + dark): Prove it, resolve dialog (403 then success), Burst 300, Tamper, join Delhi, every tab, history drawer; phone width 390 px has no horizontal scroll and the dialog fits. Still unseen in a browser: the 409 `stale_edit` toast (unit-tested only).
- [~] Hand-off to Miti: `scripts/run_web.{sh,ps1}`, `web/.env.example`, `web/README.md` are done. Left: Miti replaces `dashboard/` in the README `setup` block (owner C) with `web/` (text is in `web/README.md`); delete `dashboard/` only as a SEPARATE final commit, and only if the team agrees (plan B: keep it). CI already builds `web/`.
### Waiting on other lanes
- Now ON `main` (Lane C's merge): `engine/signing.py`, policy v1 + op_id rule + CLOSE_VS_NEW_INFO in `derive`, `sync/carry.py` (`/healthz`, `/v1/carry`, `/v1/events/has`), `scripts/chaos_agent.py`, fixtures.
- [B] `priority.classify` (I reuse Lane D's `classify_priority` through one call in `writes._priority`)
- [D] ingest that fails closed on unsigned events (strict xfail test flags it). Levels CONSTRAINED/RECOVERING and queues in the snapshot are now on `main` (Simrit's merge)

## 5. How to run and test

```powershell
cd C:\Users\dell\Desktop\Codeastra\monorepo
python -m venv .venv                                          # once
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest tests -q                 # whole repo
.\.venv\Scripts\python.exe -m pytest tests/api -q             # Lane A only (in-process, ~15 s)
.\.venv\Scripts\python.exe scripts/run_regions.py             # mum :8001 + pun :8002; each region creates its own key on first start
.\.venv\Scripts\python.exe scripts/gen_fixtures.py            # regenerate contracts/fixtures/*.json
```

Test status at the last update: see the progress log. Lane A files: `test_reports_api.py`, `test_write_path_v2.py`,
`test_trust_and_infra.py`, `test_status_api.py`, `test_store.py`, `test_roles.py`, `test_contracts.py`,
`test_crash_recovery.py` (real process, kill -9). One strict `xfail` documents that Lane D's ingest still accepts
unsigned foreign events; remove it when ingest fails closed.

### Running links (as left on 2026-10-12)

| what | URL |
|---|---|
| Story page | http://localhost:5173/ |
| Console, mock (simulator, no backend needed) | http://localhost:5173/demo |
| Console, LIVE against the real nodes | http://localhost:5173/demo?mode=live |
| Console, start with 2 nodes (join Delhi live) | http://localhost:5173/demo?nodes=2 |
| Mumbai API + docs | http://127.0.0.1:8001/docs · /v1/status |
| Pune API + docs | http://127.0.0.1:8002/docs |
| Delhi API + docs | http://127.0.0.1:8003/docs |

Start them: `.\.venv\Scripts\python.exe scripts/run_regions.py mum pun del` (backend) and `cd web; npm install; npm run dev` (frontend).
Dev keys: `run_one.py` now provisions `dev-officer`, `dev-supervisor` and `dev-admin` on every region (the console's live mode needs all three). Chaos agent for live Crash/Restart: `$env:REGIONS='mum,pun,del'; python scripts/chaos_agent.py`.

**Live mode:** the real nodes now serve `/healthz`, `/v1/carry` and `/v1/events/has` (merged `carry.py`), so live mode can probe, fail over and show "present on survivors". Crash/Restart need Lane C's chaos agent on :9000 (run it as above); without it the UI says so instead of faking it. Restart the regions after pulling so they run the merged code. Mock mode shows everything.

## 6. Design decisions worth knowing

- Signature = Ed25519 over the ASCII bytes of the event's hex `hash`, base64 (`region/engine/signing.py`; `region/trust/signing.py`
  re-exports it for the ingest hook). `sign(region, hash)` is None when no key is provisioned (events stay unsigned and carry refuses them).
  Keys: `keys/<region>.key` (git-ignored); registry `region/trust/public_keys.json` (git-ignored, generated). `run_one.py`
  provisions every region up front and each region also provisions itself at start. Known gap vs my earlier design: nothing stops
  a region id from silently taking a NEW key if its private key file is deleted (the registry entry is overwritten).
- The history `carried` flag is set by `sync/carry.py` calling `store.mark_carried` for each newly accepted carried event.
- Replays are decided from the events themselves (`events.op_id` index), not a separate idempotency table: the same
  rule covers writes done here, replicated, or carried. The `idempotency` table from migration 001 is unused.
- `receipts.receipt()` passes an engine receipt through if it has `candidates`, else builds the same shape from stored
  events, so the API is correct on engine v0 (today) and v1 (Rahil).
- Priority uses Simrit's existing `region.sync.push.classify_priority` (one classifier for the system); swap the single
  call in `writes._priority` for `engine.priority.classify` when Lane B delivers it.
- After commit, `writes._after_commit` hands the event to `push.enqueue` via `loop.call_soon_threadsafe` (the push queue is
  an asyncio queue; routes run in worker threads). Failure is harmless: the log is the outbox.
- Reads need no key (older clients and scripts GET without one); writes need an officer or supervisor key.

## 7. Requests and findings for other lanes (do not fix in Lane A files)

- **Simrit, ingest fails open:** `region/sync/ingest.py` verifies only events whose `sig` is not null and silently skips
  verification when `region.trust.signing` is missing, so stripping `sig` bypasses it. Foreign events must carry a valid
  `sig` once signing is on (quarantine `unsigned`). My xfail test flips when this is fixed.
- **Simrit, carry/healthz/has:** merged into ONE `region/sync/carry.py`. Kept from her: `ENABLE_CARRY=0` kill switch (503 `carry_disabled`), quarantine of every rejected carried event, `get_carry_metrics`/`reset_carry_metrics`. Kept from the signed version: unknown origin, bad hash and bad/missing signature are rejected (her copy accepted unsigned events whenever no key registry was found), the own-origin guard, ingest path, `mark_carried`, and 422 for more than 200 events (her copy silently dropped the rest). `/healthz` recomputes the level at most once a second. Her lane D tests were adjusted accordingly (carry a real signed event; known origin for the bad-hash case).
- **Simrit, keys:** `scripts/run_one.py` (now with her empty-DB guard and pid files, plus key provisioning for every region) only provisions `dev-officer`. Add `dev-supervisor` and `dev-admin`
  (`key:actor:role`) so the supervisor/resolve and admin paths can be demoed live.
- **Simrit, admin routes:** `/admin/*` only needs a known key (`require_key`), so an officer key can cut links and burst. `admin_guard` should require role `admin`; her `tests/integration/test_lane_d.py` uses the officer key on `/admin/links`, so change both together.
- **Miti (`lane-c`, not on `main`):** merging it conflicts in six Lane A files (`region/api/routes_reports.py`, `routes_review.py`, `writes.py`, `region/auth.py`, `region/config.py`, `region/main.py`): it carries its own `/v1/review` and `/resolve`. Keep the Lane A superset (idempotent op_id replay, `WriteResult`, `observed`, roles, history `sig_status`/`carried`) and port her auth-mode provenance into it; do not take either side whole. A merge preview is cheap: `git merge-tree --write-tree --name-only main origin/lane-c`.
- **Rahil (`lane-B-main-build`, not on `main`):** conflicts in `region/engine/derive.py`, `region/engine/signing.py`, fixtures and `tests/engine/test_engine.py`. Five of its commits carry a `Co-Authored-By: Claude` trailer (see the last log entry); do not merge it as is.
- **Rahil:** `engine/signing.py` and policy v1 are on `main`. When `derive` changes again, run `python scripts/gen_fixtures.py` and commit the fixtures.
- **Miti:** `dashboard/` is replaced by `web/` (plan section 7). Chaos agent contract: `POST :9000/crash/{region}`,
  `POST :9000/start/{region}`, `GET :9000/state`, header `X-Admin-Key`.

## 8. Progress log (newest at the bottom)

- 2026-10-12 earlier — Basic build and Phase 1 merged to `main` (PRs #1, #4); all lanes' basic builds on `main`, 162 tests.
- 2026-10-12 — New plan SyncShield v1.1 adopted. Created `a/akshit-main`. Backend milestone done: migration 002, key and
  signing seam, write path v2 (op_id, report ids, signed events, observed, roles), resolve/review/history/status v1.1,
  CORS and body bound, contract v1.1 + changelog + fixtures. Whole repo: 228 passed, 1 xfailed (before the contract-doc
  tests were added); `tests/api`: 195 passed, 1 xfailed. Next: the `web/` frontend.
- 2026-10-12 (stop point, session limit) — Frontend `web/` built and verified (see section 4). Final checks before this stop:
  whole repo `pytest tests` = 229 passed, 1 xfailed (intended); web: `tsc` clean, 56 tests, `npm run build` OK; browser scenario in mock
  mode 13/13 checks; live-mode smoke against the real nodes 7/7 checks. Everything is committed locally on `a/akshit-main`;
  NOT pushed yet (the user pushes, or asks for the push). After pushing, delete the old remote branches `a/lane-a-basic-build`,
  `a/phase1-roles-history`, `a/post-merge-notes` (all their commits are contained in `a/akshit-main` or already on `main`).

  **Left to do (Lane A, from the plan):**
  1. DONE this session: browser exercise of every remaining path (see section 4); Prove it now really writes on both sides of a cut; phone layout fixed; the console and simulator now read the real receipt shape (contract 3.4); SDK counts probe-driven failovers; README API curl samples (verified end to end on the real nodes); hand-off files for the web console.
  2. Frontend hand-off to Miti: replace the `dashboard/` text in the README `setup` block (owner C); decide whether to delete `dashboard/` (separate commit).
  3. Partner HTTP adapter (T13, tier 2-c): NOT built on purpose. Miti's `lane-c` branch already implements the partner credential integration (TRUE sessions behind the same `TrustAdapter`); a second adapter would only collide with it. Revisit after `lane-c` is merged.
  4. DONE: README API samples (in the README `api` block, no separate `docs/api.md`).
  5. Trial-and-Reward hardening on `main` (oversize input and error bodies are done; write-queue 503 is tier 2-b, needs Lane D's knobs).
  6. Waiting on others, then re-verify: ingest that fails closed on unsigned events (flip the xfail); Lane C chaos agent on :9000 for live Crash/Restart; Lane B's newer branch `lane-B-main-build` (check its Claude co-author trailers first, see the last log entry). `/healthz`, `/v1/carry`, `/v1/events/has`, ladder levels, policy v1 and the op_id rule are already on `main`.

- 2026-10-12 (merge to main) — Pushed `main` and `a/akshit-main` (both `121b1ec`), merged Simrit's `d/simrit` (6 conflicts, resolved as described in section 7), deleted the old Lane A branches. Whole repo `pytest tests` = 285 passed, 1 xfailed (stable on two runs, includes Simrit's multi-process lane D tests); web `tsc` clean, 56 tests. All Akshit commits carry only his identity, no trailers.
  **Finding for the team:** commit `8a07981` (Rahil, already on `main`) and five commits on `origin/lane-B-main-build` (`4270664 caf935f 808a342 2b7d8e0 4d964b2`) carry a `Co-Authored-By: Claude` trailer. Not rewritten here (it is shared history); needs a team decision before `lane-B-main-build` is merged.

- 2026-10-13 — Fetched: `main` had nothing new. Continued Lane A on `a/akshit-main`. Found and fixed while exercising the console: (1) Prove it wrote everything through one node, so it never produced the disagreement it claims to test; it now creates a proof cart, isolates a node, changes the cart on both sides and reports the decisions waiting; (2) the console and simulator keyed CLOSE_VS_NEW_INFO on `relationship` and per-candidate `kind` that the real engine does not send (it sends `relationship: CONCURRENT`, `rule` and `flags`, close first), which would have broken the Review dialog on real nodes; both now follow the contract and the contract says so; a backend test pins the shape; (3) the failover counter ignored a probe-driven switch; (4) live mode could not resolve or cut links because `run_one.py` provisioned only `dev-officer`; it now provisions all three dev keys; (5) phone layout: chips, dialog, header. Whole repo `pytest tests` = 286 passed, 1 xfailed; web `tsc` clean, 57 tests, build OK; browser scenarios pass in mock and LIVE, light and dark. The experimental `frontend-new/` and `frontend 2/` folders are not mine and are never staged.
