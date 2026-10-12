# Lane A (Akshit) — handoff and progress log

Purpose: let any engineer or coding agent continue Lane A from exactly where it stopped, with no
other context. Read this file first, then the two build-plan PDFs (Basic = PDF 1, Main = PDF 2).
**Keep the "Progress log" and the checklists up to date after every milestone.**

Last updated: 2026-10-12 (Basic build complete and pushed; PDF 2 Phase 1 not started)

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
- `main` currently has ONE commit by Miti ("Initial commit") that already contains a working Basic
  build for all four lanes (engine, sync, dashboard, store, API, integration tests).
- **My branch: `a/lane-a-basic-build`** (created from `main`). All Lane A work is pushed here.
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

## 7. Design decisions worth knowing

- Event ids are `<origin>:<seq>`; report ids are `R-<origin>-<seq of CREATE>` (no coordination).
- Validation (`schemas.validate_field`, pydantic bounds) runs at **write time only**; received events
  are checked for shape/hash/identity only (never against business rules).
- `consistency.replication` is computed from `peer_view`: a peer confirms a report when its last
  summary shows `seq >= max seq per origin` of the report's events.
- GET endpoints are unauthenticated in Basic; writes need `X-API-Key`. Roles are PDF 2 Phase 1.
- Tests patch `config.settings` (module attribute, frozen dataclass replaced with
  `dataclasses.replace`); all code reads `config.settings.<x>` at call time, so this is safe.

## 8. Known gaps / not started (PDF 2, Lane A)

Phase 1: SET validation per policy, roles (officer/supervisor/admin) enforcement, `PATCH` expected/409
(done in Basic), consistency block (`confirmed_by`, `awaiting`, `stale_possible`),
`GET /v1/reports/{id}/history`, kill -9 test, partner adapter interface + contract page.
Phase 2: `POST /resolve` + `GET /v1/review` (409 stale guard), Ed25519 signing (`trust/`), idempotency
keys, real partner calls with cache + fallback. Phase 3: `/v1/metrics` writes, bounded write queue
(OVERLOADED). Phase 4: negative security tests (T10, T13), README API samples.

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
