# SyncShield contracts (v1.2) — owner: Akshit

Contract version stays `1`. This file, `schemas.py` and the fixtures may only **grow**
(new optional fields, new endpoints). Never rename or remove. Changes go through a PR
labelled `contract` and a line in `CHANGELOG.md`. v1.1 is additive: every new field is nullable,
so older events, fixtures and clients keep working. v1.2 (section 8: scaling, rate limiting, identity claims) is
additive too: every new setting is off by default.

## 1. Event envelope

Stored in `events.body` and sent between regions unchanged.

| key | type | notes |
|---|---|---|
| `v` | int | contract version, `1` |
| `event_id` | str | `"<origin>:<seq>"`, globally unique, never reused |
| `origin`, `seq` | str, int | seq is gap-free per origin, allocated inside the write transaction |
| `report_id` | str | client-chosen `R-` + 8 hex (carts), or server-made `R-<origin>-<seq of its CREATE>` |
| `type` | str | `CREATE` \| `SET` \| `NOTE` \| `RESOLVE` |
| `field` | str\|null | null for CREATE and NOTE |
| `value` | any | CREATE: `{initial fields}`; NOTE: `{"text": ...}`; SET / RESOLVE: the new value |
| `supersedes` | [event_id] | live values of this field the writer saw and replaces (at most 16) |
| `hlc` | [ms, counter] | hybrid logical clock; tie-break only, never decides safety |
| `priority` | 0/1/2 | 0 critical, 1 normal, 2 bulk (`section 3.6`) |
| `op_id` | str\|null | **v1.1** client operation id (UUID); null for server-made events |
| `observed` | [event_id]\|null | **v1.1** evidence a close / new-info write had seen (`section 3.5`); else null |
| `actor` | str | from the API key, never the request body |
| `role` | str | **v1.1** role of the key that authorised the write (`officer` / `supervisor`) |
| `auth_mode` | str | **v1.1** `online` \| `cached` \| `stale`: how fresh the trust data behind the decision was |
| `reason` | str\|null | required on RESOLVE |
| `wall` | str | display only, never ordering |
| `hash` | str | sha256 of canonical JSON of every key except `hash` and `sig` |
| `sig` | str\|null | **v1.1** base64 Ed25519 over the ASCII bytes of `hash`, made by the origin's own key |

Canonical JSON: `json.dumps(obj, sort_keys=True, separators=(',',':'), ensure_ascii=False)` as UTF-8, no floats
in hashed fields. Tie-break order: `(hlc[0], hlc[1], origin, seq)`.

`schemas.EVENT_KEYS` lists the envelope keys, including `op_id` and `observed` (always present, nullable);
`schemas.EVENT_OPTIONAL_KEYS` lists the additive `role` and `auth_mode`. Every NEW event carries all of them;
older events and fixtures may lack the optional two.

## 2. Report fields

`title` (1–120), `description` (≤2000), `category` (flood, collapse, medical, fire, road_block,
shelter, other, **cart**), `location` (`{lat_e6, lon_e6}` ints or null), `severity` (1–5), `status`
(open, assigned, resolved, closed, cancelled — no hard deletes), `assigned_team` (str|null),
`people_estimate` (int|null), `notes` (built from NOTE events). Validation lives in
`schemas.validate_field` and runs at **write time only**.

The backend is domain neutral. The shop demo only relabels these fields in the browser
(`web/src/domain/shop.ts`): title = cart owner, severity = delivery speed (1 Standard … 5 Priority),
status open/assigned/resolved/closed/cancelled = Shopping/Checking out/Paid/Order placed/Cancelled,
assigned_team = fulfilment centre, one NOTE per item added.

## 3. HTTP API

Client headers: `X-Api-Key` (writes), `Idempotency-Key` (optional alias of `op_id`). Internal calls:
`X-Region`, `X-Peer-Token`. Reads (`GET /v1/...`) need no key; a key is accepted and ignored.

### 3.0 Errors and bounds

