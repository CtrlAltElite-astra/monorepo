# SyncShield

**Zero-loss failover, safe conflict resolution, honest consistency.**
*"Regions can die. Shoppers won't notice. No add-to-cart is lost."*

CodeAstra 2.0 (RGIT) · Cloud Computing and Distributed Systems · PS2
Team: Akshit Sharma (A), Rahil (B), Miti Shah (C), Simrit (D)

> **How to edit this file.** Every section sits between `<!-- BEGIN:... -->` and `<!-- END:... -->`
> markers with an owner. Send your text as a PR touching only your own block; Miti resolves layout.
> Nothing here is described as implemented until its test passes (see *Status*).

<!-- BEGIN:problem owner=C -->
## Problem and approach

A data layer for apps that run in several regions. When a whole node dies or the network between
nodes breaks, the shopper's browser quietly moves to the next node, no acknowledged write is lost,
concurrent edits are settled by written safety rules (or a supervisor), and every screen says
honestly how far to trust what it shows.

"Disaster" here means any anomaly that interrupts the normal flow: a node crash, a network
partition, a slow link, a traffic spike, tampered data. The demo domain is an online shop
(carts, add-to-cart, checkout) running on a domain-neutral event engine.

| Claim we will measure | Evidence |
|---|---|
| 0 errors shown to shoppers while a node is killed | judge-panel counter, test T20 |
| 0 acknowledged add-to-carts lost | "acknowledged = present on survivors" counter, test T17 (kill -9) |
| No concurrent edit silently overwritten | conflict receipts, review queue, tests T4 T6 T7 |
| Status is never greener than the facts | UNKNOWN badge, CONVERGED only with equal digests, tests T1 T2 F1 |
<!-- END:problem -->

<!-- BEGIN:consistency owner=C -->
## Consistency model and defence

**AP with strong eventual consistency.** Any node accepts valid writes during a partition; two
nodes holding the same set of events derive identical state. Reads are local and may be stale; the
API says when. No consensus (Raft/Paxos): a cut-off node would have to refuse orders, and refusing
is the worse failure.

1. Every change is an **immutable event** (`CREATE`, `SET`, `NOTE`, `RESOLVE`) with a per-origin
   gap-free `seq`, a hybrid logical clock (tie-break only) and a content hash.
2. **`supersedes` pointers carry causality.** A write lists the field values it saw and replaces.
   Two writes to one field where neither replaced the other are *concurrent*, which is a conflict.
3. **`derive()` is a pure function of the event set.** Order, duplicates and relays do not matter.
4. **Replication = copying missing events**: pull by per-origin watermark (authoritative), push as
   a shortcut, and browser-carried signed events on failover. All three go through one idempotent `ingest()`.

Full write-up: [docs/consistency-model.md](docs/consistency-model.md).
<!-- END:consistency -->

<!-- BEGIN:architecture owner=C -->
## Architecture

![architecture](docs/architecture.png)

Each node is a full stack with its own SQLite file. The browser SDK talks to the preferred node and
fails over to the others; nodes replicate to each other; the chaos agent can kill any node.
Details: [docs/architecture.md](docs/architecture.md), shop mapping: [docs/shop-mapping.md](docs/shop-mapping.md).
<!-- END:architecture -->

<!-- BEGIN:policy owner=B -->
## Conflict policy (v1)

Applied only when the live values of a field disagree. Writes that saw the old value are updates, never conflicts.

| field | concurrent rule | review |
|---|---|---|
| title, description, category | latest by HLC order | no |
| severity (delivery speed) | highest | receipt only |
| status | most active wins (open < assigned < resolved < closed = cancelled), latest breaks ties | yes |
| assigned_team (fulfilment centre) | earliest by HLC order, flag `DOUBLE_DISPATCH` | yes |
| location | latest; review if candidates are 200 m or more apart | if far |
| people_estimate | highest, never summed | yes |
| notes (cart activity) | union by event id; one `op_id` is one write | n/a |
| close vs new information | status stays as derived; `CLOSE_VS_NEW_INFO` review item | yes |
| two supervisor decisions that differ | the field's rule again, flag `RESOLVE_VS_RESOLVE` | yes |

