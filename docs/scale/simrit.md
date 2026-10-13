# Lane D (Simrit): hand-off note for the scaling plan

**Built.** Settings and `POST /admin/config` (four runtime keys, 422 on anything else, nothing applied on a bad
body); `region/sync/groupcommit.py` (power = writes saved per transaction: low 1, medium 8, high 32);
`region/sync/scale.py` (region write limit that answers 503 `overloaded` + `Retry-After`, load meter, the `load` and
`rate` status blocks); a `blr` region on :8004; a peer list that is re-read every sync round, so a region added at
runtime is pulled from and pushed to without a restart; `scripts/loadgen.py` with `--actor`, `--spread`, real
concurrency; an `OVERLOADED` level for a node that refused a write in the last 2 s.

**Run it.** `python -m uvicorn tests.integration.scale_app:app` starts a region with the hook (see below). Dial it:
`curl -X POST :8001/admin/config -H "X-API-Key: dev-admin" -H "Content-Type: application/json" -d '{"power":"high"}'`.
Spam as one shopper: `python scripts/loadgen.py --actor Bob -n 40` (each shopper has a key, `dev-bob`, from
`scripts/run_one.py`). Split across regions: `python scripts/loadgen.py --spread mum,pun,del -n 300 -c 12`.
Bengaluru: `python scripts/join_region.py blr` (needs the others running; the chaos agent needs `REGIONS=mum,pun,del,blr`).

**Joining steps (done in `scale-join`, see `docs/scale/join.md`; the list below is what this lane asked for).**
1. `region/main.py` (A): add `from region.sync import scale` and, **before** the CORS middleware is added, `scale.install(app)`.
   Until that line exists the region limit and the power dial do nothing; the settings, the status blocks and
   `/admin/config` already work. The tests start regions through `tests/integration/scale_app.py`, which is exactly that line.
2. `contracts/fixtures/status.json` (A): I added the `load` and `rate` blocks (an exact-key test needs them). Lane A's
   contract v1.2 should carry the same text; on merge keep one copy.
3. `region/api/ratelimit.py` (A) should read `config.settings.rate_actor_burst` / `rate_actor_refill_per_s` at call time and
   offer `snapshot()` returning `refused_actor`; `scale.rate_block()` already calls it when the module exists.

**Measured, this laptop only** (Windows, one machine, 300 writes to ONE region, loadgen): batching works: at 24
concurrent writers the number of commits fell from 300 (low) to 43 (medium) to 21 (high). Throughput did **not**
rise much: 65 / 61 / 69 writes/s (low / medium / high), and 74 w/s on the unmodified app. A p95 of about 3 s at 24
concurrent writers is also in the unmodified app, so the limit here is not the disk wait and the dial's visible effect
on this machine is small. Say so; do not present it as a speed-up. Sequential (1 writer): 150-175 w/s at any setting.

**Not finished / known limits.** Reads on the same SQLite connection can see a batch a few ms before its COMMIT.
`drop_pct` on a link is stored and still not applied. Region limit counts every write request that is admitted (also
ones that later fail validation); a replay is never counted. `region/sync/state.py` reports OVERLOADED only after a refusal,
not from queue depth alone. `REGION_WRITE_LIMIT_PER_S` is a 1-second window, not a smoothed bucket.
