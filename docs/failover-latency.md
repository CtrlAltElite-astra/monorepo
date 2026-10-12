# Failover latency (test T20)

Measured with the real browser SDK (`dashboard/src/sdk/syncshieldClient.js`) against real node processes on one
laptop (Windows, Python 3.14, localhost). Script: `dashboard/scripts/measure-failover.mjs`. 10 runs each.
Plan target: **first success on a survivor within 1.5 s, p95**.

| scenario | what happens | p50 | p95 | max | verdict |
|---|---|---|---|---|---|
| A. node crashed (kill -9, connection refused) | the SDK is refused at once and retries on the next node | 34.9 ms | 38.6 ms | 38.6 ms | meets 1.5 s |
| B. node hung (accepts, never answers) | the SDK waits one request timeout (600 ms) then retries | 618.8 ms | 631.4 ms | 631.4 ms | meets 1.5 s |

Runs in scenario A needed on average 1.0 failed attempt(s) before success.
The measured time starts when the crash completes (the port is closed) and ends at the first successful write on the
survivor, including the carry of the browser's outbox.

Honest limits: localhost has no real network latency; a hung node costs one request timeout (600 ms)
per failed attempt, so the worst case with N dead nodes ahead of a live one is about N x 600 ms plus one round trip.
