# The joining steps (J0 to J7): what was done, what was found

Branch `scale-join`, 13 October 2026, on top of `main` with all four lanes merged (`cdf59a7`). Baseline before any change:
`pytest` 527 passed, 24 skipped; the 24 are the Node-only tests, which pass once `npm install` is run in `collaboration/true`.

| step | done | how it was checked |
|---|---|---|
| J0 | `region/main.py` calls `scale.install(app)` before the CORS middleware; `tests/integration/scale_app.py` now only re-exports the app (a second call would count every write twice) | `tests/integration/test_scale.py` on real processes; the dial and the region limit now work on a plain `region.main:app` |
| J1 | the console already pointed at `/admin/config` and `/api/blr`; verified on real regions. The chaos agent no longer lists Bengaluru as a peer of regions it starts while Bengaluru is not running, and tells the running regions about it when it joins | dial sets `status.load.batch` 1 / 8 / 32; Bengaluru joined empty and reached the same digest in 6.2 s |
| J2 | `bench_scale.py` run on the demo machine: no mode says "unsupported" | `docs/scale/scale-result.json`, copied to `frontend-new/public/` (the Scaling tab reads it) |
| J3 | the "Limit this region" switch works on real regions; the fairness properties now run against the real limiter (`RealLimiter` adapter in `tests/scale/test_limiter_properties.py`) | `pytest tests/scale` (10 passed); browser test: route moves to Pune, errors stay 0, replication into Mumbai continues |
| J4 | the console sends a bearer session in sign-in mode (never an API key); `IdentityHost` mounts Miti's `IdentityPanel` in the identity slot with automatic onboarding (a member's credential is issued the first time they sign in; no claim codes to paste), sign-in, sign-out and revoke; the shop header shows who is signed in; `region/api/routes_reports.py` calls `claims.require_claim` for a note that says `requires` (sign-in mode only); the adults-only item is shown in sign-in mode | `tests/convergence/test_join.py`; browser test (auth) |
| J5 | `ratelimit.key` returns `claims.subject_key(auth)` (one line) | unit test (two sessions of one credential share an allowance, another credential does not); browser test (a second window, same credential, is refused) |
| J6 | `/v1/carry` refuses a validly signed event whose credential was revoked (`revoked_credential`), quarantined, state unchanged; Tamper's fourth row in the console; the rule's reasoning is in `docs/conflict-policy.md` | unit test; browser test (four rows rejected, quarantine +4, state hash unchanged) |
| J7 | browser test `frontend-new/tests/browser/integration.cjs` (keys and auth modes); README, contract and changelog updated | see the README |

## Decisions that differ from the plan text, and why

1. **The revoked-credential rule runs on carry only, not on peer replication.** The plan says "where ingest and carry call it".
   Replication cannot apply it: a region that knows about a revocation would refuse an event its peers already hold, its
   watermark for that origin would stall behind the refused event, and it would never reach the others' digest. Carry is the
   untrusted browser channel the scenario is about. Cost (stated in the README): a browser cannot rescue an acknowledged
   write of a member who was revoked meanwhile.
2. **A claim check only in sign-in mode.** In API-key mode there are no claims, so a note that says `requires` is stored as before;
   the console does not offer the restricted item then.

## Defects found while joining (and fixed)

- `region/api/routes_auth.py` built a new HTTP client for every call to the sidecar. That loads the TLS certificate store
  (300 to 500 ms of CPU on Windows) and froze the region's whole event loop on every sign-in and every `/v1/auth/status`, so health
  probes timed out and the console saw a busy region as down. One client per event loop now (a regression test pins it), with a
  2-second idle keep-alive so a connection the Node sidecar has just closed is not reused.
- The console detected sign-in mode only through a region that answered its report poll; in sign-in mode that poll is refused
  until someone has signed in, so the identity section never appeared. It now asks every region's public `/v1/auth/status`.
- A credential claimed a moment ago is "not active" in the regions' status list until their next refresh (about 5 s); the identity
  section retries a few times before reporting it.
- The load generator built a new HTTP client for every request (about 300 ms each); it now keeps one per worker thread.
- `npm run build` in `frontend-new` stopped at `vite.config.ts` for want of `@types/node`; added as a dev dependency.

## Not done

- Adding Bengaluru at runtime in sign-in mode: the call that tells running regions about a new peer needs an admin session. Start
  all four regions together there.
- The 5 s revocation window is the sidecar's refresh interval; a revoked member is accepted until the region's next refresh.
