# Lane B (Rahil): hand-off

1. **Built:** `scripts/bench_scale.py` (HTTP-only benchmark: `--vertical`, `--horizontal`, `--compare`, writes
   `docs/scale/scale-result.json` and copies it to `frontend-new/public/scale-result.json`); the
   `revoked_credential` admission rule in `region/engine/admit.py` (`reject_reason(..., is_revoked=)`, default
   never revoked); fairness properties of the per-actor limiter against a reference bucket
   (`tests/scale/`); and a "revoked actor keeps sending" scenario in F1.
2. **Run:** `pytest tests/engine tests/fuzz tests/scale` and, with regions up,
   `python scripts/bench_scale.py --vertical --horizontal --compare` (add `--url region=url` per region).
3. **Rule, one line:** a region that has not yet heard of a revocation may still sign a write from that
   credential, so every node that knows the credential is revoked refuses the event (`revoked_credential`),
   after the signature check, and the signature binds the `credential_id` so no carrier can strip it.
4. **Measured** (13 Oct 2026, 04:22 IST; Windows 11, AMD64 Family 23 Model 24, 4 logical CPUs, Python 3.14; all
   regions on this one laptop, main at `f765cc3`, 300 add-to-cart writes, 8 concurrent): horizontal at the
   default batch, 1 / 2 / 3 regions = 59.1 / 84.8 / 92.8 writes/s (p95 178 / 159 / 153 ms); a repeat run gave
   59.0 / 77.3 / 97.4. 0 refused, 0 errors. F1: 236 examples, 918 revoked-credential events refused, no divergence.
5. **Not finished (joining phase):** vertical and compare are recorded as `"unsupported"` because today's
   `/admin/config` does not take `power` (lane D); re-run the benchmark after lane D merges. Wire
   `is_revoked=claims.is_revoked` into `region/sync/carry.py` and `ingest.py` (lane D files) once lane C's
   `claims.py` lands; point `make_limiter()` in `tests/scale/test_limiter_properties.py` at lane A's
   `region/api/ratelimit.py`; add the rule's reasoning to `docs/conflict-policy.md`; README fuzz figures (211)
   are now 236 (README is not a lane B file in this plan).
