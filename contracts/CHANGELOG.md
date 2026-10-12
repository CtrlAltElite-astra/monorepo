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
