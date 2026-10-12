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

The demo frontend is **`frontend-new/`**: a landing page and a live console.

- **Left, the shop.** One scrolling column: five shoppers, six products, the cart. Every tap is a real signed write.
  Items can be added and removed (a removal is a new event with quantity -1; nothing is deleted). Delivery speed,
  fulfilment centre, check out, pay, place order, cancel, and the cart history. After an order is placed or cancelled
  the next item starts a new cart.
- **Right, the live backend.** Three region cards (Mumbai, Pune, Delhi), each with a status light, its event count,
  carts, events at risk, and its own copy of the selected shopper's cart. Evidence tiles (acknowledged, recovered,
  not on survivors), the latest action as three steps (saved, signed, peers confirmed), an event trail, and replication links.
- **Break it.** Pause replication, then hold **Hold to crash** on a region card: the process is really killed. The card
  shakes and its light turns red (solid for two seconds, then blinking). **Restart** brings it back: amber while it
  catches up, then green.
- **Failover.** The browser sends writes to the first region that answers, in a fixed order: Mumbai, Pune, Delhi. When
  a region dies the browser first hands the survivor the signed events it still holds, so no acknowledged write is lost.
  A region that comes back must answer three checks in a row before it serves again.
- **Inspect.** Carts per region, Network (up / slow / cut, burst, tamper), Review (a supervisor resolves a conflict),
  Outbox, Security, Metrics, API, and **Prove it** (cut a region off, change the same cart on both sides, heal, compare digests).
- **Motion** switch in the navigation turns animation on or off. A `?mode=mock` simulator runs the same UI with no backend.

Three-minute walkthrough: add items -> pause replication -> add one more -> hold **Hold to crash** on the serving region ->
watch the browser move to the next region with 0 errors -> **Restart** -> resume replication -> **Prove it**.

`web/` is the earlier console with the same SDK (port 5173) and `dashboard/` is the first dashboard, which also has the
opt-in wallet sign-in. Screenshots of the current UI are still to be added (see [later.md](later.md)).
<!-- END:demo -->

<!-- BEGIN:setup owner=C -->
## Stack and setup

Python 3.11+, FastAPI, SQLite (WAL, one file per node), httpx, React + Vite + TypeScript. Needs Node 18+.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows   (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt   # add -r requirements-auth.txt for the optional sign-in mode and its tests

# 1. the crash controller; it also starts the regions
set REGIONS=mum,pun,del           # PowerShell: $env:REGIONS='mum,pun,del'   bash: export REGIONS=mum,pun,del
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
| mum / pun / del | 8001 / 8002 / 8003 | `data/<region>.db`, signing keys in `keys/` (both git-ignored, created on first start) |
| chaos agent | 9000 | kills and starts region processes for the UI; pid files in `run/` |
| `frontend-new` | 5175 | proxies `/api/<region>` and `/chaos`; settings in `frontend-new/.env.example` |
| `web` (earlier console) | 5173 | `bash scripts/run_web.sh` or `cd web; npm run dev` |

Dev keys on every region: `dev-officer`, `dev-supervisor`, `dev-admin`. All settings come from environment variables
(see `.env.example`). Run **exactly one** Uvicorn worker per node: sequence numbers are guarded by an in-process lock.
To reset the demo, stop the regions and delete `data/*.db*`.
<!-- END:setup -->

<!-- BEGIN:api owner=A -->
## API

Header `X-API-Key`. Dev keys: `dev-officer` (writes), `dev-supervisor` (also resolves conflicts), `dev-admin`
(partition lab). Reads need no key. Full contract: [contracts/contracts.md](contracts/contracts.md).

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

Writes accept an optional `op_id` (or `Idempotency-Key` header): the same operation is applied once, and a replay returns 200 with `Idempotent-Replay: true`. 2xx bodies carry the full signed `event`.

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
pytest tests/integration   # real node processes: replicate, restart, partition, heal, relay
pytest                     # everything
bash scripts/check_m1.sh   # curl evidence script (needs jq, nodes running)
python scripts/prove_it.py --chaos 3 --wait 20   # live: cut / write on both sides / heal, ends CONVERGED (exit 0)
```

Lane B results on the merged `main` (Windows laptop, Python 3.14; numbers from the runs, not estimates):

- **T17 zero-loss failover on real processes** (`tests/failover/test_t17_live.py`): Mumbai is killed with
  `kill -9` while holding 3 adds no peer has; the browser carries them to Pune; every acknowledged event is
  present on the survivor (`/v1/events/has` missing = []); 0 errors; Mumbai restarts and both converge with
  no duplicate items. Kill to first success on Pune: 52 to 61 ms over 4 runs. (T20 asks for p95 over 10
  runs; not measured yet.)
- **T18 carry integrity, T16 engine half, T1, T2, T4, T5, T6** and one test per policy rule: pass.
- **F1 fuzz:** 211 examples on 3 nodes, 5 480 writes, 1 839 retries under the same `op_id`, 361 tampered
  deliveries rejected, no divergence (`tests/fuzz/last_result.json`).

*(Simrit: add the T19-T22 results and scenario scripts.)*
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

The `frontend-new` console uses the API-key mode; the wallet sign-in screen is in `dashboard/`.
<!-- END:convergence -->

<!-- BEGIN:tr owner=D -->
## Trial and Reward log

*(Simrit: before/after numbers from `docs/tr-log.md`.)*
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
| TRUE credential sign-in (optional auth mode) | built, off by default, tested on real processes |
| Demo frontend `frontend-new` (shop, region cards, crash and restart, fixed failover order, inspector, Prove it) | built; a browser run against three isolated real backends passed every stage before the last UI changes (smaller type, remove from cart, new cart after an order), which were checked in the simulator only |
| Failover latency (T20): crashed node p95 39 ms, hung node p95 631 ms | measured, see `docs/failover-latency.md` |
| Delhi as a third node: joins empty and converges; cascade crash of two nodes loses nothing | verified live |
| Metrics API | not built; the Metrics tab shows what the status endpoint provides |
| Screenshots of the current UI in this README | not done, see [later.md](later.md) |
<!-- END:status -->

<!-- BEGIN:limits owner=C -->
## Limits we state openly

- A write is lost only if its node dies before any peer has it **and** no browser outbox holds it
  (for example the tab was closed). Pushing critical events first shrinks that window; it cannot remove it.
- A node holding a valid signing key can still lie. No Byzantine tolerance is claimed.
- Localhost traffic is not TLS; a deployed VM needs a TLS reverse proxy.
- Revocations issued during a partition are unknown to the cut-off node until it reconnects.
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
| D | Simrit | replication, carry, runtime, Trial and Reward |
<!-- END:credits -->
