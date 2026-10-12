# Lifeboat — Geo-Distributed Disaster Data Replication with Graceful Degradation

CodeAstra 2.0 (RGIT) · PS2 · Team lanes: **A** Akshit · **B** Rahil · **C** Miti · **D** Simrit

## The problem

During a flood or earthquake the network link between two control rooms can break while both
rooms still have power and officers typing reports. A system that needs one central server stops
accepting reports at exactly the wrong moment. A system that later copies one database over the
other silently loses work.

## What we build

Independent regional copies (Mumbai, Pune, later Delhi) of one disaster-report service.
Each region:

- accepts reports on its own, even when every peer is unreachable;
- stores them durably in its own SQLite file (201 is returned only after commit);
- swaps missing changes with the other regions whenever the network allows;
- when two regions changed the same field while cut off, detects it, applies a stated rule,
  and shows the disagreement instead of hiding it.

## Design in five lines

1. **AP + strong eventual consistency.** Every region always accepts valid writes. Regions holding
   the same set of events always derive the same state. Reads are local and may be stale; the API says so.
2. **Every change is an immutable event** (`CREATE`, `SET`, `NOTE`, later `RESOLVE`) with a
   per-origin gap-free `seq`, a hybrid logical clock, and a content hash.
3. **`supersedes` pointers carry causality.** A write lists the field values it saw and replaces.
   Two writes to the same field where neither replaced the other are *concurrent* = a conflict.
4. **`derive()` is a pure function of the event set.** Order, duplicates and relays do not matter,
   so replication can be unordered, repeated and relayed and still converge.
5. **Replication = copying missing events**, pulled per origin by a gap-free watermark.
   The partition is simulated by a link switch enforced in the backend, both directions.

Full detail: `docs/` and the two build-plan PDFs (Basic Build Plan, Main Build Plan).

## Repository layout

| path | what | single owner |
|---|---|---|
| `contracts/` | event envelope, API shapes, fixtures — frozen, additive-only | Akshit |
| `region/main.py`, `store.py`, `auth.py`, `migrations/`, `api/`, `trust/` | API, storage, security | Akshit |
| `region/engine/` | canonical JSON, HLC, `derive()`, digests (pure, no I/O) | Rahil |
| `region/sync/`, `region/config.py`, `scripts/`, `tests/integration/` | replication, link switch, runtime | Simrit |
| `dashboard/`, `docs/`, `README.md` | operator UI and documentation | Miti |

See [OWNERS.md](OWNERS.md) for the change protocol.

## Quick start

Needs Python 3.11+ and Node 18+.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows   (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt

python scripts/run_regions.py     # mum :8001 and pun :8002, data in data/*.db

cd dashboard
copy .env.example .env            # (cp on Linux/macOS)
npm install
npm run dev                       # http://localhost:5173
```

Try it: create a report in Mumbai → it appears in Pune within ~3 s. Press **Cut mum↔pun**,
edit the same field on both sides, **Heal** → one conflict, identical on both.

UI without a backend: set `VITE_FIXTURES=1` in `dashboard/.env` (read-only, uses `contracts/fixtures` and `dashboard/src/mock`).

Restart proof: stop one region, run `python scripts/run_one.py pun` — data is still there.

## Tests

```bash
pytest tests/engine        # pure engine: hash, HLC, derive, digests
pytest tests/integration   # spawns real region processes: replicate, restart, partition, heal
bash scripts/check_m1.sh   # curl evidence script (needs jq, regions running)
```

## Ports and environment

| region | port | database |
|---|---|---|
| mum | 8001 | `data/mum.db` |
| pun | 8002 | `data/pun.db` |
| del | 8003 | `data/del.db` (joins in Phase 3) |

All settings come from environment variables; see `.env.example`. Run **exactly one** Uvicorn
worker per region (sequence numbers are guarded by an in-process lock).

## API (Basic subset)

Header `X-API-Key: dev-officer`. Full contract in `contracts/contracts.md`.

| method + path | purpose |
|---|---|
| `POST /v1/reports` | create a report → `201 {report, event_id, consistency}` |
| `PATCH /v1/reports/{id}` | set a field; `expected` ids → `409` if stale |
| `POST /v1/reports/{id}/notes` | add a note |
| `GET /v1/reports[/{id}]` | report view with `state`, `live`, `conflicts` |
| `GET /v1/status` | heads, peers, level, event/state digests |
| `GET /internal/v1/sync/{summary,events}` | peer replication (503 when link down) |
| `POST /admin/links/{peer}` | `{mode: up\|down}` partition switch |

## Status

Done (Basic build, M1/M1b): two persistent regions, pull replication with watermarks, link
switch both ways, conflict detection with provisional "latest" value, status skeleton, dashboard.

**Not done yet** (PDF 2): per-field conflict policy v1, review queue and `RESOLVE`, push fast path,
Ed25519 signatures and roles, priority lanes and slow links, Delhi region, metrics, fuzz test,
partner integration. Nothing in PDF 2 should be described as implemented until its test passes.

## Limits we state openly

Region-level offline only (no browser offline mode); a region with a valid key can still lie
(no Byzantine tolerance); localhost traffic is not TLS; events a destroyed region never
replicated are lost.
