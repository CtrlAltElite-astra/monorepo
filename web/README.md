# SyncShield web console

Vite + React 18 + TypeScript, no UI library. Light and dark themes, animated, usable at phone width.
Two pages: `/` tells the story (what fails, what the browser does, the four ideas, the limits) and `/demo`
is the console a judge drives: a shop on the left, three node cards, counters that must stay honest, and tabs
for carts, the partition lab, review, the outbox, security, metrics, the raw API and "Prove it".

## Run it

```bash
bash scripts/run_web.sh            # or: powershell scripts/run_web.ps1   (or: cd web; npm install; npm run dev)
```

| URL | what it is |
|---|---|
| http://localhost:5173/ | the story page |
| http://localhost:5173/demo | the console on the **simulator** (no backend needed) |
| http://localhost:5173/demo?mode=live | the console on the **real regions** (needs `python scripts/run_regions.py mum pun del`) |
| http://localhost:5173/demo?nodes=2 | start with two nodes, press "Join Delhi" to watch it catch up |

Live mode also wants `python scripts/chaos_agent.py` (with `REGIONS=mum,pun,del`) for "Hold to crash" and
"Restart". Without it the console says so and everything else still works. Settings: [.env.example](.env.example).

## How it is built

- **One API, two backends.** `src/api/` exposes a `Transport` (`request(region, method, path)`). `mockClient.ts` is
  an in-browser simulator of three nodes behind the same REST paths as the real ones; `liveClient.ts` is plain
  `fetch`. Nothing in the UI knows which one it has.
- **The simulator is a faithful copy of the rules, not a script.** `src/sim/derive.ts` is the policy v1 `derive()`
  (per-field rules, op_id groups, CLOSE_VS_NEW_INFO), `src/sim/world.ts` runs nodes, links, replication, the degradation
  ladder and kill/restart. Its receipts have the exact shape of the real engine (contract 3.4).
- **The browser SDK** (`src/sdk/syncshieldClient.ts`) is what makes failover zero-loss: it probes `/healthz`,
  retries with the same `op_id`, hands the signed events it holds (the outbox) to the next node before its first request
  there, prunes the outbox once a peer holds an event, and fails back. Failover counts both a retried call and a
  call that went straight to the next node because a probe had already found the old one dead.
- **Honest numbers.** Every figure on screen is taken from an API response held in `src/state/demo.tsx`.
  A node that does not answer is UNKNOWN, never green; the global badge says CONVERGED only when the event digests
  and state hashes are equal; "acknowledged = present on survivors" is measured by asking the surviving nodes.
- **"Prove it"** creates a cart, cuts one node off, changes the same cart on both sides (one side places the order, the
  other adds an item), heals, waits and compares digests. It writes to specific nodes directly because the SDK would
  send everything to one.

## Test it

```bash
cd web
npm run typecheck
npm test            # 57 tests: derive, the simulated world, the SDK (failover, outbox, idempotent retries)
npm run build
```

The browser scenarios used to verify the console (Playwright) are not part of the repo. What they check, in both
themes and at 390 px: add to cart and replication, kill the serving node under auto-shoppers (0 errors, nothing lost),
restart and converge, Prove it, the review queue and resolve (403 for an officer, success for a supervisor), burst,
tamper (three forged events, all rejected), join Delhi, every tab, and no horizontal scroll on a phone. The same flow
also passes against the real regions (`?mode=live`) with a real `kill -9`.