One line of reasoning per rule, the receipt shape and the accepted-event rule (shape, known origin,
unchanged hash, the origin's Ed25519 signature) are in [docs/conflict-policy.md](docs/conflict-policy.md).
Full contract: [contracts/contracts.md](contracts/contracts.md#6-v11-additions-implemented-additive).
<!-- END:policy -->

<!-- BEGIN:demo owner=C -->
## The demo (real nodes, nothing mocked)

The demo frontend is **`frontend-new/`**: a landing page and a live console. Everything on screen comes from a real region.

**The shop and the failover story**

- **Left, the shop.** One scrolling column: five shoppers, six products (plus an adults-only item in sign-in mode), the cart.
  Every tap is a real signed write. Items can be added and removed (a removal is a new event with quantity -1; nothing is
  deleted). Delivery speed, fulfilment centre, check out, pay, place order, cancel, and the cart history. After an order is
  placed or cancelled the next item starts a new cart.
- **Right, the live backend.** A region card per region (Mumbai, Pune, Delhi, and Bengaluru once it joins) with its status
  light, events stored, carts, events at risk, writes per second, and its own copy of the selected shopper's cart. Evidence
  tiles (acknowledged, recovered, not on survivors), the latest action as three steps (saved, signed, peers confirmed),
  an event trail, and the replication links.
- **Break it.** Pause replication, then hold **Hold to crash** on a region card: the process is really killed. **Restart**
  brings it back: amber while it catches up, then green.
- **Failover.** A shopper's writes go to their **home region** first (Alice and Bob Mumbai, Chitra and Dev Pune, Esha Delhi),
  then to the others in a fixed order. When a region dies, or answers "over my limit", the browser first hands the survivor the
  signed events it still holds, so no acknowledged write is lost. A region that comes back must answer three checks in a row.
- **Inspect.** Carts per region, Network, Review (a supervisor resolves a conflict), Outbox, Security, Metrics, API,
  Scaling, and **Prove it** (cut a region off, change the same cart on both sides, heal, compare digests).

**Scaling and rate limiting (all real, all off by default; contract v1.2)**

- **Limit each shopper** (Network tab: Off / 5 / 10 / 20): a token bucket per shopper. A "requests left" meter sits under each
  shopper; **Spam as <shopper>** empties only that shopper's meter, the region answers `429`, and the console shows a calm
  notice, not an error. In sign-in mode the bucket follows the verified person (credential), not the device.
- **Limit a region** (Network tab): a region above N writes a second answers `503 overloaded`; the browser moves the write to
  the next region ("Serving from Pune · Mumbai is at its write limit"), errors shown to shoppers stays 0, and replication
  into the limited region keeps working.
- **Power dial** on each region card (low / medium / high = 1 / 8 / 32 writes saved per transaction): real group commit.
  It cuts commits sharply; on one laptop it barely changes throughput, and the console shows what the machine did.
- **Add Bengaluru**: starts a fourth region empty; the others add it without a restart and it reaches the same digest.
- **Burst 300 / Burst 1000 / Spread 1000** write real cart notes and time how long every region needs to agree.
- **Scaling tab**: the measured comparison from `scripts/bench_scale.py` with its "measured at" time; "not measured yet" when
  the file is missing. The simulator's numbers (`?mode=mock`) are modelled and labelled as such.

**Sign-in mode (opt-in, the partner team's TRUE credential)**

With the backend in sign-in mode the console shows an **identity section**: paste the claim codes, sign in with the wallet
(a real SD-JWT presentation; the wallet's key never leaves the browser), and see *what the shopper holds* next to *what the
shop received*. The **adults-only item** is refused with `403 claim_required` and nothing is stored until the shopper proves
"over 18" (only that one attribute is disclosed). After a crash the next add succeeds on another region **with no new
sign-in**. **Tamper** gains a fourth row: an event validly signed for a shopper whose credential is then revoked is refused by
the region that knows, quarantined, and changes no state.

Three-minute walkthrough: add items -> pause replication -> add one more -> hold **Hold to crash** on the serving region ->
watch the browser move to the next region with 0 errors -> **Restart** -> resume replication -> **Prove it**. Then: limit
Mumbai and press **Spam as Alice** (the route moves to Pune), **Add Bengaluru**, and the dial.

`web/` is the earlier console with the same SDK (port 5173) and `dashboard/` is the first dashboard, which also has the
opt-in wallet sign-in. Screenshots of the current UI are still to be added (see [later.md](later.md)).
<!-- END:demo -->

<!-- BEGIN:setup owner=C -->
## Stack and setup

Python 3.11+, FastAPI, SQLite (WAL, one file per node), httpx, React + Vite + TypeScript. Needs Node 18+ (Node 24+ for the
sign-in mode).

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows   (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt   # add -r requirements-auth.txt for the optional sign-in mode and its tests

# 1. the crash controller; it also starts the regions (Bengaluru is listed but only joins when you add it)
set REGIONS=mum,pun,del,blr       # PowerShell: $env:REGIONS='mum,pun,del,blr'   bash: export REGIONS=mum,pun,del,blr
python scripts/chaos_agent.py     # :9000 (dev only)
# then start each region once:   curl -X POST -H "X-Admin-Key: dev-admin" http://127.0.0.1:9000/start/mum   (pun, del)
# or, without crash controls:    python scripts/run_regions.py mum pun del

# 2. the demo frontend
cd frontend-new
npm install
npm run dev                       # http://localhost:5175  (console: /demo, simulator: /demo?mode=mock)
```

| what | port | notes |
|---|---|---|
| mum / pun / del / blr | 8001 / 8002 / 8003 / 8004 | `data/<region>.db`, signing keys in `keys/` (both git-ignored, created on first start); `blr` (Bengaluru) is not started by default |
| chaos agent | 9000 | kills and starts region processes for the UI; pid files in `run/` |
| `frontend-new` | 5175 | proxies `/api/<region>`, `/chaos`, `/issuer`, `/relay`; settings in `frontend-new/.env.example` |
| `web` (earlier console) | 5173 | `bash scripts/run_web.sh` or `cd web; npm run dev` |
| sign-in mode: issuer / sidecars / revoke relay | 4001 / 4101-4103 / 4090 | only when `AUTH_ENABLED=true` (see below) |

Dev keys on every region: `dev-officer`, `dev-supervisor`, `dev-admin`, and one per shopper (`dev-alice`, `dev-bob`, `dev-chitra`,
`dev-dev`, `dev-esha`). For the per-shopper meters to count each shopper separately, start the console with
`VITE_SHOPPER_KEYS=Alice=dev-alice,Bob=dev-bob,Chitra=dev-chitra,Dev=dev-dev,Esha=dev-esha`; without it every shopper shares one
allowance. All settings come from environment variables (see `.env.example`), and the scaling ones can be changed live with
`POST /admin/config` (the console does exactly that). Run **exactly one** Uvicorn worker per node: sequence numbers are
guarded by an in-process lock. To reset the demo, stop the regions and delete `data/*.db*`.

### Sign-in mode (TRUE credentials)

```bash
cd collaboration/true && npm install                  # once, Node 24+
pip install -r requirements-auth.txt                  # once (PyJWT)
python scripts/true_stack.py up --provision           # issuer :4001, sidecars :4101-4103; prints one-time claim codes
python scripts/true_stack.py relay                    # optional: lets the console's "Revoke this shopper" button work
set AUTH_ENABLED=true                                 # then start the chaos agent / regions in that shell
python scripts/chaos_agent.py                         # (regions it starts pick up their sidecar settings)
cd frontend-new && set VITE_AUTH_ENABLED=1 && npm run dev   # paste the claim codes into the identity section
```

Start **all** the regions you want before using the console in this mode: adding a region at runtime needs an admin session.

### Run the browser test

`frontend-new/tests/browser/integration.cjs` drives the real console in headless Chrome against real regions on a clean `data/`
(one-off: `npm install --no-save puppeteer-core` in `frontend-new`). `node tests/browser/integration.cjs keys` checks spam as one
shopper, limiting a region (the route moves, errors stay 0), the power dial, and Bengaluru joining. `... auth` (stack in sign-in
mode, claim codes saved in `frontend-new/.local/codes.txt`) checks the restricted item, a denied write, the person-keyed
limit, a crash without signing in again, and the four-row Tamper. Each `auth` run needs fresh claim codes (each works once).
<!-- END:setup -->

<!-- BEGIN:api owner=A -->
## API

Header `X-API-Key`. Dev keys: `dev-officer` (writes), `dev-supervisor` (also resolves conflicts), `dev-admin`
(partition lab and runtime settings). Reads need no key. In sign-in mode the header is `Authorization: Bearer <session>` instead. Full contract: [contracts/contracts.md](contracts/contracts.md).

| method + path | purpose |
|---|---|
| `POST /v1/reports` | create a cart/report, `201` only after commit |
| `PATCH /v1/reports/{id}` | set a field; `expected` ids give `409` if stale |
| `POST /v1/reports/{id}/notes` | add a note (cart item) |
| `GET /v1/reports[/{id}]` | view with `state`, `live`, `conflicts`, `consistency` |
| `GET /v1/reports/{id}/history` | events of the report in order, with `sig_status` and whether a browser carried it |
| `GET /v1/review` | conflicts that need a decision, each with its receipt (candidates, rule, policy version) |
| `POST /v1/reports/{id}/resolve` | supervisor only: `{field, value, candidates, reason}`; `403` for an officer, `409 stale_edit` / `no_conflict` |
| `GET /v1/status` | level, heads, peers, digests, carry counters |
| `GET /healthz` | liveness + level, no key |
| `POST /v1/events/has` | `{ids}` -> `{missing}` |
| `POST /v1/carry` | a browser hands over signed events from a dead node (hash + signature verified) |
| `GET /internal/v1/sync/{summary,events}` · `POST .../push` | peer replication (503 when link down) |
| `POST /admin/links/{peer}` | `{mode: up\|down\|slow, delay_ms, rate_bps}` partition lab |
| `POST /admin/config` | runtime settings: `rate_actor_burst`, `rate_actor_refill_per_s`, `region_write_limit_per_s`, `power`, `add_peer`; unknown or invalid values are `422` and nothing is applied |

Writes accept an optional `op_id` (or `Idempotency-Key` header): the same operation is applied once, and a replay returns 200 with `Idempotent-Replay: true`. 2xx bodies carry the full signed `event`.

Refusals that are answers, not failures: `429 rate_limited` (the shopper's own limit; `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`), `503 overloaded` (the region's write limit), `403 claim_required` (sign-in mode: an item whose note says `"requires": "age_over_18"`). Reads, `op_id` replays, carry and peer replication are never limited. `GET /v1/status` also carries `load` (writes per second, power, batch, queue depth) and `rate` (the limits and how many writes were refused).

### Try it (`python scripts/run_regions.py`, two nodes)

```bash
M=http://127.0.0.1:8001; P=http://127.0.0.1:8002; J='Content-Type: application/json'

# create a cart: a client-chosen id plus an op_id means a retry can never double it
curl -s -X POST $M/v1/reports -H 'X-API-Key: dev-officer' -H "$J"   -d '{"report_id":"R-0a1b2c3d","op_id":"create-1","title":"Alice","category":"cart","severity":1}'

# add an item (a note); sending this exact request again returns 200 + Idempotent-Replay: true and writes nothing
curl -s -X POST $M/v1/reports/R-0a1b2c3d/notes -H 'X-API-Key: dev-officer' -H "$J"   -d '{"op_id":"add-1","text":"{\"kind\":\"ADD\",\"sku\":\"SKU-1\",\"name\":\"Kolhapuri sandals\",\"qty\":1,\"price\":1299,\"user\":\"Alice\"}"}'

curl -s $P/v1/reports/R-0a1b2c3d            # the same cart, read from the other node

# cut the link both ways; Mumbai places the order, Pune adds an item; heal
curl -s -X POST $M/admin/links/pun -H 'X-API-Key: dev-admin' -H "$J" -d '{"mode":"down"}'
curl -s -X POST $P/admin/links/mum -H 'X-API-Key: dev-admin' -H "$J" -d '{"mode":"down"}'
curl -s -X PATCH $M/v1/reports/R-0a1b2c3d -H 'X-API-Key: dev-officer' -H "$J"   -d '{"op_id":"close-1","field":"status","value":"closed","expected":["mum:1"]}'
curl -s -X POST $P/v1/reports/R-0a1b2c3d/notes -H 'X-API-Key: dev-officer' -H "$J"   -d '{"op_id":"add-2","text":"{\"kind\":\"ADD\",\"sku\":\"SKU-2\",\"name\":\"Steel water bottle\",\"qty\":1,\"price\":499,\"user\":\"Alice\"}"}'
curl -s -X POST $M/admin/links/pun -H 'X-API-Key: dev-admin' -H "$J" -d '{"mode":"up"}'
curl -s -X POST $P/admin/links/mum -H 'X-API-Key: dev-admin' -H "$J" -d '{"mode":"up"}'

curl -s $M/v1/review                        # one item: the order was placed while a new item arrived elsewhere

# a supervisor decides; `candidates` is the cart's `live.status` list. An officer's key gets 403
curl -s -X POST $M/v1/reports/R-0a1b2c3d/resolve -H 'X-API-Key: dev-supervisor' -H "$J"   -d '{"field":"status","value":"closed","candidates":["<ids from live.status>"],"reason":"ship with the new item"}'
```

Both nodes now show the same state and an empty review queue (`/v1/status` has the same `event_digest` on each).
A forged event handed to `POST /v1/carry` comes back `{"result": "rejected", "reason": "unknown_origin" | "bad_hash" | "bad_sig"}`.
<!-- END:api -->

<!-- BEGIN:tests owner=B+D -->
## Tests and how to run them

```bash
pytest tests/engine        # pure engine: hash, HLC, derive and policy v1, signing, carry admission, priority
pytest tests/failover      # T17 zero-loss failover (in memory, and kill -9 on real processes) + T18 carry integrity
pytest tests/fuzz          # F1: three nodes, random writes, retries, partitions, tampering
pytest tests/shop          # shop fixtures (carts, split-brain, close-vs-new-info, op_id retry)
pytest tests/scale         # fairness of the per-shopper limit as properties, against the real limiter
pytest tests/integration   # real node processes: replicate, restart, partition, heal, relay, power, region limit, Bengaluru
pytest tests/convergence   # sign-in mode: unit tests, and real crypto and real processes (needs Node 24 + npm install in collaboration/true)
pytest                     # everything
cd frontend-new && npm test && npm run build        # console unit tests, then the type-check and production build
node tests/browser/integration.cjs keys|auth        # the real console against real regions (see Run the browser test)
bash scripts/check_m1.sh   # curl evidence script (needs jq, nodes running)
python scripts/prove_it.py --chaos 3 --wait 20   # live: cut / write on both sides / heal, ends CONVERGED (exit 0)
```

Results on the joined `main` (Windows laptop, Python 3.14; numbers from the runs, not estimates): **558 passed, 0 failed** (8 min 21 s; the Node-dependent sign-in tests ran too). Console `npm test`: 118 passed; partner package: 67 passed, 1 todo; identity panel: 8 passed; `npm run build` passes with and without the TRUE packages.

- **T17 zero-loss failover on real processes** (`tests/failover/test_t17_live.py`): Mumbai is killed with
  `kill -9` while holding 3 adds no peer has; the browser carries them to Pune; every acknowledged event is
  present on the survivor (`/v1/events/has` missing = []); 0 errors; Mumbai restarts and both converge with
  no duplicate items. Kill to first success on Pune: 52 to 61 ms over 4 runs (T20 asks for p95 over 10 runs: crashed node
  p95 39 ms, hung node p95 631 ms in `docs/failover-latency.md`).
- **T18 carry integrity, T16 engine half, T1, T2, T4, T5, T6** and one test per policy rule: pass.
- **T21 degradation ladder, T22 data at risk, T14 priority queue, T15 partition both ways** (`tests/integration/test_lane_d.py`): pass.
- **F1 fuzz:** 236 examples on 3 nodes, 918 events from a revoked credential refused, no divergence (`tests/fuzz/last_result.json`).
- **Scaling and limits on real processes** (`tests/integration/test_scale.py`, `test_scale_groupcommit.py`): 300 writes at each
  power are all present, gap-free and identical on every region; a region over its limit answers `503` while replication into it
  still works; Bengaluru joins empty and reaches the same digest and loses nothing when killed; group commit shares transactions
  (24 writers, 300 writes: 21 commits at high power, 43 at medium, 300 at low).
- **Sign-in joins** (`tests/convergence/test_join.py`): the restricted item is `403 claim_required` and stores nothing, the limit
  follows the credential (two sessions of one person share an allowance), carry refuses a validly signed event whose credential
  was revoked (quarantined, state unchanged), and the sidecar HTTP client is built once, not on every sign-in.
- **Browser test** (`frontend-new/tests/browser/integration.cjs`): keys mode **14 of 14** checks and sign-in mode **20 of 20** passed on 13 October 2026, headless Chrome against real regions (a denied write is a real 403 / 401 from a region with nothing stored).
<!-- END:tests -->

<!-- BEGIN:convergence owner=A+D -->
## Convergence integration (partner team)

Sign-in with the partner team's **TRUE credential** is built and is **off by default** (`AUTH_ENABLED=true` turns it on).
A member proves eligibility with a credential, a region issues a short session that every region verifies locally with
pinned keys, and roles are enforced on the server: officers touch only their own carts, only supervisors resolve
conflicts. Killing a region does not log anyone out, and the issuer is never called on the write path. Revocation
reaches every region through a signed status list; if the issuer is unreachable, regions use the last status they hold
within strict time limits. Write-up with test evidence and limits: [docs/convergence-true.md](docs/convergence-true.md);
the partner's code is under `collaboration/true`; a scripted run is `python scripts/demo_true.py`.

The current console (`frontend-new`) uses it end to end: in sign-in mode every request carries a verified session (never an API
key), the identity section signs members in with the wallet, an adults-only item needs a proven claim, the per-shopper limit
follows the credential, and Tamper's fourth row is an event from a revoked credential. The old wallet screen is still in `dashboard/`.
<!-- END:convergence -->

<!-- BEGIN:tr owner=D -->
## Trial and Reward log

Measured on one laptop with every region on it; full tables and the commands are in [docs/tr-log.md](docs/tr-log.md).

- **Write spike:** with a region limit of 40 writes a second, 300 writes at 24 at a time gave 55 accepted and 245 `503`;
  afterwards all regions held the same 555 events with the same digest and nothing quarantined. More concurrency did **not**
  make a region faster (63 writes a second at 24 at once, 74 one at a time): one Python process on one SQLite file is the ceiling.
- **Add capacity:** Bengaluru joined empty and reached the same digest as the other three in **6.2 seconds** (555 events).
- **Group commit (the power dial):** 21 commits instead of 300 for the same 300 writes at high power, but throughput stayed
  about the same on this machine (43.5, 37.5, 39.9 writes a second at low, medium, high).
- **1, 2, 3 regions at low power:** 39.0, 47.2, 51.5 writes a second (`scripts/bench_scale.py`, shown on the console's Scaling tab).
<!-- END:tr -->

<!-- BEGIN:status owner=C -->
## Status (honest)

| area | state |
|---|---|
| Independent nodes, local-first writes, pull + push replication, watermarks, partition and slow link, restart safety, roles, history, status digests | built, covered by the test suite |
| op_id idempotency, Ed25519-signed events, `/v1/carry`, `/v1/events/has`, `/healthz`; ingest and carry reject unsigned or badly signed events | built, covered by tests and verified live |
| Policy v1 and CLOSE_VS_NEW_INFO | built, covered by tests (`tests/engine`, `tests/shop`) |
| Review queue and supervisor resolve (`/v1/review`, `/resolve`) | built, covered by tests and verified live |
| Chaos agent (crash / start a node, inject a tampered event) | built, verified live |
| Zero-loss failover (T17) on real processes with `kill -9`; carry integrity (T18) | built, covered by tests |
| Randomized convergence test (F1, Hypothesis) | built, see the numbers under *Tests* |
| TRUE credential sign-in (optional auth mode) | built, off by default, tested on real processes; in the current console: identity section, restricted item, crash without re-sign-in, four-row Tamper (browser test, sign-in mode) |
| Per-shopper rate limit (429), keyed by the verified person in sign-in mode | built, property tests against the real limiter, verified in the browser |
| Region write limit (503) and failover to the next region; group commit (power dial); `POST /admin/config` | built, verified on real processes and in the browser; the dial changes commits, not much throughput on one laptop |
| Bengaluru (fourth region) joining a running mesh | built, verified live (same digest in 6.2 s); in sign-in mode start all regions together |
| Measured scaling comparison (Scaling tab) | measured, `docs/scale/scale-result.json`; one laptop, so not three machines' worth |
| Revoked-credential rule | refuses an event at `/v1/carry` only (browser path); peer replication keeps valid history so digests can converge |
| Demo frontend `frontend-new` (shop, region cards, crash and restart, failover order, scaling controls, inspector, Prove it) | built; `frontend-new/tests/browser/integration.cjs` drives it against real regions in both modes (see *Tests*) |
| Failover latency (T20): crashed node p95 39 ms, hung node p95 631 ms | measured, see `docs/failover-latency.md` |
| Delhi as a third node: joins empty and converges; cascade crash of two nodes loses nothing | verified live |
| Metrics API | not built; the Metrics tab shows what the status endpoint provides |
| Screenshots of the current UI in this README | not done, see [later.md](later.md) |
| `WRITE_QUEUE_MAX`, `drop_pct` on a link, `WRITE_BATCH_MS`, `LINK_SLOW_BPS` | `WRITE_QUEUE_MAX` is enforced (503); the other three are accepted or read but do nothing |
<!-- END:status -->

<!-- BEGIN:limits owner=C -->
## Limits we state openly

- A write is lost only if its node dies before any peer has it **and** no browser outbox holds it
  (for example the tab was closed). Pushing critical events first shrinks that window; it cannot remove it.
- A node holding a valid signing key can still lie. No Byzantine tolerance is claimed.
- Localhost traffic is not TLS; a deployed VM needs a TLS reverse proxy.
- Revocations issued during a partition are unknown to the cut-off node until it reconnects, so a revoked member can still be
  accepted there for a short, stated window.
- **Power is a batch-size setting on one laptop, not real hardware.** All regions and the load generator share one machine's CPU, so
  "three regions" is not three machines, and throughput numbers vary from run to run.
- **The rate limiter's counters are in memory, per region.** A limited shopper who fails over starts with a fresh allowance on the
  next region: it limits load per region, it is not a global quota. A restart empties the buckets.
- **A revoked member's event is refused at carry, not at replication.** A browser cannot rescue an acknowledged write of a member
  who was revoked meanwhile, if its node died first; replication keeps such history so every region can reach the same digest.
- **The restricted product proves a claim from the partner team's credential**; no real identity or payment is involved.
- We do not claim 100% uptime. We claim: no error shown while any node answers, and no
  acknowledged write lost while any browser or peer holds it.
<!-- END:limits -->

<!-- BEGIN:credits owner=C -->
## Credits, AI use and team

Libraries: FastAPI, Uvicorn, httpx, Pydantic, SQLite, React, Vite, Hypothesis, pytest.
AI assistants were used as the event rules allow; each module's owner explains it line by line and
every PR was explained back by a teammate. Ideas (not code) came from the references in the build plan
(CouchDB conflict model, Jepsen, Bayou session guarantees, bounded counters); nothing was copied.

| lane | person | owns |
|---|---|---|
| A | Akshit Sharma | API, storage, security, contracts, the `web` and `frontend-new` consoles |
| B | Rahil | merge engine, signing, correctness tests |
| C | Miti Shah | first dashboard, chaos agent, fixtures, partner sign-in, documentation |
| D | Simrit | replication, carry, runtime, scaling controls (power, region limit, Bengaluru), Trial and Reward |
<!-- END:credits -->
