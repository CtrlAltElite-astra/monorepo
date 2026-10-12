# Conflict policy v1

Owner: Rahil. Code: `region/engine/policy.py` (the rules) and `region/engine/derive.py` (how they are applied).
`POLICY_VERSION = 1`.

This is SyncShield's defined conflict-resolution policy. It says what every node shows when two nodes
changed the same thing without seeing each other, and which cases a supervisor must decide.

The engine keeps the original field names. The shop screen shows them under other labels:

| engine field | shop label |
|---|---|
| `title` | cart owner |
| `description` | delivery note |
| `severity` 1–5 | delivery speed: 1 Standard, 2 Plus, 3 Express, 4 Same-day, 5 Priority |
| `status` | `open` Shopping → `assigned` Checking out → `resolved` Paid → `closed` Order placed; `cancelled` |
| `assigned_team` | fulfilment centre |
| `location` | delivery pin |
| notes | cart activity (one note per item added) |

## 1. What counts as a conflict

Every change is an event that is never edited. A write to a field lists, in `supersedes`, the values of
that field its node held at that moment. Those values are replaced. A value that no write has replaced
is **live**.

- One live value: that is the field's value.
- Several live values that are equal: still no conflict.
- Several live values that differ: a **conflict**. The two writers did not see each other.

These are not conflicts:

- an edit after an edit on the same node (the second replaces the first);
- changes to different fields, or to different carts;
- a write that was retried (section 5).

The result depends only on which events a node holds, never on the order they arrived in. Two nodes
with the same events show the same values, the same conflicts and the same receipts.

## 2. The rules, one line each

When a field is in conflict every node shows the same provisional value, chosen by this table.

| field | value shown until someone decides | supervisor review | rule id | why |
|---|---|---|---|---|
| `title`, `description`, `category` | the latest write | no | `LATEST` | low stakes; the other value stays in the history |
| `severity` | the highest | no, receipt only | `SEVERITY_HIGHEST` | a lost upgrade is worse than a false one |
| `status` | the most active: `open` < `assigned` < `resolved` < `closed` = `cancelled`, lowest wins; a tie takes the latest | yes | `STATUS_MOST_ACTIVE` | a false "order placed" can ship an incomplete cart |
| `assigned_team` | the earliest write | yes, flag `DOUBLE_DISPATCH` when two different centres were named | `TEAM_EARLIEST` | a stable choice; only a person redirects a shipment |
| `location` | the latest write | only if two pins are 200 m or more apart (`FAR_APART`) or one side has no pin (`LOCATION_MISSING`) | `LOCATION_LATEST` | GPS jitter is harmless, a different street is not |
| `people_estimate` | the highest, never the sum | yes | `PEOPLE_HIGHEST` | 20 and 25 for one thing is not 45 |
| notes | all of them, oldest first | never a conflict | | adding never conflicts |
| two supervisor decisions with different values | the field's own rule again | yes, flag `RESOLVE_VS_RESOLVE` | the field's rule | no decision silently overrides another |
| order placed or cancelled while an unseen item was added | the status does not change | yes | `CLOSE_VS_NEW_INFO` | an addition is never buried under a close (section 3) |

"Latest" and "earliest" use the hybrid logical clock order `(hlc, origin, seq)`. The clock only breaks
ties between writes that are already known to be concurrent. It never decides whether a conflict exists.

## 3. Order placed while a new item was added

A cart's status can have a single live value and still be unsafe. Mumbai places Alice's order while
Pune, cut off, adds a gift card. Only Mumbai touched the status, so there is no status conflict, yet
the order was placed without the gift card on screen.

Events carry an optional `observed` list for this:

- a write that sets the status to `closed` or `cancelled` lists the cart-activity notes and the live
  delivery-speed values its node held;
- a cart-activity note, or a delivery-speed write, lists the live status values its node held.

If a live close and a note (or a live delivery speed of 4 or more) exist where neither lists the other,
`derive()` adds a review item with rule `CLOSE_VS_NEW_INFO`. The item's `new_info` names what the order
did not see. The status stays "order placed" on every node until a supervisor decides: ship with the
new item, refund and re-order, or keep it as it is.

The item goes away when a status write that saw the new item replaces the close. Confirming the close
and reopening the cart both do that. A further item nobody saw opens it again.

`policy.observed_for(type, field, value, view)` returns the list a new write must carry.

## 4. A supervisor's decision

