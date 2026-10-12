# Lane A (Akshit) — handoff and progress log

Purpose: let any engineer or coding agent continue Lane A from exactly where it stopped, with no
other context. Read this file first, then the two build-plan PDFs (Basic = PDF 1, Main = PDF 2).
**Keep the "Progress log" and the checklists up to date after every milestone.**

Last updated: 2026-10-12 (all four lanes merged to `main`; Lane A basic build + Phase 1 are on `main`; full suite 162 passed; next is Phase 2)

---

## 1. Project in one paragraph

**Lifeboat** (CodeAstra 2.0, RGIT, PS2: geo-distributed disaster data replication with graceful
degradation). Each region (mum :8001, pun :8002, later del :8003) is an independent FastAPI +
SQLite service that always accepts writes. Every change is an immutable signed-later event; a
region's visible data is `derive(event_set)`; replication copies missing events (pull by per-origin
gap-free watermark). AP + strong eventual consistency. Stack: Python FastAPI, SQLite (WAL,
`synchronous=FULL`, one file per region), httpx, React + Vite dashboard.

## 2. Team lanes and file ownership (from OWNERS.md)

| lane | person | owns |
|---|---|---|
| **A** | **Akshit (me)** | `contracts/`, `region/main.py`, `region/store.py`, `region/auth.py`, `region/migrations/`, `region/api/`, `region/trust/`, `requirements.txt`, `tests/api/` |
| B | Rahil | `region/engine/`, `tests/engine/`, `tests/fuzz/`, `docs/conflict-policy.md` |
| C | Miti | `dashboard/`, `docs/` (except conflict-policy), `README.md` |
| D | Simrit | `region/sync/`, `region/config.py`, `.env.example`, `scripts/`, `tests/integration/` |

Rules: branches `a/…`; never edit another lane's files (ask the owner); `contracts/*` and
`migrations/*` are **additive only** (new optional fields), with a line in `contracts/CHANGELOG.md`;
no force-push; one Uvicorn worker per region; a write never calls a peer; no code copied from
reference repos.

**Commit identity rule (hard requirement from the user):** every commit is authored by
`Akshit Sharma <akshitsharma24@spit.ac.in>` (already in git config). No co-author trailers or
auto-generated attribution lines, and no tool names, in commit messages, PR text, code comments or docs.

## 3. Repo and branch

- Remote: `https://github.com/CtrlAltElite-astra/monorepo` (default branch `main`).
- `main` started as ONE commit by Miti that already contained a working Basic build for all four lanes.
  Since then merged via PRs: #1 Lane A basic build (mine), #2 Miti's dashboard/docs (`lane-c`),
  #3 Rahil's engine tests/docs (`lane-B-basic-build`). Lane D (Simrit) had no branch yet when last checked.
- `a/lane-a-basic-build`: merged (PR #1). `a/phase1-roles-history`: merged (PR #4).
- Also merged: Simrit's Lane D (PR #5, branch `d/simrit`): push fast path, slow links, `loadgen.py`,
  `mock_partner.py`, scenario scripts, `tests/integration/test_lane_d.py`. Its own notes: `LANE_D_HANDOFF.md`.
- `main` is at `8d68cee`. **Current working branch: `a/post-merge-notes`** (docs/contract fixes only;
  the user opens and merges PRs). Start Phase 2 work on a new branch from the latest `main`.
- NOT on `main` yet: Rahil's policy v1 (the engine is still `POLICY_VERSION = 0`, rule `v0-latest`).
- Working copy on the user's machine: `C:\Users\dell\Desktop\Codeastra\monorepo` (Windows 11,
  PowerShell; Python 3.14 venv at `.venv`, Node 24).

## 4. How to run everything

