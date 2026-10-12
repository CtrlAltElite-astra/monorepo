# Contract changelog

- **v1 (2026-10-12)** initial freeze: event envelope, report fields, Basic API subset.
  - Status additionally exposes `event_digest`, `state_hash`, `quarantined`, `incomplete_reports`; peers expose `fails`.
- **v1 additive (2026-10-12)** degradation ladder and data-at-risk fields (no existing field changed).
  - `GET /v1/status` gains nullable `risk {unreplicated, critical_unreplicated, oldest_age_ms}` and `queues {P0, P1, P2}`; both are `null` until Lane B / Lane D populate them.
  - `level` may now also be `CONSTRAINED` or `RECOVERING` (`schemas.LEVELS`, contracts.md 4.1).
  - `GET /v1/reports` ordering is now defined (newest CREATE first by `(hlc, origin, seq)`); `limit`/`cursor` validated (contracts.md 4.3).
  - `schemas.py` gains `LEVELS`, `REPLICATION_STATES`, `RISK_KEYS`, `QUEUE_KEYS`.