| status | meaning |
|---|---|
| 401 | no key, a key this region does not know, or an unknown peer / bad peer token |
| 403 | a known key whose role may not do this (checked before the body is read; nothing is stored) |
| 404 | unknown report |
| 409 | `stale_edit` (stale `expected` / stale resolve, with `live`), `no_conflict`, `op_id_reuse`, `report_exists` |
| 422 | validation, or a body larger than 512 kB |
| 429 + `Retry-After` | `rate_limited`: this actor sent writes faster than its allowance (v1.2, section 8.3) |
| 503 + `Retry-After` | overload or link down; v1.2 adds the body `overloaded` for a region over its write limit |

Bounds: title 120 · description 2 000 · note 2 000 · reason 500 · `supersedes`/`expected`/`candidates` ≤ 16 ·
sync page ≤ 200 events · carry ≤ 200 events · has ≤ 500 ids · body ≤ 512 kB · `op_id` ≤ 64 chars.
They are constants in `schemas.py`.

### 3.1 Roles (who may do what)

The role comes from the API key table (`API_KEYS=key:actor:role,...`); the actor is always the key's actor.

| role | create / set / note | resolve | `/admin/*` |
|---|---|---|---|
| `officer` | yes | no (403) | no |
| `supervisor` | yes | yes | no |
| `admin` | no (403) | no | yes (when `ENABLE_ADMIN=1`) |

The table lives in `region/trust/roles.py`; an unknown role is denied everything. A carried or replicated
event was authorised by its origin at write time and is bound by that origin's signature; the carrier's own
role is not re-checked.

### 3.2 Endpoint table

