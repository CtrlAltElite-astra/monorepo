# Study sheet: the engine (Rahil, plan section 14)

Short answers to say out loud. Every claim points at code or a test you can open in front of the jury.

## CRDT vocabulary and why derive() converges

- **The event log is a grow-only set (G-Set).** Merging two logs is set union. Union is
  *associative*, *commutative* and *idempotent*, so it does not matter in what order, in what
  grouping, or how many times events arrive. Events are never updated or deleted.
- **derive() is a pure function of that set** (`region/engine/derive.py`): no I/O, no randomness, no
  wall clock. Same set in, byte-identical view out. That is *strong eventual consistency*: two nodes
  holding the same events show the same state, with no consensus round.
- **Each field is a multi-value register.** The "live" writes of a field are the ones nobody's
  `supersedes` list replaced. One live value means no conflict. Several live values are the
  concurrent values of a multi-value register. We do not throw the losers away: the policy picks a
  deterministic *provisional* value and the receipt keeps every candidate.
- **Why not plain last-writer-wins?** LWW silently drops a concurrent write; the 2013 Jepsen
  Cassandra test showed about 28% of acknowledged writes lost in one partition test (cite it as that
  test, do not generalise). We detect the concurrency and show it.
- **Why not cr-sqlite or Automerge?** They auto-merge and hide the disagreement. The problem statement
  grades a *defined* conflict policy and review, so the merge logic has to be ours and visible.

## HLC rules (`region/engine/hlc.py`)

- A stamp is `(physical_ms, counter)`.
- **Local write:** `l' = max(l, now)`; the counter goes up by 1 if `l` did not move, else it resets to 0.
- **Receive:** `l' = max(l, remote_l, now)`; the counter is one more than the largest counter among the
  inputs that reached `l'`, or 0 when only the wall clock did.
- **It never goes backwards**, even when the wall clock jumps back (`test_hlc_never_goes_backwards...`).
- **It only breaks ties and orders the display.** The total order is `(l, counter, origin, seq)`, and
  it is total because `(origin, seq)` is unique. Safety never depends on it: what is concurrent is
  decided by `supersedes`, not by clocks.
- A stamp more than 60 s ahead of ours is flagged as a suspect clock (`hlc.is_suspect`).

## derive() line by line

1. Keep only well-formed events and deduplicate by `event_id`. Malformed events are ignored the
   same way on every node, so one bad event can never block a report (`well_formed`).
2. `_op_groups`: events with the same `op_id` are copies of one client write (a retry on another
   node). A node refuses the same op_id with a different body at write time (409 `op_id_reuse`).
3. For each field:
   - the candidates are the CREATE plus every SET and RESOLVE of that field;
   - `gone` is every id some candidate's `supersedes` lists, plus all copies of those ids (replacing one
     copy replaces them all);
   - `live` is the candidates not in `gone`, one per op group, oldest first;
   - if the live values disagree, `_resolve` gives (winner, rule, review, flags) and a receipt is
     written; two RESOLVEs with different values add review and `RESOLVE_VS_RESOLVE`. Equal concurrent
     values are not a conflict.
4. `_close_vs_new_info`: for each live order placed or cancelled, the notes and delivery-speed writes of
   4 or more that it did not list in `observed` and that did not list it. One review item per close,
   with the close first and the status left as derived.
5. Notes: every NOTE except later copies of a retried add, oldest first.
6. The receipt's `provisional` is always the value every node shows.
7. Return `state`, `live`, `conflicts`, `incomplete` (true until the CREATE arrives) and `policy_version`.

## Every policy rule and its exception (`region/engine/derive.py`)

| field | rule | exception or edge case to know |
|---|---|---|
| title, description, category | latest by HLC, no review | none |
| severity (delivery speed) | highest, receipt only (D2) | a later write that **saw** both values can lower it, because that is an update, not a conflict |
| status | most active: open < assigned < resolved < closed = cancelled, lowest wins, always review (D1) | closed versus cancelled is a tie, so the latest wins; "furthest wins" was the other option (D1) |
| assigned_team | earliest, review | `DOUBLE_DISPATCH` only when two **different** centres were named; assign versus unassign is reviewed without that flag |
| location | latest; review if 200 m or more apart (D3) | only the boolean `FAR_APART` comes from the distance; a candidate without a pin is not measured |
| people_estimate | highest, never the sum, review | none |
| notes | union; copies with the same op_id collapse | none |
| CLOSE_VS_NEW_INFO | status unchanged, review | Paid (`resolved`) is not a close; delivery speed below 4 is not new information; a close that recorded no `observed` list raises no alarm; when the status is itself in conflict the item shows the status every node shows |
| RESOLVE vs RESOLVE | same value: no conflict; different values: the field rule again plus `RESOLVE_VS_RESOLVE` | a write that missed the decision is concurrent with it and reopens review |

Any rule change bumps `POLICY_VERSION` in the same PR. Nodes on different versions show INCOMPATIBLE,
never CONVERGED.

## Signing and carry (`region/engine/signing.py`, `admit.py`)

- Ed25519 over the event hash. The hash is SHA-256 of canonical JSON (sorted keys, no spaces, UTF-8,
  no floats) without `hash` and `sig`. Signatures are deterministic: same key and hash, same signature.
- A node signs only its own events: `signing.sign(region, hash)`. The private key is in
  `keys/<region>.key` (git-ignored); public keys are in `region/trust/public_keys.json`; a region makes
  its own key on first start (`signing.ensure_keys`).
- Carry checks (`region/sync/carry.py`, and the same rule in `admit.py`), in order: shape, then
  unknown_origin, then bad_hash, then bad_sig. An edited body fails
  the hash. Edited and re-hashed, it fails the signature. Signed by another node, it fails the signature.
  The browser can only fail to help; it cannot harm.
- The carrier's role is not checked again: the origin authorised the write and the signature binds it.

## Hypothesis (F1, `tests/fuzz/test_convergence.py`)

- A *stateful* test (`RuleBasedStateMachine`) on three signed in-memory nodes. Rules: new cart, edit,
  add item, retry an old write with the same op_id, two nodes edit the same field, supervisor decides,
  partial delivery in any order with duplicates, full sync, tampered delivery.
- *Invariants* after every step: at most one field receipt per field, a field receipt's provisional
  value is one of its candidates, a CLOSE_VS_NEW_INFO item shows the derived status, clocks never go back.
- It runs on the same `derive()` the regions run; it found that a CLOSE_VS_NEW_INFO item could report
  "closed" while every screen showed "open" (fixed in the merge).
- *Teardown:* deliver everything everywhere in different orders. Then require the same events, views,
  state hash and digest, everything acknowledged present, and a retried add counted once.
- `derandomize=True` runs the same cases every time, so CI is reproducible; `FUZZ_RANDOM=1` runs fresh
  cases. When a run fails, Hypothesis *shrinks* it to a minimal sequence of steps.
- Quote the real count from `tests/fuzz/last_result.json` (last run: 211 examples), never a round number.

## Zero-loss failover (T17, `tests/failover/`)

- The browser keeps every signed event it was acknowledged. It drops one when any peer's
  acknowledged head for that origin reaches its seq.
- The node dies, and the next request times out. The browser carries the outbox to the next node, then
  retries with the **same** op_id.
- If the dead node had committed that write before dying, there are now two events with one op_id.
  derive() counts them once (`test_t17_a_write_that_committed_but_never_answered...`).
- The stated limit: an event is lost only if its node dies before any peer has it **and** no browser
  outbox holds it.