A decision is an ordinary event of type `RESOLVE`. It replaces exactly the values that were live when
the supervisor looked (the receipt's `event_ids`) and it replicates like any other event. After it
arrives everywhere it is the only live value, so the review item disappears on every node.

A write that never saw the decision is concurrent with it. Both are then live, and the conflict comes
back under the field's rule. A decision never hides an edit it did not see.

## 5. A retried write counts once

A browser that gets no answer retries on another node with the same `op_id`. If the first node had
already stored the write, the same write now exists as two events. Events that share an `op_id` and say
the same thing (same type, field and value) are copies of one write:

- only the oldest copy is live, so a retried "add to cart" is one item;
- replacing one copy replaces all of them, so the other copy cannot come back as a rival value.

The same `op_id` with different content is a client bug, not a retry. It is not merged.

## 6. The receipt

Every conflict is recorded in the report's `conflicts` list, at most one entry per field:

```json
{
  "field": "assigned_team",
  "relationship": "CONCURRENT",
  "candidates": [
    {"event_id": "mum:2", "value": "Bhiwandi", "origin": "mum", "actor": "agent-mum-1", "wall": "2026-10-12T13:50:10+00:00"},
    {"event_id": "pun:1", "value": "Chakan", "origin": "pun", "actor": "agent-pun-1", "wall": "2026-10-12T13:50:11+00:00"}
  ],
  "rule": "TEAM_EARLIEST",
  "policy_version": 1,
  "provisional": "Bhiwandi",
  "needs_review": true,
  "flags": ["DOUBLE_DISPATCH"],
  "values": ["Bhiwandi", "Chakan"],
  "event_ids": ["mum:2", "pun:1"]
}
```

`provisional` is the value in `state`. `values` and `event_ids` repeat the candidates in the older
shape, which the contract keeps. A `CLOSE_VS_NEW_INFO` item also has `new_info`: a list of
`{event_id, kind: "NOTE" | "SEVERITY", value, origin, actor, wall}`.

A review queue is every receipt with `needs_review: true`.

## 7. Worked example

Alice's cart exists on Mumbai and Pune. The link is cut and both keep writing. After the link heals
both nodes show exactly this (`fixtures/events_split_brain.json`, test T4):

| field | written while cut off | shown on both nodes | rule and review |
|---|---|---|---|
| delivery speed | Mumbai 5, Pune 4 | 5 | `SEVERITY_HIGHEST`, receipt only |
| fulfilment centre | Mumbai Bhiwandi, then Pune Chakan | Bhiwandi | `TEAM_EARLIEST`, `DOUBLE_DISPATCH`, review |
| status | Mumbai "order placed"; Pune did not touch it | order placed | no field conflict |
| cart activity | Pune adds a gift card | the gift card is visible on Mumbai | all notes are kept |
| order vs new item | the order saw neither Pune's gift card nor its Same-day upgrade | status stays "order placed" | `CLOSE_VS_NEW_INFO`, review |

## 8. Decisions taken

| # | question | taken | where |
|---|---|---|---|
| D1 | two nodes changed the status: which one is shown? | the most active, always reviewed | `Policy.status_rule` (`"furthest"` is the other option) |
| D2 | does a delivery-speed conflict need review? | no, the highest is safe; the receipt records it | `Policy.severity_review` |
| D3 | how far apart must two delivery pins be for review? | 200 m | `Policy.location_review_m` |

Every node must run the same rules, so these are code, not per-node settings. To change one, edit
`POLICY_V1` in `policy.py`, bump `POLICY_VERSION` and update the rule's test in the same PR. Nodes with
different policy versions show INCOMPATIBLE instead of CONVERGED.

## 9. Things the engine guarantees

- **No clock decides safety.** The highest, the most active and the review flags do not use time. The
  distance check uses whole-number arithmetic, so no two machines can round differently.
- **A malformed event cannot break a report.** `derive()` ignores an event whose shape it cannot use, the
  same way on every node. Received events are never re-validated against business rules, so the rules
  also cope with values nobody checked.
- **Nothing is lost.** A value that did not win stays in the event log and in the receipt.

## 10. Replication priority

`priority.classify(event, view_before)` gives the lane stored on each event. On a slow link the lower
number is sent first.

| lane | events |
|---|---|
| 0 critical | status set to `resolved`, `closed` or `cancelled`; delivery speed raised to 4 or more; a fulfilment centre assigned; any `RESOLVE`; a cart created at delivery speed 4 or more |
| 1 normal | everything else |
| 2 bulk | cart-activity notes and bulk seeds |

## 11. Tests

| claim | test |
|---|---|
| every rule in section 2 | `tests/engine/test_policy.py`, one test per rule |
| order placed vs new item | `tests/engine/test_close_vs_new_info.py` |
| a retried write counts once (T16, engine half) | `tests/engine/test_op_id.py` |
| the same pages twice change nothing (T1) | `tests/engine/test_gates.py`, with the real store and ingest |
| any arrival order gives the same view (T2) | `tests/engine/test_gates.py`, all 720 orders of six events |
| the worked example on both nodes (T4), disjoint fields (T5), decisions (T6) | `tests/engine/test_gates.py` |
| priority lanes and data at risk | `tests/engine/test_priority.py` |

Run them with `pytest tests/engine`.