| method + path | body | response / rule | state |
|---|---|---|---|
| `POST /v1/reports` | `{report_id?, op_id?, title, category, severity, location?, description?, people_estimate?}` | `201 {report, event, event_id, consistency}` | built (A) |
| `PATCH /v1/reports/{id}` | `{field, value, expected?, op_id?}` | `200` same body; 409 `stale_edit` when `expected` differs from the live set | built (A) |
| `POST /v1/reports/{id}/notes` | `{text, op_id?}` | `201` same body | built (A) |
| `GET /v1/reports` | `?limit=&cursor=` | array of report views, newest first, incomplete hidden | built (A) |
| `GET /v1/reports/{id}` | | report view | built (A) |
| `GET /v1/reports/{id}/history` | | array of every event of the report in order, each with `sig_status` and `carried` | built (A) |
| `POST /v1/reports/{id}/resolve` | `{field, value, candidates, reason, op_id?}` | supervisor only, `201` same body | built (A) |
| `GET /v1/review` | | open review items `[{report_id, title, field, receipt}]`, newest first | built (A) |
| `GET /v1/status` | | status view (section 4.4) | built (A mounts D's snapshot) |
| `GET /v1/health` | | `{region, ok: true}` | built (A) |
| `GET /healthz` | | `{region, ok, level, ts}`; no auth, no DB writes | contract; built by D |
| `POST /v1/carry` | `{events: [envelope, ...]}` | `{results: [{event_id, result: new\|dup\|rejected, reason?}]}` | contract; built by D |
| `POST /v1/events/has` | `{ids: [event_id, ...]}` | `{missing: [event_id, ...]}` | contract; built by D |
| `GET /internal/v1/sync/summary`, `/events`, `POST /push` | | peer replication; 401 unknown peer; 503 when link down | built (D) |
| `GET /admin/links`, `GET/POST /admin/links/{peer}` | `{mode: up\|down\|slow, delay_ms?, rate_bps?}` | needs `ENABLE_ADMIN=1` and a valid key | built (D) |

Reserved, not built yet (do not reuse): `GET /v1/metrics`, `POST /admin/config`, `POST /admin/burst`.

`/v1/carry` results use `reason` in `bad_hash | bad_sig | unknown_origin | shape | own_origin`. `own_origin`: an event of
THIS node that it does not hold is never imported (it would reuse a sequence number; it was committed before it was
acknowledged). It must stay disabled until signature verification is on. Any valid API key may call it: the event
signatures, not the carrier, are the protection. Accepted events go through the same idempotent `ingest()` as replication.

### 3.3 Writes: `op_id`, report ids, signed events, replays

- `POST /v1/reports` may name its own cart: `report_id` must match `^R-[0-9a-f]{8}$` (422 otherwise). A second CREATE for an
  existing id with a different `op_id` is `409 {"error": "report_exists"}`. Without a `report_id` the server makes `R-<region>-<seq>`.
- Every 2xx body is `{"report": <view>, "event": <the full signed envelope>, "event_id", "consistency"}`. The browser keeps `event`
  in its outbox until a peer has it; it is the exact envelope stored here and replicated to peers.
- A retried write (same `op_id`, or the `Idempotency-Key` header) never creates a second event: the node answers **200** with
  the original event and the header `Idempotent-Replay: true`. This also holds when the original event arrived here by
  replication or carry. The replay check runs before the stale-`expected` check.
- The same `op_id` with a different body is `409 {"error": "op_id_reuse"}`.
- Events sharing an `op_id` count as one write in `derive()` (Lane B's op_id group rule).

### 3.4 Resolve and review

`POST /v1/reports/{id}/resolve` (supervisor) writes a `RESOLVE` event (priority 0) that supersedes exactly the live
candidates. `409 stale_edit` carries the `live` ids when the candidates changed; `409 no_conflict` when the field has
nothing to settle. A write that never saw the resolution is concurrent with it and opens a new review item: a resolution
never hides an edit it did not see. A receipt is:

```json
{"field": "status", "relationship": "CONCURRENT", "rule": "STATUS_MOST_ACTIVE", "policy_version": 1,
 "provisional": "assigned", "needs_review": true, "flags": [],
 "candidates": [{"event_id": "mum:4", "value": "closed", "origin": "mum", "actor": "agent-mum-1", "wall": "..."}]}
```

The engine's own receipt is passed through untouched when it has `candidates`; otherwise the API builds this shape from
the stored events (`region/api/receipts.py`), so consumers see one shape on any engine version. Older keys (`values`,
`event_ids`) are kept.

A CLOSE_VS_NEW_INFO receipt has the same shape: `relationship` stays `"CONCURRENT"`, `rule` and `flags` carry
`"CLOSE_VS_NEW_INFO"`, `field` is `"status"`, and `candidates` lists the close first, then every change it did not see.
A candidate's `value` is the status for the close, the note text for a NOTE, and the number for a severity write.
Candidates carry no `kind` or `field`: a client tells them apart by `rule` and by position, not by extra keys.

### 3.5 `observed` (CLOSE_VS_NEW_INFO evidence)

A status write (SET or RESOLVE) to `closed` or `cancelled` lists the NOTE and live severity event ids its node had.
A NOTE or a severity write lists the live status event ids its node had. Every other event has `observed: null`.
The engine raises the review item when a live close `c` and a NOTE / severity ≥ 4 write `i` exist where neither
lists the other; the check uses only the event set, so it is identical on every node.

### 3.6 Priority

0 critical: severity ≥ 4 on CREATE or SET, status → resolved/closed/cancelled, a fulfilment centre assigned, any
RESOLVE · 2 bulk: NOTE · 1 normal: everything else. The write path stores the result on the event; the UI must
show the stored value, not recompute it.

## 4. Responses the UI depends on

See `fixtures/report.json` and `fixtures/status.json` (generated by `python scripts/gen_fixtures.py`).

### 4.0 Report `consistency` block

```json
"consistency": {"replication": "PARTIAL", "confirmed_by": ["pun"], "awaiting": ["del"], "stale_possible": true}
```

- `confirmed_by`: peers whose last summary shows they hold every event of this report. `awaiting`: the other
  configured peers. Both are sorted by region id.
- `replication`: `LOCAL_ONLY` (no peer confirmed, or no peers), `PARTIAL` (some), `REPLICATED` (all).
- `stale_possible`: any configured peer is unreachable right now, so a newer edit may exist that this
  region cannot see. Shown even for a report every reachable peer has confirmed.

### 4.1 Status `level` (degradation ladder)

`level` is one of `schemas.LEVELS`:

| level | meaning | produced |
|---|---|---|
| `HEALTHY` | every peer reachable, heads equal | now |
| `SYNCING` | peers reachable, heads differ | now |
| `ISOLATED` | a peer failed 3 times or was silent for 5 s; writes still accepted | now |
| `CONSTRAINED` | link is up but measured slow; P0/P1 first, P2 deferred (never dropped) | by the sync lane |
| `RECOVERING` | link just came back; priority catch-up in progress | by the sync lane |
| `OVERLOADED` | write queue at its limit | by the sync lane |

Clients must treat an unknown `level` as "unknown", never as healthy. A browser that cannot reach a node shows it
as UNKNOWN: that is a UI state, not a node level.

### 4.2 Data at risk

`risk` is always present and computed by the region: `unreplicated` = events of THIS region whose seq is above every
peer's acknowledged head for this region (what a crash right now would lose unless a browser outbox carries it);
`critical_unreplicated` = the subset with `priority == 0`; `oldest_age_ms` = age of the lowest-seq one (0 when none).
A snapshot from the sync lane overrides the computed value.

### 4.3 List ordering and paging

`GET /v1/reports` is ordered newest first by the CREATE event's `(hlc, origin, seq)`, so every region returns the
same order for the same event set. `cursor` is an integer offset; `limit` is clamped to 1..500; negative values are 422.

### 4.4 `GET /v1/status`

```json
{"region": "mum", "level": "HEALTHY", "policy_version": 1, "schema_version": 1,
 "heads": {"mum": 17, "pun": 9, "del": 0}, "event_count": 26,
 "peers": {"pun": {"link": "up", "reachable": true, "fails": 0, "last_ok_s": 0.4, "last_ok_ms": 412,
                   "their_heads": {"mum": 17, "pun": 9}, "acked": {"mum": 17, "pun": 9}, "last_error": null}},
 "event_digest": "9f3a..", "state_hash": "c1d4..", "quarantined": 0, "incomplete_reports": 0,
 "risk": {"unreplicated": 0, "critical_unreplicated": 0, "oldest_age_ms": 0},
 "queues": {"P0": 0, "P1": 0, "P2": 0},
 "carry": {"received": 0, "new": 0, "dup": 0, "rejected": 0}}
```

`queues` is `null` until the sync lane supplies it; `carry` is the node's carry counters (since it started). Consumers must
handle `null`. `acked` repeats
`their_heads` under the v1.1 name. The global badge is computed by the client from every region's status:
CONVERGED only when every configured region is reachable and all report the same `event_digest`, `state_hash` and
`policy_version`; UNKNOWN if any is unreachable; INCOMPATIBLE if policy versions differ; DIVERGED if heads are equal
but hashes differ (a bug alarm).

## 5. Behavioural rules everyone relies on

- A write never waits for, or calls, another region.
- A 2xx is returned only after the SQLite commit (`synchronous=FULL`).
- One Uvicorn worker per region.
- Every event is signed by its origin inside the write transaction; a node never signs another node's events.
- Replication is idempotent: applying the same page twice changes nothing; the same `op_id` is the same write.
- A received or carried event is checked for shape, hash, signature and identity only; never re-validated against business rules.
- One link switch blocks both directions (outbound pull skipped, inbound `/internal` returns 503).
- Keys: each region keeps `keys/<region>.key` (git-ignored) and publishes `keys/<region>.pub`; the committed registry is
  `region/trust/public_keys.json`. A region id cannot silently take a different key.

## 6. Cross-origin access

Regions allow `CORS_ORIGINS` (default `http://localhost:5173`, `http://127.0.0.1:5173`, `:4173`) with the headers
`X-Api-Key`, `Content-Type`, `Idempotency-Key`, `X-Admin-Key`, and expose `Idempotent-Replay`, `Retry-After`,
`X-RateLimit-Limit` and `X-RateLimit-Remaining`.

## 7. Policy v1 (the defined conflict-resolution policy; implemented in the engine by Lane B / Lane C)

| field | concurrent rule | review |
|---|---|---|
| title, description, category | latest by (hlc, origin, seq) | no |
| severity | highest | receipt only |
| status | most active: open < assigned < resolved < closed = cancelled; lowest wins, latest breaks ties | yes |
| assigned_team | earliest by tie-break order; `DOUBLE_DISPATCH` when two different teams | yes |
| location | latest; review if candidates are 200 m or more apart | if far |
| people_estimate | highest, never summed | yes |
| notes | union by event id | n/a |
| CLOSE_VS_NEW_INFO | a live close and a NOTE / severity >= 4 write that did not observe each other; status stays as derived | yes |

Events sharing an `op_id` are ONE write: superseding one member supersedes all, one member stays live, one note is shown.
Events without `observed` (older nodes) never raise CLOSE_VS_NEW_INFO. Keys: `keys/<region>.key` (private, git-ignored) and
`region/trust/public_keys.json` (registry, generated locally for development, git-ignored; `scripts/gen_keys.py`).
Peers authenticate with `X-Peer-Token`, so an unsigned peer event is still accepted today and any signature present is verified.

## 8. v1.2 additions: scaling, rate limiting, identity claims

Frozen on 2026-10-13 so four lanes can build against it at the same time. Everything here is off by default
(limits 0, power `medium`, sign-in off), so a region that sets nothing behaves exactly as in v1.1.

### 8.1 Settings (environment, per region)

| name | default | meaning | built by |
|---|---|---|---|
| `RATE_ACTOR_BURST` | 0 (off) | writes one actor may make at once: the size of its token bucket | Lane A |
| `RATE_ACTOR_REFILL_PER_S` | 1 | tokens added to each actor's bucket per second | Lane A |
| `REGION_WRITE_LIMIT_PER_S` | 0 (off) | writes this region accepts per second from clients | Lane D |
| `POWER` | `medium` | writes saved per batch: `low` 1, `medium` 8, `high` 32 | Lane D |

Runtime change: `POST /admin/config` accepts any of `{"rate_actor_burst", "rate_actor_refill_per_s",
"region_write_limit_per_s", "power"}` and answers with the full current set. An unknown key is 422.
The per-actor limiter reads `config.settings.rate_actor_burst` and `config.settings.rate_actor_refill_per_s` when
those attributes exist, so `/admin/config` only has to update them; until then it reads the environment variables.

### 8.2 Fourth region

Id `blr`, name Bengaluru, port `8004`, database `data/blr.db`. It is in the port table but not started by default.

### 8.3 Refusals

```
429  {"error": "rate_limited", "scope": "actor", "retry_after_ms": 800}
     headers: Retry-After (whole seconds, at least 1), X-RateLimit-Limit, X-RateLimit-Remaining
503  {"error": "overloaded", "scope": "region"}
     header: Retry-After
403  {"error": "claim_required", "claim": "age_over_18"}
```

- Reads are never limited. Peer replication (`/internal`) and `POST /v1/carry` are never limited.
- A replay of an `op_id` already stored is answered before the limiter: it is never refused and never counted.
- The actor limit is checked after authentication (and the role check) and after the replay check, before anything
  else in the write path. Each write that reaches it costs one token, whatever its outcome; a refused write stores
  nothing and uses no sequence number. Requests that fail validation (422) never reach it.
- Every accepted write (and every replay) carries `X-RateLimit-Limit` and `X-RateLimit-Remaining` while a limit is set.
- The actor is the API key's actor today, so two clients that share one key share one allowance. When sign-in is on
  it becomes the credential (section 8.5).
- Buckets live in each region's memory. A client that moves to another region starts with a full allowance there:
  this bounds the load on each region; it is not a global quota. A restart empties them.
- `overloaded` is not a failure for a client that can reach another region: it should send the write there (same
  `op_id`) and avoid the limited region until `Retry-After` has passed.

### 8.4 Status additions (`GET /v1/status`)

```json
"load": {"writes_per_s": 3.2, "power": "medium", "batch": 8, "queue_depth": 0},
"rate": {"actor_burst": 10, "actor_refill_per_s": 1, "region_limit_per_s": 0,
         "refused_actor": 0, "refused_region": 0}
```

Both blocks may be absent (a v1.1 region): consumers must handle that. `region.api.ratelimit.snapshot()` returns the
actor part of `rate` (`actor_burst`, `actor_refill_per_s`, `refused_actor`) for the status builder.

### 8.5 Identity claims (Lane C provides, the others only call)

```
region/trust/claims.py
  subject_key(auth) -> str             credential id when signed in, else the API-key actor. The rate limiter's key.
  has_claim(auth, claim: str) -> bool  "age_over_18" | "member". Always False in API-key mode.
  is_revoked(credential_id: str) -> bool
```

- Event provenance (already present when sign-in is on): `event["credential_id"]`.
- An event whose `credential_id` is revoked is rejected by ingest and carry with the reason `revoked_credential` (Lane B).
- A restricted product: the note JSON carries `"requires": "age_over_18"`; the write is refused with the 403 above
  unless the session has proven that claim.