```powershell
cd C:\Users\dell\Desktop\Codeastra\monorepo
python -m venv .venv                      # once
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest tests/api -q              # Lane A tests (in-process)
.\.venv\Scripts\python.exe -m pytest tests/engine tests/integration -q   # baseline, other lanes
.\.venv\Scripts\python.exe scripts/run_regions.py              # mum :8001 + pun :8002, data/*.db
.\.venv\Scripts\python.exe scripts/gen_fixtures.py             # regenerate contracts/fixtures/*.json
cd dashboard; copy .env.example .env; npm install; npm run dev # http://localhost:5173
```

Baseline before any Lane A change: `pytest tests/engine tests/integration` -> **17 passed**.
Merged `main` (all four lanes), 2026-10-12: `pytest tests` -> **162 passed** (about 55 s; the slow part is
real-process integration tests).

Run the whole project live (verified 2026-10-12 on merged `main`):

```powershell
.\.venv\Scripts\python.exe scripts/run_regions.py            # mum :8001 + pun :8002 (one dying no longer stops the other)
cd dashboard; npm run dev                                    # http://localhost:5173
.\.venv\Scripts\python.exe scripts/scenarios/split_brain.py  # cut, write both sides, heal, converge
.\.venv\Scripts\python.exe scripts/scenarios/throttle.py     # slow link: severity-5 report overtakes 20 bulk notes (~1 s)
.\.venv\Scripts\python.exe scripts/mock_partner.py --port 8000   # mock partner (key "partner-secret-key")
```
Live result: both regions converge (same event_digest and state_hash), level HEALTHY, every report
REPLICATED with `stale_possible` false, same list order on both, CORS preflights from
`http://localhost:5173` pass for POST/PATCH/admin links. `/v1/review` and `/v1/metrics` answer 404
(not built; the dashboard tolerates that). Stop everything with Ctrl+C in each terminal.

## 5. Lane A scope (PDF 1, Basic build)

Acceptance ("done when"):
1. `POST /v1/reports` on mum returns 201 with `event_id` `mum:1`; the row exists after a process restart.
2. Two quick PATCHes on the same field produce **no** conflict (second supersedes first).
3. Stopping pun does not change mum's write latency or response.
4. `GET /v1/status` returns the 4.3 shape with real heads.

Basic API owned by Lane A: `POST /v1/reports`, `PATCH /v1/reports/{id}` (`expected` -> 409),
`POST /v1/reports/{id}/notes`, `GET /v1/reports[/{id}]`, `GET /v1/status` (shared with D),
`GET /v1/health`. Reserved for PDF 2 (do not reuse): `/resolve`, `/v1/review`, `/history`,
`/internal/v1/sync/push`, `/v1/metrics`, `/admin/config`, `/admin/burst`.

## 6. What Lane A has done

### Already in `main` (Miti's initial commit; verified working)
`store.py` (tx = BEGIN IMMEDIATE under an RLock, gap-free `next_seq`, HLC persisted in `node_meta`,
`insert_event_if_absent` new/dup/mismatch, views, quarantine), `migrations/001_init.sql` (all 9
tables, idempotent), `api/writes.py` (local write path: seq + HLC + hash + event + view in one tx,
201 only after commit), `api/routes_reports.py`, `api/routes_status.py`, `auth.py` (API key ->
actor, role), `main.py` (lifespan: init DB, start pull loop), `contracts/` (envelope, fields,
`validate_field`, 3 fixtures).

### My changes on branch `a/lane-a-basic-build`
- [x] Contracts (additive): `schemas.py` gains `LEVELS` (adds CONSTRAINED, RECOVERING, OVERLOADED),
      `REPLICATION_STATES`, `RISK_KEYS`, `QUEUE_KEYS`; `contracts.md` sections 4.1 (level ladder),
      4.2 (nullable `risk` and `queues`), 4.3 (list ordering/paging); `CHANGELOG.md` entry;
      `scripts/gen_fixtures.py` + regenerated `fixtures/status.json` with sample `risk`/`queues`.
