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
- **Working branch: `a/akshit-main`** (from `main`, plus the unmerged docs branch `a/post-merge-notes`). It replaces the
  earlier Lane A branches `a/lane-a-basic-build`, `a/phase1-roles-history` (both merged by PRs #1 and #4) and
  `a/post-merge-notes` (merged into this branch). Delete those three remote branches only after this one is pushed.
- There is an **untracked folder `frontend 2/`** in the repo root (someone's earlier generated incident-report dashboard,
  with `node_modules` inside). It is not part of this build: never `git add -A`; stage files by name.

## 4. Status board against the plan (Lane A rows)

Legend: [x] done and tested · [~] partly · [ ] not started · [D]/[B]/[C] waiting on another lane

### Backend
- [x] Contract v1.1 (`contracts/contracts.md`, `schemas.py`, `CHANGELOG.md`, status fixture) and migration `002_op_id.sql`
- [x] Migration runner in `store.py` (001 idempotent each start; later files once, in a transaction, recorded in `schema_migrations`)
- [x] Signing seam: `region/trust/keys.py` (per-region Ed25519 key, registry), `region/trust/signing.py` (`sign`, `verify`)
- [x] Write path v2: `op_id` idempotency (also for events that arrived by replication/carry), client `report_id`,
      signed event in every 2xx body, `observed` plumbing, roles + `auth_mode`, push hand-off after commit
- [x] `POST /v1/reports/{id}/resolve` (supervisor, 409 guards) and `GET /v1/review` with full receipts
- [x] `GET /v1/reports/{id}/history` with `sig_status` and `carried`
- [x] `/v1/status` v1.1 (peers `acked`/`last_ok_ms`, computed `risk`, nullable `queues`/`carry`)
- [x] CORS explicit origin list, body-size bound, startup key provisioning
- [x] Tests T7, T10, T16 (A part), A9, 403/409/422 paths: `tests/api/` (see section 5)
- [ ] Partner HTTP adapter with cache and fallback + T13 (tier 2-c; interface, contract and mock already exist)
- [ ] README API samples (`docs/api.md`)
### Frontend (`web/`)
- [ ] Foundation (Vite + React + TypeScript, themes), simulator, SDK, shop adapter, Landing, Demo, panels
- [ ] Mock mode acceptance A1–A12, live mode against real nodes, light and dark themes, animations
- [ ] Hand-off notes for Miti, `scripts/run_web.*`, `.env.example` keys, remove `dashboard/` (separate commit)
### Waiting on other lanes
- [B] `engine/signing.py` (my `trust/signing.py` is the seam; B may delegate or replace its body), `policy v1`, `op_id` group rule in `derive`, `CLOSE_VS_NEW_INFO`, `priority.classify`
- [D] `GET /healthz`, `POST /v1/carry`, `POST /v1/events/has`, ingest that fails closed on unsigned events, levels CONSTRAINED/RECOVERING, queues/carry in the snapshot
- [C] `scripts/chaos_agent.py` (:9000), fixtures

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

## 6. Design decisions worth knowing

- Signature = Ed25519 over the ASCII bytes of the event's hex `hash`, base64. `sign` only uses THIS region's key.
  Keys: `keys/<region>.key` (git-ignored) and `keys/<region>.pub`; committed registry `region/trust/public_keys.json`
  (empty seed). Each region writes only its own `.pub`, so three regions starting at once cannot corrupt a registry.
  `ensure_region_key` refuses to re-key a region id that is registered with a different public key.
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
- **Simrit, carry/healthz/has:** contract is in `contracts/contracts.md` 3.2; on a carried event call
  `store.mark_carried(conn, [event_id, ...])` so history shows `carried: true`, and fill `carry` in the status snapshot.
- **Simrit, keys:** `scripts/run_one.py` only provisions `dev-officer`. Add `dev-supervisor` and `dev-admin`
  (`key:actor:role`) so the supervisor/resolve and admin paths can be demoed live.
- **Rahil:** `engine/signing.py` may delegate to or replace `region/trust/signing.py`; keep `verify(origin, hash, sig)`.
  When policy v1 changes `derive`, run `python scripts/gen_fixtures.py` and commit the fixtures.
- **Miti:** `dashboard/` is replaced by `web/` (plan section 7). Chaos agent contract: `POST :9000/crash/{region}`,
  `POST :9000/start/{region}`, `GET :9000/state`, header `X-Admin-Key`.

## 8. Progress log (newest at the bottom)

- 2026-10-12 earlier — Basic build and Phase 1 merged to `main` (PRs #1, #4); all lanes' basic builds on `main`, 162 tests.
- 2026-10-12 — New plan SyncShield v1.1 adopted. Created `a/akshit-main`. Backend milestone done: migration 002, key and
  signing seam, write path v2 (op_id, report ids, signed events, observed, roles), resolve/review/history/status v1.1,
  CORS and body bound, contract v1.1 + changelog + fixtures. Whole repo: 228 passed, 1 xfailed (before the contract-doc
  tests were added); `tests/api`: 195 passed, 1 xfailed. Next: the `web/` frontend.
