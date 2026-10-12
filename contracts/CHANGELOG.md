# Contract changelog

- **v1 (2026-10-12)** initial freeze: event envelope, report fields, Basic API subset.
  - Status additionally exposes `event_digest`, `state_hash`, `quarantined`, `incomplete_reports`; peers expose `fails`.
- **v1 additive (2026-10-12)** degradation ladder and data-at-risk fields (no existing field changed).
  - `GET /v1/status` gains nullable `risk {unreplicated, critical_unreplicated, oldest_age_ms}` and `queues {P0, P1, P2}`; both are `null` until Lane B / Lane D populate them.
  - `level` may now also be `CONSTRAINED` or `RECOVERING` (`schemas.LEVELS`, contracts.md 4.1).
  - `GET /v1/reports` ordering is now defined (newest CREATE first by `(hlc, origin, seq)`); `limit`/`cursor` validated (contracts.md 4.3).
  - `schemas.py` gains `LEVELS`, `REPLICATION_STATES`, `RISK_KEYS`, `QUEUE_KEYS`.
- **v1 additive, PDF 2 Phase 1 (2026-10-12)** (no existing field changed or removed).
  - Report `consistency` gains `confirmed_by`, `awaiting`, `stale_possible` next to `replication` (contracts.md 4.0, `schemas.CONSISTENCY_KEYS`).
  - New `GET /v1/reports/{id}/history`: array of the report's events in `(hlc, origin, seq)` order (contracts.md section 3).
  - Roles enforced on `POST /v1/reports`, `PATCH /v1/reports/{id}`, `POST /v1/reports/{id}/notes`: officer and supervisor write, admin and unknown roles get 403 (contracts.md 3.1). A known key that was accepted before as any role may now be refused.
  - New `contracts/partner-contract.md` (proposed partner API) and `region/trust/adapter.py` (adapter interface).
- **v1.1 (2026-10-12, additive)** zero-loss failover and policy v1 (SyncShield plan v1.1).
  - Event envelope gains `op_id` (client operation id, null for server-made events) and `observed`
    (event ids the writer had seen; set only on close / new-info writes). Both are always present (nullable).
    Migration: `events.op_id` + index, applied automatically on start (`store._migrate_v11`).
  - `sig` is now filled: base64 Ed25519 over the ASCII hex `hash`, by the origin node's key.
  - 2xx write bodies also carry the full signed `event`. A replayed `op_id` returns 200 + `Idempotent-Replay: true`;
    same `op_id` with a different body is 409 `op_id_reuse`. `Idempotency-Key` header is accepted as an alias.
  - `POST /v1/reports` accepts an optional client `report_id` (`R-` + 8 hex); taken id is 409 `report_exists`.
  - New: `GET /healthz`, `POST /v1/events/has`, `POST /v1/carry` (hash + signature verified, own-origin events never imported).
  - `/v1/status` gains `carry {received,new,dup,rejected}`; `policy_version` is now 1.
  - Conflict receipts gain `relationship`, `candidates[]`, `provisional`, `policy_version`, `flags` (`values`/`event_ids` kept).
    New rules: `HIGHEST`, `STATUS_MOST_ACTIVE`, `EARLIEST_HLC` (+ `DOUBLE_DISPATCH`), `LATEST_HLC`, `CLOSE_VS_NEW_INFO`.
  - `CATEGORIES` gains `cart`.

