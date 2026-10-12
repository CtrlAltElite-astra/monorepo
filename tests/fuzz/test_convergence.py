"""F1 randomized convergence (owner: Rahil). Plan 6.8 and section 10.

Three in-memory nodes (region.engine.model.Replica: the real engine and write
rule, every event signed). Hypothesis makes random writes, retries of earlier
writes under the same op_id, supervisor decisions, partial deliveries in any
order with duplicates, tampered deliveries, clocks that jump backwards, and a
shopper whose credential was revoked who keeps sending (a fourth region that has
not heard of the revocation signs those writes; every replica must refuse them).
At the end every node gets everything and all three must agree byte for byte.

    pytest tests/fuzz -q                                    200 examples, the same ones every run
    FUZZ_EXAMPLES=2000 FUZZ_RANDOM=1 pytest tests/fuzz -q   a longer run with fresh random cases

The counts of the last run are written to tests/fuzz/last_result.json.
"""
import json
import os
from collections import Counter
from pathlib import Path

import hypothesis
from hypothesis import HealthCheck, given, settings, strategies as st
from hypothesis.stateful import (RuleBasedStateMachine, initialize, invariant, precondition, rule,
                                 run_state_machine_as_test)

from contracts.schemas import CATEGORIES, STATUSES
from region.engine import admit, canonical, signing
from region.engine.derive import derive
from region.engine.model import Replica
from region.engine.derive import POLICY_VERSION

EXAMPLES = int(os.environ.get("FUZZ_EXAMPLES", "200"))
RANDOM = os.environ.get("FUZZ_RANDOM") == "1"
ROOT = Path(__file__).resolve().parents[2]
RESULT = Path(__file__).with_name("last_result.json")

REGIONS = ("mum", "pun", "del")
KEYS = {r: signing.generate() for r in REGIONS}  # (private, public) per node
PUBLIC = {r: public for r, (_, public) in KEYS.items()}
STALE = "blr"                    # Bengaluru: still signs writes from a credential revoked elsewhere
STALE_KEY = signing.generate()
PUBLIC[STALE] = STALE_KEY[1]     # a known region, so its events pass every check but revocation
REVOKED = "cred-revoked-esha"
IS_REVOKED = {REVOKED}.__contains__  # every replica knows the revocation
STATS = Counter()
CLOCK = st.integers(0, 40)  # tiny and unordered: wall clocks jump back and timestamps tie across nodes


def pin(dlat=0):
    return {"lat_e6": 19_072_800 + dlat, "lon_e6": 72_882_600}


VALUES = {
    "title": ["Alice", "Alice S", "A. Sharma"],
    "description": ["", "leave at the gate"],
    "category": list(CATEGORIES[:2]),
    "severity": [1, 2, 3, 4, 5],
    "status": list(STATUSES),
    "assigned_team": [None, "Bhiwandi", "Chakan", "Gurgaon"],
    "location": [None, pin(), pin(900), pin(4500)],  # 100 m and 500 m from the first pin
    "people_estimate": [None, 0, 20, 25],
}
ITEMS = ["gift card", "filter coffee", "yoga mat"]


