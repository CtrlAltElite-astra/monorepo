# Fault: `test_t21_ladder_transitions` fails about one run in three

Owner of the code and the test: Simrit (Lane D). Written by Rahil (Lane B) during the merge of
`lane-b-rahil` into `main`, 2026-10-13. Status: **open**, tracked as D6 in `docs/lane-b-handoff.md`.

## In one paragraph

Right after a node starts, `GET /v1/status` reports `"level": "ISOLATED"` for a peer that is up and
answering, until the node's first successful pull from that peer. T21 reads the level once, immediately
after both nodes answer `/v1/health`, and asserts `HEALTHY` or `RECOVERING`. Whether the first pull has
finished by then is a race, so the test fails intermittently. The race is in the product
(`region/sync/state.py`), not only in the test: the plan's ISOLATED rule (3 failures or 5 s of silence)
is not met at that moment, yet the node reports ISOLATED.

## Where it fails

`tests/integration/test_lane_d.py`, `test_t21_ladder_transitions` (line 543), at line 556:

```python
s0 = httpx.get(f"{URL['mum']}/v1/status").json()
assert s0["level"] in ("HEALTHY", "RECOVERING")
```

```
E   AssertionError: assert 'ISOLATED' in ('HEALTHY', 'RECOVERING')
```

All 5 failures whose output was captured (4 on `main` alone, 1 on the merged tree) fail at this line with this message.

## Evidence

Runs of `pytest tests/integration/test_lane_d.py::test_t21_ladder_transitions` on one Windows laptop
(Python 3.14), alternating between the two trees:

| tree | runs | passed | failed | failing line |
|---|---|---|---|---|
| `main` at `6320cb7` (before the merge) | 19 | 13 | 6 | 556 (4 of 4 captured) |
| merged tree | 19 | 12 | 7 | 556 (1 of 1 captured) |

The rate is the same on both trees (about one run in three), and the failing line runs before any write,
ingest or push, so the merge does not cause it. It also showed up once in the full suite.

## What happens, step by step

1. The test starts `mum`, then `pun` (`start_node`), each with `SYNC_INTERVAL_MS=250`.
2. `mum`'s pull loop (`region/sync/client.py`, `run_forever`) tries `pun` at once. `pun` is still
   starting, so the pull fails: `state.mark_error` adds 1 to `fails` and `last_ok` stays `None`. The loop
   then sleeps 250 ms. If `pun` takes more than about 0.75 s to come up, `fails` also reaches
   `ISOLATED_FAILS = 3`; only the first successful pull (`state.mark_ok`) sets `last_ok` and resets
   `fails` to 0.
3. `wait_healthy("mum")` and `wait_healthy("pun")` return as soon as each answers `GET /v1/health`.
   That says the process is up. It says nothing about whether `mum` has pulled from `pun` yet.
4. The test reads `mum`'s status straight away. `state.snapshot()` (`region/sync/state.py`) computes:

   ```python
   silent = last_ok is None or (now - last_ok) > ISOLATED_SILENCE_S   # line 178
   isolated = fails >= ISOLATED_FAILS or silent                       # line 179
   ...
   elif has_isolated:
       level = "ISOLATED"                                             # line 203
   ```

   `last_ok is None` makes the peer "silent" no matter how little time has passed, so the level is
   `ISOLATED`.
5. If `mum`'s next pull (up to 250 ms later, plus the request) has already succeeded, `last_ok` is set
   and the level is `HEALTHY` or `RECOVERING`: the test passes. If not, it fails. Slow process start on
   Windows makes the window wider.

The next assertion has the same weakness: `s_slow["level"] == "CONSTRAINED"` (line 561) is read once,
and `ISOLATED` takes precedence over `CONSTRAINED` in `snapshot()`, so it can fail for the same reason
when the first pull is late.

## Why it is a product fault, not only a test fault

- Plan 6.8: ISOLATED means "a peer failed 3 times or was silent for 5 s". At start-up neither is true
  yet; the node has simply not heard from the peer. Reporting ISOLATED then is greener-than-facts in
  reverse: redder than the facts.
- It is visible in the demo. Appendix A step 3 (restart Mumbai) expects RECOVERING then HEALTHY; the
  node card shows a red ISOLATED chip first, every time a node starts or restarts.
- `"reachable": false` is reported for a peer that is reachable.

## Ways to fix it (owner decides)

| option | change | effect | risk |
|---|---|---|---|
| A, product (recommended) | Count silence from when the node started trying, not from "never": record `STARTED_AT = time.time()` when the pull loop starts and use `silent = (now - (last_ok or STARTED_AT)) > ISOLATED_SILENCE_S` | Matches plan 6.8. A peer that is up is never reported ISOLATED at boot. A peer that is really down still turns ISOLATED after 3 failed pulls (a refused connection fails at once, so about 0.75 s at 250 ms) or 5 s | For up to 5 s or 3 failures, a never-reached peer shows `reachable: true`; consider a separate "not contacted yet" flag for the UI |
| B, test only | Replace the single read at lines 555-556 with `poll_until(lambda: level() in ("HEALTHY", "RECOVERING"), timeout=6)`, and the same for the CONSTRAINED read | Test becomes stable | The red ISOLATED chip at boot stays in the product |
| A and B | both | stable test and correct behaviour | none beyond A |

## How to check a fix

```bash
for i in 1 2 3 4 5 6 7 8 9 10; do pytest -q tests/integration/test_lane_d.py::test_t21_ladder_transitions; done
```

Expect 10 of 10. Then the whole suite, and the demo restart step: the restarted node should show
RECOVERING, then HEALTHY, never ISOLATED while its peers are up.
