# Architecture (Basic build)

![architecture](architecture.png)

Source: `architecture.svg` (edit it, then re-export the PNG). Text version:

```
            Operator dashboard (React + Vite, :5173)
            polls /v1/status and /v1/reports of each region every 1 s
                 |                              |
        +--------v---------+  pull (HTTP)  +----v-------------+       DELHI :8003
        | MUMBAI  mum:8001 |<------------->| PUNE   pun:8002  |       (configured later,
        | FastAPI, 1 worker|  /internal/   | FastAPI, 1 worker|        same code, own DB)
        | event log + view |  v1/sync/*    | event log + view |
        | link switch      |               | link switch      |
        +--------+---------+               +---------+--------+
                 |                                   |
          SQLite (WAL) data/mum.db            SQLite (WAL) data/pun.db
```

- Each region is a **complete, independent stack** with its own database.
- Regions talk only through `/internal/v1/sync`, which the link switch can cut in **both** directions.
- Writes go to one chosen region. There is no central server.
- Write path: validate → allocate `seq` → HLC stamp → append event → `derive()` → view → commit → `201`.
- Sync path: summary → per origin fetch pages after our watermark → idempotent ingest → re-derive touched reports.

## Where code lives

| concern | path |
|---|---|
| shapes and validation | `contracts/` |
| API and write path | `region/api/` |
| storage | `region/store.py`, `region/migrations/` |
| merge engine (pure) | `region/engine/` |
| replication and link switch | `region/sync/` |
| operator UI | `dashboard/` |

The dashboard shows only what the backend reports; an unreachable region is shown as unknown, never as empty.