- [x] `GET /v1/status` now always returns `risk` and `queues` (null until Rahil's `engine/risk.py`
      and Simrit's push queue fill them; read from `state.snapshot()` via `.get`).
- [x] `GET /v1/reports`: ordered newest-first by the CREATE event `(hlc, origin, seq)` (same order on
      every region; old code sorted by per-origin seq, which is not chronological across regions);
      `limit`/`cursor` validated (>= 0, limit clamped 1..500). New `store.report_ids_newest_first`.
- [x] `PATCH` body: `value` is now required (null allowed to clear nullable fields).
- [x] `main.py` lifespan awaits the cancelled pull task before closing the DB.
- [x] `tests/api/conftest.py` (in-process region fixture, `mk_event` peer-event factory).
- [x] `tests/api/test_reports_api.py`: create/patch/notes/validation/401/404/409/list/ordering/badges/
      write-never-calls-peer/hung-peer/restart (56 tests incl. parametrised; status 8, store 7, contracts 36).
- [x] `tests/api/test_status_api.py`: status shape vs fixture, heads, digests, nullable blocks.
- [x] `tests/api/test_store.py`: concurrent gap-free seq, rollback, WAL/synchronous=FULL, region guard.
- [x] `tests/api/test_contracts.py`: fixture hashes, schemas stays stdlib-only, doc/code parity.
- [x] Full suite green: `pytest tests` -> **124 passed** (107 in `tests/api`, 17 engine + integration).
      Mutation check done: the ordering, status-shape and `value`-required tests fail on the original code.
- [x] Real-process walkthrough of PDF 1 section 8 passed (20/20 checks: replication, hard kill + restart,
      catch-up, partition both ways, heal, identical conflict on both regions).
- [x] Acceptance 3 measured: mum write latency p50 2.9 ms with pun alive vs 2.4 ms with pun killed.
- [x] Dashboard still builds (`npm ci && npm run build`).
- [x] Commit (author Akshit only, no trailers) and push `a/lane-a-basic-build`.

**Basic build status for Lane A: COMPLETE.** All four PDF 1 acceptance checks are covered by tests and
were observed against real processes.

### PDF 2 Phase 1 (branch `a/phase1-roles-history`) — built and tested
- [x] **Roles**: `region/trust/roles.py` (pure table: officer/supervisor write; supervisor resolves;
      admin = /admin only, NOT a report writer; unknown role denied). `region/auth.py` gains
      `require(action)`; the three write routes use it. 401 unknown/missing key, 403 wrong role,
      checked before body validation, nothing stored. `require_key` kept (Simrit's `/admin` guard uses it).
- [x] **Trust adapter interface**: `region/trust/adapter.py` (`TrustAdapter`, `AuthDecision`,
      `EvidenceVerdict`, `AnchorReceipt`, `TrustVerdict`, default `LocalKeyAdapter`, `get_adapter`/
      `set_adapter`). Unsupported methods return neutral results, never raise. Auth decisions already go
      through the adapter, so the Phase 2 partner adapter only needs to be swapped in.
- [x] **Partner contract page**: `contracts/partner-contract.md` (PROPOSED, not agreed with a partner).
- [x] **Consistency block**: report `consistency` = `{replication, confirmed_by, awaiting, stale_possible}`
      (`routes_reports._consistency`; reachability from `state.snapshot()` once per request).
- [x] **`GET /v1/reports/{id}/history`**: plain array of full events in `(hlc, origin, seq)` order
      (matches what Miti's dashboard assumed).
- [x] **kill -9 test**: `tests/api/test_crash_recovery.py` — real process, 4 concurrent writers, hard kill
      mid-stream; every acknowledged id present, seq gap-free and continues, views == creates, clock moves on.
- [x] Contracts/fixtures/CHANGELOG updated (`contracts.md` 3.1 roles, 4.0 consistency, history row,
      `schemas.CONSISTENCY_KEYS`); contract-parity tests added.
- [x] Whole repo: `pytest tests` -> **156 passed**. Real-process check (2 regions): consistency goes
      REPLICATED -> LOCAL_ONLY + stale on a cut -> REPLICATED after heal; history identical on both regions.

## 7. Design decisions worth knowing

- Event ids are `<origin>:<seq>`; report ids are `R-<origin>-<seq of CREATE>` (no coordination).
- Validation (`schemas.validate_field`, pydantic bounds) runs at **write time only**; received events
  are checked for shape/hash/identity only (never against business rules).
- `consistency` is computed from `peer_view`: a peer confirms a report when its last summary shows
  `seq >= max seq per origin` of the report's events. `stale_possible` = ANY configured peer is
  unreachable now (even one that confirmed), per PDF 2 6.8.
- GET endpoints are unauthenticated; writes need `X-API-Key` with role officer or supervisor (admin is
  deliberately not a report writer, per the PDF 2 role table).
- Auth decisions go through `region.trust.adapter.get_adapter()`; tests swap it with `set_adapter`.
- Tests patch `config.settings` (module attribute, frozen dataclass replaced with
  `dataclasses.replace`); all code reads `config.settings.<x>` at call time, so this is safe.

## 8. Known gaps / not started (PDF 2, Lane A)

Phase 1: DONE (see section 6). Not done from Phase 1: nothing for Lane A.
**Phase 2 (next):** `POST /v1/reports/{id}/resolve` + `GET /v1/review` (409 stale guard, role
`supervisor`; needs Rahil's receipts/policy v1 in `derive`), Ed25519 per-region signing in `trust/`
(`sig` over the hex hash; verify hook in Simrit's ingest; tests T10), idempotency keys
(`Idempotency-Key` header, table already exists), `observed` field on events (CLOSE_VS_NEW_INFO),
event `auth` block, real partner HTTP adapter with cache + fallback (T13).
Phase 3: `/v1/metrics` writes, bounded write queue + `OVERLOADED`, `/admin/config` coordination with Simrit.
Phase 4: negative security tests (T10, T13), README API samples with curl.

## 8b. Findings for other lanes (not Lane A files; do not fix here, tell the owner)

- **Simrit (`scripts/run_regions.py`)**: the supervisor loop exits and terminates ALL regions as soon as
  any one process dies. So "stop one region, restart it" (README restart proof) only works when each
  region is started separately with `python scripts/run_one.py <region>`. A hard kill of pun through
  `run_regions.py` also takes mum down.
- **Simrit (`state.snapshot`)**: a peer never contacted yet counts as unreachable, so a fresh region
  with a configured peer shows `ISOLATED` until the first successful pull (about one interval).
  Honest, but the dashboard will flash ISOLATED at start-up.
- **Rahil / Simrit**: `/v1/status` now returns `risk` and `queues` (null). Fill them by adding
  `"risk"` / `"queues"` keys to the dict returned by `state.snapshot()`; no change needed in the route.
- (Fixed by Miti already: `run_regions.py` no longer stops every region when one dies.)

### Asks created by Phase 1 (for the owners; not done here because they are not Lane A files)
- **Simrit** (`region/config.py`, `scripts/run_one.py`): provision default keys so roles can be demoed:
  `dev-officer:officer-<r>-1:officer`, `dev-supervisor:supervisor-<r>-1:supervisor`,
  `dev-admin:admin-<r>-1:admin`. Today only `dev-officer` exists, so nobody can act as supervisor.
- **Simrit** (`region/sync/routes_admin.py`): when ready, gate `/admin/*` with `require("admin")` from
  `region.auth` instead of `require_key`. NOT done yet on purpose: the dashboard and
  `tests/integration` call `/admin/links` with the officer key, so switching needs the admin key
  provisioned and those callers updated in the same change.
- **Miti**: `VITE_SUPERVISOR_KEY` (resolve button) should be `dev-supervisor` once that key exists;
  an officer key gets 403 on resolve. Reports now carry `consistency.confirmed_by/awaiting/stale_possible`
  (the drawer already reads `stale_possible`).
- **Simrit**: `scripts/mock_partner.py` now exists and its paths/shapes match `contracts/partner-contract.md`.
  Differences found on 2026-10-12 (contract updated to cover the first two; mock unchanged):
  it answers a forged/revoked credential with HTTP 200 + `valid: false` (our adapter must treat that as
  invalid, like 401/403); it returns `region_scope: "all"` (accepted); and it never enforces `X-Api-Key`
  (`verify_partner_key` is defined but unused), so a negative test for a wrong partner key cannot use it
  yet. Not blocking.
- **Simrit** (still open): default `dev-supervisor` / `dev-admin` keys in `config.py` and `scripts/run_one.py`;
  `run_one.py` still only provisions `dev-officer`.
- **Rahil** (still open): policy v1. Live proof it is missing: split_brain.py ends with severity 4
  (`v0-latest`), where PDF 2 policy would show 5 (highest). After it merges run `scripts/gen_fixtures.py`.
- **Rahil**: when policy v1 changes `derive`, run `python scripts/gen_fixtures.py` and commit the fixtures;
  `tests/api/test_contracts.py::test_report_fixture_is_what_derive_produces` fails until you do.

## 9. Progress log (append newest at the bottom)

- 2026-10-12 — Cloned monorepo; analysed both PDFs and Miti's commit. Baseline 17 tests pass.
- 2026-10-12 — Created branch `a/lane-a-basic-build`; contract additions (status `risk`/`queues`,
  level ladder, list ordering) done; store/route/main hardening done; `tests/api/conftest.py` written.
  Next: write the four API test modules, run everything, push.
- 2026-10-12 — API test suite written (107 tests), full suite 124 passed, real-process walkthrough and
  latency check passed, dashboard builds. Handoff doc finalised for the Basic build; committing and
  pushing `a/lane-a-basic-build`. **Next (after the user confirms): PDF 2 Phase 1 for Lane A**
  (roles enforcement, consistency block `confirmed_by`/`awaiting`/`stale_possible`,
  `GET /v1/reports/{id}/history`, kill -9 test, partner adapter interface + contract page).
- 2026-10-12 — PRs #1 (Lane A), #2 (Miti), #3 (Rahil) merged into `main`; 135 tests green on merged `main`.
  Started and finished Phase 1 on `a/phase1-roles-history`: roles + adapter interface, partner contract,
  consistency block, history endpoint, kill -9 test. 156 tests green; real-process check passed.
  **Next: open a PR for `a/phase1-roles-history` (label `contract`), then Phase 2** (resolve/review,
  Ed25519 signing, idempotency keys), which depends on Rahil's policy v1 receipts landing in `main`.
- 2026-10-12 — PR #4 (Phase 1) and #5 (Simrit, Lane D) merged. Pulled `main` (`8d68cee`): 162 tests pass.
  Ran the project live (2 regions + dashboard dev server + Simrit's split_brain and throttle scenarios):
  all converge, 20/20 live checks pass. Found: mock partner semantics (see section 8b) and that policy v1
  is still missing. Partner contract amended (`valid:false` = invalid, `region_scope` may be `all`,
  `exp` in Unix seconds); `adapter.AuthDecision.status_ver` now accepts int or str.
  **Next: Phase 2 on a fresh branch.** Parts that do not need policy v1: Ed25519 signing in `trust/signing.py`
  (Simrit's `ingest.py` already calls `trust.signing.verify(origin, hash, sig) -> bool` when an event has a
  `sig`, and quarantines `bad_sig`; so `trust/signing.py` must export exactly that function),
  idempotency keys. Resolve/review wait for Rahil's receipts.
  **Security gap to close in Phase 2:** `ingest.py` only verifies events whose `sig` is not null and
  silently skips verification if `signing` is missing. Once signing exists, an attacker could strip `sig`
  to bypass it. Agree with Simrit a rule that events from another origin MUST carry a valid `sig`
  (fail closed, quarantine `unsigned`/`bad_sig`), behind a setting so tests with unsigned fixtures still run.