class Shop(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.nodes = {r: Replica(r, KEYS[r][0]) for r in REGIONS}
        self.ops = []    # every client write so far: (op_id, type, report_id, field, value, reason)
        self.acked = {}  # every event a node has acknowledged
        self.carts = 0
        self.last_clock = {r: (0, 0) for r in REGIONS}
        self.stale = Replica(STALE, STALE_KEY[0])  # not a replica: only a source of revoked writes
        self.revoked = set()                       # ids of every event written for the revoked credential

    def _write(self, region, now, type_, rid, field=None, value=None, reason=None, op=None):
        node = self.nodes[region]
        if op is None:
            op = f"op-{len(self.ops)}"
            self.ops.append((op, type_, rid, field, value, reason))
        seen = [tuple(e["hlc"]) for e in node.events.values()]
        held = set(node.events)
        ev = node.write(now, type_, rid, field, value, op_id=op, reason=reason)
        if ev["event_id"] not in held:  # a new event, not the answer to an op the node already holds
            assert all(tuple(ev["hlc"]) > h for h in seen)  # stamped later than everything the node had seen
            assert signing.verify_with(PUBLIC[region], ev["hash"], ev["sig"])
            STATS["writes"] += 1
        self.acked[ev["event_id"]] = ev

    def _known(self):
        return [(r, rid) for r in REGIONS for rid in sorted({e["report_id"] for e in self.nodes[r].events.values()})]

    def _shared(self):
        """[(cart, the nodes that hold it)] for carts on two or more nodes."""
        holders = {}
        for region, rid in self._known():
            holders.setdefault(rid, []).append(region)
        return [(rid, regions) for rid, regions in sorted(holders.items()) if len(regions) > 1]

    def _open_items(self):
        return [(r, rid, c["field"]) for r in REGIONS for rid, v in self.nodes[r].views().items()
                for c in v["conflicts"]]

    # ---- writes ---------------------------------------------------------
    @initialize(region=st.sampled_from(REGIONS), now=CLOCK)
    def a_cart_every_node_already_has(self, region, now):
        """Start with one replicated cart, so two nodes can disagree about it from the first step."""
        self.new_cart(region=region, now=now, owner="Alice", speed=1)
        for other in REGIONS:
            self.nodes[other].receive(list(self.nodes[region].events.values()), now, PUBLIC, IS_REVOKED)

    @precondition(lambda self: self.carts < 3)
    @rule(region=st.sampled_from(REGIONS), now=CLOCK, owner=st.sampled_from(["Alice", "Bob"]), speed=st.integers(1, 5))
    def new_cart(self, region, now, owner, speed):
        self.carts += 1
        value = {"title": owner, "description": "", "category": "cart", "severity": speed, "status": "open",
                 "assigned_team": None, "location": None, "people_estimate": None}
        self._write(region, now, "CREATE", f"R-{self.carts:08x}", value=value)

    @precondition(lambda self: self.carts)
    @rule(data=st.data(), now=CLOCK, field=st.sampled_from(sorted(VALUES)))
    def edit(self, data, now, field):
        region, rid = data.draw(st.sampled_from(self._known()))
        self._write(region, now, "SET", rid, field, data.draw(st.sampled_from(VALUES[field])))

    @precondition(lambda self: self.carts)
    @rule(data=st.data(), now=CLOCK, item=st.sampled_from(ITEMS))
    def add_item(self, data, now, item):
        region, rid = data.draw(st.sampled_from(self._known()))
        self._write(region, now, "NOTE", rid, value={"text": item})

    @precondition(lambda self: self.ops)
    @rule(data=st.data(), now=CLOCK)
    def retry(self, data, now):
        """A browser repeats an earlier write, with the same op_id, on a node of its choice."""
        op, type_, rid, field, value, reason = self.ops[data.draw(st.integers(0, len(self.ops) - 1))]
        region = data.draw(st.sampled_from([r for r in REGIONS if type_ == "CREATE" or self.nodes[r].of(rid)]))
        self._write(region, now, type_, rid, field, value, reason, op=op)
        STATS["retries"] += 1

    @precondition(lambda self: self._shared())
    @rule(data=st.data(), now=CLOCK, field=st.sampled_from(sorted(VALUES)))
    def two_nodes_edit_the_same_field(self, data, now, field):
        """Two nodes that both hold a cart write one field without seeing each other."""
        rid, holders = data.draw(st.sampled_from(self._shared()))
        first, second = data.draw(st.permutations(holders))[:2]
        mine, theirs = data.draw(st.permutations(VALUES[field]))[:2]
        self._write(first, now, "SET", rid, field, mine)
        self._write(second, data.draw(CLOCK), "SET", rid, field, theirs)

    @precondition(lambda self: self._open_items())
    @rule(data=st.data(), now=CLOCK)
    def supervisor_decides(self, data, now):
        region, rid, field = data.draw(st.sampled_from(self._open_items()))
        self._write(region, now, "RESOLVE", rid, field, data.draw(st.sampled_from(VALUES[field])), reason="fuzz")
        STATS["decisions"] += 1

    # ---- the network ----------------------------------------------------
    @rule(data=st.data(), src=st.sampled_from(REGIONS), dst=st.sampled_from(REGIONS), now=CLOCK)
    def deliver(self, data, src, dst, now):
        """Some of what one node holds reaches another: any subset, any order, possibly twice."""
        held = sorted(self.nodes[src].events)
        if src != dst and held:
            ids = data.draw(st.lists(st.sampled_from(held), max_size=10))
            self.nodes[dst].receive([self.nodes[src].events[i] for i in ids], now, PUBLIC, IS_REVOKED)
            STATS["deliveries"] += 1

    @rule(src=st.sampled_from(REGIONS), dst=st.sampled_from(REGIONS), now=CLOCK)
    def sync(self, src, dst, now):
        """One node pulls everything another holds."""
        if src != dst and self.nodes[src].events:
            self.nodes[dst].receive(list(self.nodes[src].events.values()), now, PUBLIC, IS_REVOKED)
            STATS["full_syncs"] += 1

    @rule(data=st.data(), src=st.sampled_from(REGIONS), dst=st.sampled_from(REGIONS), now=CLOCK,
          how=st.sampled_from(["edited", "edited and re-hashed", "signed by another node"]))
    def tampered_delivery(self, data, src, dst, now, how):
        held = sorted(i for i in self.nodes[src].events if i not in self.nodes[dst].events)
        if src != dst and held:
            original = self.nodes[src].events[data.draw(st.sampled_from(held))]
            bad = dict(original, reason="tampered")
            if how != "edited":
                bad["hash"] = canonical.event_hash(bad)  # the hash fits again, the signature does not
            if how == "signed by another node":
                forger = next(r for r in REGIONS if r != original["origin"])
                bad["sig"] = signing.sign_with(KEYS[forger][0], bad["hash"])
            before = self.nodes[dst].event_digest()
            assert self.nodes[dst].receive([bad], now, PUBLIC, IS_REVOKED) == [] and self.nodes[dst].event_digest() == before
            STATS["tampered_rejected"] += 1

    @precondition(lambda self: self.carts)
    @rule(data=st.data(), src=st.sampled_from(REGIONS), dst=st.sampled_from(REGIONS), now=CLOCK,
          item=st.sampled_from(ITEMS))
    def revoked_actor_keeps_sending(self, data, src, dst, now, item):
        """Bengaluru learns a cart, accepts an add from the revoked credential and signs it; a replica refuses it."""
        self.stale.receive(list(self.nodes[src].events.values()), now, PUBLIC)  # Bengaluru does not know
        carts = sorted({e["report_id"] for e in self.stale.events.values()})
        if not carts:
            return
        ev = self.stale.write(now, "NOTE", data.draw(st.sampled_from(carts)), value={"text": item},
                              credential_id=REVOKED)
        self.revoked.add(ev["event_id"])
        assert admit.reject_reason(ev, PUBLIC, is_revoked=IS_REVOKED) == "revoked_credential"
        before = self.nodes[dst].event_digest()
        assert self.nodes[dst].receive([ev], now, PUBLIC, IS_REVOKED) == []
        assert self.nodes[dst].event_digest() == before
        STATS["revoked_rejected"] += 1

    # ---- what must hold after every step ----------------------------------
    @invariant()
    def every_view_is_consistent(self):
        for node in self.nodes.values():
            for view in node.views().values():
                fields = [c["field"] for c in view["conflicts"] if c["rule"] != "CLOSE_VS_NEW_INFO"]
                assert len(fields) == len(set(fields))  # at most one field receipt per field
                for c in view["conflicts"]:
                    assert c["provisional"] == view["state"][c["field"]]
                    if c["rule"] == "CLOSE_VS_NEW_INFO":  # the live close, then what it did not see
                        assert c["field"] == "status" and c["event_ids"][0] in view["live"]["status"]
                        assert c["needs_review"] is True and c["flags"] == ["CLOSE_VS_NEW_INFO"]
                    else:
                        assert c["event_ids"] == view["live"][c["field"]]
                        assert canonical.dumps(c["provisional"]) in {canonical.dumps(x) for x in c["values"]}  # never invented
                    assert c["policy_version"] == POLICY_VERSION
                assert all(i in node.events for ids in view["live"].values() for i in ids)
                notes = [n["id"] for n in view["state"]["notes"]]
                assert len(notes) == len(set(notes))

    @invariant()
    def clocks_never_go_back(self):
        for r, node in self.nodes.items():
            assert node.clock >= self.last_clock[r]
            self.last_clock[r] = node.clock

    # ---- the end: everything is delivered everywhere ----------------------
    def teardown(self):
        union = {i: e for node in self.nodes.values() for i, e in node.events.items()}
        everything = list(union.values())
        orders = (everything, everything[::-1], sorted(everything, key=lambda e: e["hash"]))
        for node, events in zip(self.nodes.values(), orders):
            node.receive(events + events[:3], 99, PUBLIC, IS_REVOKED)  # a different order per node, some events twice
        from_bengaluru = list(self.stale.events.values())  # its revoked writes, and its copies of everything else
        for node in self.nodes.values():
            node.receive(from_bengaluru, 99, PUBLIC, IS_REVOKED)
            assert not set(node.events) & self.revoked  # none of the revoked writes is stored anywhere
        assert all(e.get("credential_id") != REVOKED for node in self.nodes.values() for e in node.events.values())
        a, b, c = self.nodes.values()
        assert set(a.events) == set(b.events) == set(c.events) >= set(self.acked)  # nothing acknowledged is missing
        assert a.event_digest() == b.event_digest() == c.event_digest()
        assert canonical.dumps(a.views()) == canonical.dumps(b.views()) == canonical.dumps(c.views())
        assert a.state_hash() == b.state_hash() == c.state_hash()
        for rid, view in a.views().items():
            adds = {e["op_id"] for e in a.of(rid) if e["type"] == "NOTE"}
            assert len(view["state"]["notes"]) == len(adds)  # a retried add is still one item
            assert view["incomplete"] is False
        assert a.receive(everything, 100, PUBLIC, IS_REVOKED) == []  # delivering it all again changes nothing
        STATS["examples"] += 1
        STATS["events"] += len(union)


def test_f1_three_nodes_always_converge():
    STATS.clear()
    run_state_machine_as_test(Shop, settings=settings(
        max_examples=EXAMPLES, stateful_step_count=50, deadline=None, derandomize=not RANDOM,
        suppress_health_check=list(HealthCheck)))
    assert STATS["examples"] >= EXAMPLES, "fewer examples ran than asked for"
    RESULT.write_text(json.dumps({
        "test": "F1 randomized convergence",
        "result": "passed",
        "examples": STATS["examples"],
        "nodes": len(REGIONS),
        "writes": STATS["writes"],
        "retries_with_same_op_id": STATS["retries"],
        "supervisor_decisions": STATS["decisions"],
        "partial_deliveries": STATS["deliveries"],
        "full_syncs": STATS["full_syncs"],
        "tampered_deliveries_rejected": STATS["tampered_rejected"],
        "revoked_credential_events_rejected": STATS["revoked_rejected"],
        "events_compared_at_the_end": STATS["events"],
        "cases": "fresh random" if RANDOM else "fixed (same every run)",
        "policy_version": POLICY_VERSION,
        "hypothesis": hypothesis.__version__,
    }, indent=2) + "\n", encoding="utf-8")


# ---- derive() is total: no input makes it raise ----------------------------
REAL = json.loads((ROOT / "fixtures" / "events_split_brain.json").read_text(encoding="utf-8"))["events"]
JUNK = st.recursive(
    st.none() | st.booleans() | st.integers(-5, 5) | st.floats(allow_nan=False) | st.text(max_size=4),
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=4), inner, max_size=3), max_leaves=5)
BREAKABLE = ["event_id", "origin", "seq", "type", "field", "value", "supersedes", "hlc", "op_id", "observed", "actor"]
BROKEN = st.builds(lambda event, key, junk: {**event, key: junk}, st.sampled_from(REAL), st.sampled_from(BREAKABLE), JUNK)


@settings(max_examples=300, deadline=None, derandomize=not RANDOM)
@given(st.lists(st.sampled_from(REAL) | BROKEN | JUNK, max_size=12))
def test_derive_never_raises_whatever_it_is_given(events):
    view = derive(events)
    canonical.dumps(view)  # and the result is still plain JSON
    assert set(view) == {"state", "live", "conflicts", "incomplete", "policy_version"}
