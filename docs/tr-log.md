# SyncShield: Trial and Reward log

Team: Akshit Sharma, Rahil, Miti, Simrit. Reference: Main Build Plan v1.1, section 13, and the scaling master plan.

**Every number below comes from a run on one Windows laptop (4 logical CPUs, Python 3.14), all regions and the load
generator on that same machine, 13 October 2026.** An earlier version of this file listed figures nobody had measured
(for example "accepted 200, rejected 100"); they are gone. Where a result is small or surprising it is written as it is.

## 1. The curveballs and what answers them

| curveball | what answers it | measured here |
|---|---|---|
| write spike | per-region write limit (`REGION_WRITE_LIMIT_PER_S`): over it a write gets `503 overloaded` + `Retry-After` and the browser sends it to the next region; group commit (`POWER`) shares a transaction between queued writes | section 3 |
| add capacity | Bengaluru (`blr`, :8004) joins a running mesh empty and pulls every origin | section 4 |
| region lost | the browser carries its signed events to a survivor; nothing acknowledged is lost | tests T17/T18, `docs/failover-latency.md` |
| throttled link | critical events cross first (`PushQueue`), bulk waits but is never dropped | unit test `test_t14_priority_queue_ordering`; no live timing is claimed |

## 2. Knobs (all off or neutral by default; change them live with `POST /admin/config`)

| knob | default | effect |
|---|---|---|
| `REGION_WRITE_LIMIT_PER_S` | 0 (off) | writes per second a region admits; a replayed `op_id` is never refused or counted |
| `POWER` | medium | writes saved per transaction when writes are already queued: low 1, medium 8, high 32 |
| `RATE_ACTOR_BURST`, `RATE_ACTOR_REFILL_PER_S` | 0 (off), 1 | per-shopper token bucket (429), keyed by the verified person in sign-in mode |
| `WRITE_QUEUE_MAX` | 100 | writes in flight above which a region answers 503 |
| `PAGE_SIZE`, `SYNC_INTERVAL_MS`, `PUSH_ENABLED`, `LINK_RATE_BPS` | 200, 1000, 1, 0 | replication paging, interval, push shortcut, link throttle |

`WRITE_BATCH_MS` and `LINK_SLOW_BPS` are read from the environment but nothing uses them.

## 3. Write spike (one region, 24 writers at once)

`python scripts/loadgen.py --url http://127.0.0.1:8001 -n 300 -c 24`

| run | offered | accepted | refused | accepted per second | p50 | p95 |
|---|---|---|---|---|---|---|
| one writer at a time (baseline, 200 writes) | 200 | 200 | 0 | 73.9 | 11.3 ms | 16.4 ms |
| 24 at once, no limit | 300 | 300 | 0 | 63.1 | 89.7 ms | 3 703 ms |
| 24 at once, region limit 40 a second | 300 | 55 | 245 (all 503) | 13.4 | n/a | n/a |

What this shows, and what it does not:

- With the limit on, the region turned away 245 of 300 writes with `503` and said so in its own counter
  (`rate.refused_region` = 245). In the console the browser moves the shopper to the next region, so nothing is shown as an error.
- Nothing admitted was lost: after the spike all three regions held **555 events** (200 + 300 + 55), with the same digest,
  contiguous heads that add up to 555, and 0 quarantined.
- **The extra concurrency did not make the region faster.** 24 writers at once gave a lower throughput than one
  at a time, and a long tail (p95 3.7 s). The unmodified region does the same, so this is not the limit or the batching.
  A single Python process with one SQLite file is the ceiling on this machine.
- **The power dial barely changes throughput here.** Group commit really cuts commits (24 writers, 300 writes: 21 commits at high
  power, 43 at medium, 300 at low; see `tests/integration/test_scale.py` for the asserted version), but a laptop SSD makes each commit cheap, so the
  throughput stayed about the same (`docs/scale/scale-result.json`: 43.5, 37.5 and 39.9 writes a second at low, medium and high).
  Power is a batch-size setting on one laptop, not real hardware.

## 4. Add capacity: Bengaluru joins

`POST http://127.0.0.1:9000/start/blr` (the chaos agent runs with `REGIONS=mum,pun,del,blr`), or `python scripts/join_region.py blr`.

- Bengaluru starts with an empty database, the three running regions add it as a peer without a restart, and it pulls every origin.
- **6.2 seconds** from the start request until all four regions reported the same event digest (555 events).
- Killing Bengaluru afterwards loses nothing: the writes it acknowledged are on the other regions
  (`test_bengaluru_joins_a_running_mesh_empty_converges_and_loses_nothing`).
- In sign-in mode the running regions cannot be told about a new peer at runtime (that call needs an admin session), so
  start all four regions together there.

## 5. Scaling comparison (`python scripts/bench_scale.py --vertical --horizontal --compare`)

300 add-to-cart writes, 8 at a time, recorded in `docs/scale/scale-result.json` (the console's Scaling tab reads the same file):

| setup | writes per second | p95 |
|---|---|---|
| one region, low / medium / high power | 43.5 / 37.5 / 39.9 | 148 / 179 / 165 ms |
| 1 / 2 / 3 regions at low power | 39.0 / 47.2 / 51.5 | 158 / 137 / 159 ms |
| one region at high power vs three at low power | 38.9 vs 50.6 | n/a |

Three regions did better than one, by about a third. All regions share one laptop CPU, so this is not three machines'
worth. The earlier run by Lane B (before the power setting existed) measured 59, 85 and 93 on a quieter machine; run to
run variation is large.

## 6. Reproduce

```powershell
python scripts/chaos_agent.py                                   # REGIONS=mum,pun,del,blr, then POST /start/<region>
python scripts/loadgen.py --url http://127.0.0.1:8001 -n 300 -c 24 --json
curl -X POST http://127.0.0.1:8001/admin/config -H "X-API-Key: dev-admin" -H "Content-Type: application/json" -d '{"region_write_limit_per_s": 40}'
python scripts/bench_scale.py --vertical --horizontal --compare
```
