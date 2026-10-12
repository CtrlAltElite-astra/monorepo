# SyncShield — frontend-new

New reference-led landing page and live shopping console. Existing `web/`, `dashboard/`, `frontend 2/`, and backend files remain independent.

## Local use

```powershell
cd frontend-new
npm install
npm run dev
```

- Landing: http://localhost:5175
- Live demo: http://localhost:5175/demo
- Evidence: http://localhost:5175/demo?panel=prove
- Explicit offline simulator: http://localhost:5175/demo?mode=mock

The default is **live**, never an automatic fallback to simulated data. The Vite server proxies `/api/mum`, `/api/pun`, `/api/del`, and `/chaos` to existing services on ports 8001, 8002, 8003, and 9000. Copy `.env.example` to `.env` to change targets or the development keys.

Start the existing backend services from `monorepo` if they are not already running:

```powershell
.\.venv\Scripts\python.exe scripts/run_regions.py mum pun del
```

In another terminal, enable the existing crash/restart controls:

```powershell
$env:REGIONS = 'mum,pun,del'
.\.venv\Scripts\python.exe scripts/chaos_agent.py
```

Do not start duplicate processes on occupied ports. Crash/restart and proof controls operate on the real configured backends. The proof creates a proof cart, changes links temporarily, then heals them. "Reset session" clears client counters/session state; it does not erase regional databases.

## Structure and features

- `src/pages/Story.tsx`: the animated landing page: a headline that reveals line by line, packets travelling along the replication paths, blinking server lights, a bobbing "saved" card, scroll reveals, and a navigation that settles into a shadow. The hero illustration is explicitly labelled; it is not live status.
- `src/pages/Console.tsx`: the 30/70 split. The **left side is one smooth-scrolling column** (header, shoppers, product rows, cart); a small "View cart" pill scrolls to the cart while it is out of sight. The right side is the live backend.
- `src/components/Shop.tsx`: six products as compact rows with small photographs, five shoppers, the cart, delivery speed, fulfilment, checkout, simulated payment, order, cancel and history. Every tap is a real write.
- `src/components/LiveSystem.tsx`: evidence tiles, the browser-to-region route, **three region cards** (events, carts, at risk, the shopper's cart as that region holds it, the state digest), replication link pills, the latest action as three steps, the event trail, and the pause/resume controls. Each card has its own **Hold to crash** / **Restart** button.
- The inspector keeps Carts, Network (up/slow/cut, burst, tamper), Review (supervisor decisions), Outbox, Security, Metrics, API and Prove it. The Inspect view shows the same region cards above the tabs.
- `src/sdk`, `src/api`, `src/state` and the domain logic come from `web/`: failover, idempotency keys, signed-event carrying, and the original tests. A write is only finished once the page has re-read the node, so the next action never sends stale `expected` ids.
- Styles: `syncshield.css` (inspector panels and the landing), `console.css` (shell, shop, region cards; readable, commented), `motion.css` (status lights, crash and heal animations, buttons, reveals, landing motion). Type scale: body 16 px, secondary 15 px, labels never below 13.5 px.

### Status lights and the crash animation

A region's light shows what the browser actually knows: steady green with a slow ring when healthy, amber when it is catching up or cannot reach its peers, an amber spinner while it restarts, and red when it is down (solid for two seconds, then blinking). While you hold the crash button the card's border turns red and a bar fills; on release the card shakes and flashes, the light goes fast-red and then blinks. When a region comes back and reports HEALTHY the light turns green with a ring and the card pulses once.

### Which region serves a request

There is no server-side load balancer: the browser SDK (`src/sdk/syncshieldClient.ts`) chooses. It checks every region's `/healthz` twice a second and sends each write to the first region that answers, in a **fixed order: Mumbai, then Pune, then Delhi** (`order` option). While Mumbai is up it serves; the serving region saves the write, then replicates it to the others (pushed at once, and pulled again about every second). If Mumbai stops answering, the browser moves to Pune, handing over the signed events it still holds first; if Pune is also down, to Delhi. Speed only breaks ties between regions that are not in the list. A region that has just come back is not trusted at once: it must answer three checks in a row (about 1.5 s) before it serves again. The route line on the right says why a region is serving, for example "Serving from Pune · Mumbai is down".

(Before this order was fixed, survivors were ranked by measured ping. On one machine Pune and Delhi differ by a millisecond of noise, so the failover target changed from run to run.)

### Motion

Animation is on by default. The **Motion** switch in the navigation turns it off (and back on) and remembers the choice. The browser's own "reduce motion" setting is deliberately not a silent kill switch here: with it, the whole page used to be static with no way to turn animation back on.

All counts and confirmations come from live responses or this browser's session. Session counters start at zero and differ from the region's persisted event count. An unreachable region is shown as offline or unknown, never healthy. Missing optional metrics/fuzz results remain explicitly unavailable. The SDK recovery outbox is in memory and disappears when the tab closes; it is not durable offline storage.

## Validation

```powershell
npm run typecheck
npm test             # 57 tests: derive, the simulated world, the SDK (failover, outbox, idempotent retries)
npm run build
```

Last full run (13 October 2026): typecheck, 57 tests and the production build pass. A browser run against **three isolated real
backends** (throwaway databases and signing keys on ports 18101-18103, so the demo data on 8001-8003 is untouched) passed every stage:

1. a real signed cart created on the region the browser was actually serving from, then replicated to the other two;
2. delivery speed, fulfilment centre and a signed audit history;
3. rapid switching between all five shoppers: no tap dropped, and the "Added" feedback stays with the shopper who tapped;
4. replication paused on every link, a write acknowledged by one region, that region killed (a real process kill), the browser
   failing over and handing over the signed event it held, restart, links healed, all three regions converged;
5. every Inspect tab, the signature check on the history, three forged events rejected, the concurrent-write proof;
6. a supervisor resolving a real conflict (the order versus a new item), then checkout, payment state and order placement;
7. a 390 px phone width with no horizontal overflow and no browser JavaScript errors.

The harness is `.local/integration.cjs` (git-ignored, machine-specific Playwright path); run it from this folder with the dev server
on port 5175. It reads which region is serving and crashes that one, so it does not assume Mumbai.

What this does not show: it is a local, single-machine demonstration. Payment and order steps are state changes, not real payments.
The recovery outbox is in memory and is lost when the tab closes.

## Assets

`public/products-atlas.png` is a project-local product atlas generated with the built-in image-generation tool. CSS displays each cell without editing or cropping the source file. Prompt: one 3×2 landscape ecommerce sprite atlas on consistent warm off-white studio backgrounds; top row kraft filter coffee pouch, stainless bottle, folded sage kurta; bottom row ivory earbuds, white desk lamp, graphite backpack; no brand logos, restrained product photography. The source is preserved in the generated-images library. `public/ASSETS.md` records the asset and generation details. Icons and server illustrations are code-native. Fonts use Google Fonts with local sans-serif fallbacks.
