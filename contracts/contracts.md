# Lifeboat contracts (v1) — owner: Akshit

Frozen at 14:30. After that this file, `schemas.py` and the fixtures may only **grow**
(new optional fields, new endpoints). Never rename or remove. Changes go through a PR
labelled `contract` and a line in `CHANGELOG.md`.

## 1. Event envelope

Stored in `events.body` and sent between regions unchanged.

| key | type | notes |
|---|---|---|
| `v` | int | contract version, `1` |
| `event_id` | str | `"<origin>:<seq>"`, globally unique, never reused |
| `origin`, `seq` | str, int | seq is gap-free per origin, allocated inside the write transaction |
| `report_id` | str | `"R-<origin>-<seq of its CREATE>"` |
| `type` | str | `CREATE` \| `SET` \| `NOTE` \| `RESOLVE` (RESOLVE from PDF 2) |
| `field` | str\|null | null for CREATE and NOTE |
| `value` | any | CREATE: `{initial fields}`; NOTE: `{"text": ...}`; SET: the new value |
| `supersedes` | [event_id] | live values of this field the writer saw and replaces |
| `hlc` | [ms, counter] | hybrid logical clock; tie-break only |
| `priority` | 0/1/2 | 0 critical, 1 normal, 2 bulk (used from PDF 2) |
| `actor` | str | from the API key, never the request body |
| `reason` | str\|null | required on RESOLVE (PDF 2) |
| `wall` | str | display only, never ordering |
| `hash` | str | sha256 of canonical JSON of every key except `hash` and `sig` |
| `sig` | str\|null | Ed25519, PDF 2 Phase 2 |

Canonical JSON: `json.dumps(obj, sort_keys=True, separators=(',',':'), ensure_ascii=False)` as UTF-8.
Tie-break order: `(hlc[0], hlc[1], origin, seq)`.

## 2. Report fields

`title` (1–120), `description` (≤2000), `category` (flood, collapse, medical, fire, road_block,
shelter, other), `location` (`{lat_e6, lon_e6}` ints or null), `severity` (1–5), `status`
(open, assigned, resolved, closed, cancelled — no hard deletes), `assigned_team` (str|null),
`people_estimate` (int|null), `notes` (built from NOTE events). Validation lives in
`schemas.validate_field` and runs at **write time only**.

## 3. HTTP API (Basic subset)

Client header: `X-API-Key: dev-officer`. Internal calls: `X-Region`, `X-Peer-Token`.

| method + path | body | response / rule |
|---|---|---|
| `POST /v1/reports` | `{title, category, severity, location?, description?, people_estimate?}` | `201 {report, event_id, consistency}`; only after commit |
| `PATCH /v1/reports/{id}` | `{field, value, expected?: [event_ids]}` | `200 {report, event_id, consistency}`; 404 unknown; 409 `{error, live}` when `expected` differs from live set; 422 bad value |
| `POST /v1/reports/{id}/notes` | `{text}` | `201 {report, event_id, consistency}` |
| `GET /v1/reports` | `?limit=&cursor=` | JSON **array** of report views, newest first, incomplete hidden |
| `GET /v1/reports/{id}` | | report view |
| `GET /v1/status` | | status view |
| `GET /v1/health` | | `{region, ok: true}` |
| `GET /internal/v1/sync/summary` | | `{region, heads, max_seen, event_count}`; 401 unknown peer; 503 when link down |
| `GET /internal/v1/sync/events` | `?origin=&after=&limit=` | `{events (seq asc, seq>after), has_more}` |
| `GET /admin/links`, `GET/POST /admin/links/{peer}` | `{mode: up\|down}` | link table; needs `ENABLE_ADMIN=1` and a valid API key |

Reserved for PDF 2 (do not reuse): `POST /v1/reports/{id}/resolve`, `GET /v1/review`,
`GET /v1/reports/{id}/history`, `POST /internal/v1/sync/push`, `GET /v1/metrics`,
`POST /admin/config`, `POST /admin/burst`.

## 4. Responses the dashboard depends on

See `fixtures/report.json` and `fixtures/status.json` (generated from the real engine by
`python scripts/gen_fixtures.py`). Report `consistency.replication`: `LOCAL_ONLY` | `PARTIAL` | `REPLICATED`.
Additions beyond PDF 1 (all additive): status carries `event_digest`, `state_hash`,
`quarantined`, `incomplete_reports`; peers carry `fails`.

### 4.1 Status `level` (degradation ladder)

`level` is one of `schemas.LEVELS`:

| level | meaning | produced |
|---|---|---|
| `HEALTHY` | every peer reachable, heads equal | now |
| `SYNCING` | peers reachable, heads differ | now |
| `ISOLATED` | a peer failed 3 times or was silent for 5 s; writes still accepted | now |
| `CONSTRAINED` | link is up but measured slow; P0/P1 first, P2 deferred (never dropped) | PDF 2 |
| `RECOVERING` | link just came back; priority catch-up in progress | PDF 2 |
| `OVERLOADED` | admission limit reached | PDF 2 |

Clients must treat an unknown `level` as "unknown", never as healthy.

### 4.2 Optional nullable status blocks

Both keys are always present in `GET /v1/status` and are `null` until the owning lane fills them
in (`risk`: Rahil's `engine/risk.py`; `queues`: Simrit's push queue). Consumers must handle `null`.

```json
"risk":   {"unreplicated": 3, "critical_unreplicated": 1, "oldest_age_ms": 47200},
"queues": {"P0": 0, "P1": 2, "P2": 1}
```

- `risk.unreplicated`: acknowledged events of THIS region that no peer has confirmed holding.
- `risk.critical_unreplicated`: the subset with `priority == 0`.
- `risk.oldest_age_ms`: age of the oldest unreplicated event (0 when none).
- `queues.P0|P1|P2`: events waiting to be pushed to at least one peer, per priority lane.

### 4.3 List ordering and paging

`GET /v1/reports` is ordered newest first by the CREATE event's `(hlc, origin, seq)`, so every
region returns the same order for the same event set. `cursor` is an integer offset;
`limit` is clamped to 1..500; negative `limit`/`cursor` are rejected with 422.

## 5. Behavioural rules everyone relies on

- A write never waits for, or calls, another region.
- 201 is returned only after the SQLite commit (`synchronous=FULL`).
- One Uvicorn worker per region.
- Replication is idempotent: applying the same page twice changes nothing.
- A received event is checked for shape, hash and identity only; never re-validated against business rules.
- One link switch blocks both directions (outbound pull skipped, inbound `/internal` returns 503).
