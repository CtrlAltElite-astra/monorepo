# Consistency model: AP with strong eventual consistency

## The choice

When the link between regions breaks, a region can either **refuse writes** (consistent, unavailable)
or **keep accepting them** (available, temporarily divergent). We choose availability.
Refusing reports in a disaster is the worse failure. We make staleness visible instead.

**Guarantee:** two regions holding the same set of events derive identical state
(strong eventual consistency).

## How it works

1. Every change is an immutable **event** with `event_id = <origin>:<seq>`, a hash, and a hybrid logical clock.
2. A write lists in `supersedes` the field values it saw and replaces. That makes causality a
   property of the event itself.
3. **Conflict** = two writes to the same field where neither replaced the other (both stay "live").
   A write that saw the old value is just an update.
4. `derive()` is a pure function of the event set, so arrival order, duplicates and relays do not matter.
5. Replication copies missing events by per-origin gap-free watermark. It is idempotent.

## What the operator is told

| signal | meaning |
|---|---|
| Region level `HEALTHY` | all peers reachable, heads equal |
| `SYNCING` | peers reachable, heads differ (catching up) |
| `ISOLATED` | a peer failed 3 times or has been silent 5 s; our data may be stale |
| Report badge `LOCAL_ONLY / PARTIAL / REPLICATED` | how many peers confirmed this report |
| Header `CONVERGED` | every region reachable and digests identical |
| Header `UNKNOWN` | a region is unreachable: never shown as green |
| Header `DIVERGED` | regions reachable but digests differ (expected during a partition write) |

## What we do NOT guarantee

- Reads may be stale while partitioned (the badge says so).
- Events a destroyed region never replicated are lost.
- No Byzantine tolerance: a region with a valid key can lie.
- Basic build only: conflict rule is "latest wins + flag" (policy v0). Per-field safety policy and the review queue come in PDF 2.

## Likely jury questions

| question | short answer |
|---|---|
| Why not strong consistency? | A cut-off region would have to refuse reports. In a disaster that is the worse failure. |
| How do you know regions converge? | `derive()` is a pure function of the event set; tests cover permutations, duplicates and partitions and compare state hashes. |
| What counts as a conflict? | Two writes to one field where neither replaced the other. |
