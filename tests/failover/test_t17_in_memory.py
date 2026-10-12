"""T17 zero-loss failover and T18 carry integrity, on in-memory nodes (owner: Rahil).

Three signed nodes (region.engine.model.Replica) answer the contract v1.1 routes the
SDK uses: write, /v1/carry, /v1/events/has, /v1/status, /healthz. Carry admits events
with region/engine/admit.py, the rule /v1/carry must use. A node is "killed" by making
it unreachable with everything it wrote still on its disk, exactly what kill -9 leaves.

The same Shopper drives real processes in test_t17_live.py.
"""
import json
from collections import Counter

import pytest

from failover_harness import Shopper, Unreachable
from region.engine import admit, canonical, signing
from region.engine.model import Replica

REGIONS = ("mum", "pun", "del")
KEYS = {r: signing.generate() for r in REGIONS}
PUBLIC = {r: public for r, (_, public) in KEYS.items()}
CART = "R-0000a11c"
PRODUCTS = ["FCOFF-500", "GIFT-500", "MAT-01", "CHAI-01", "BAG-01", "LAMP-01"]


class Node:
    """One region behind the contract routes. Its events survive a kill, like the SQLite file."""

    def __init__(self, region):
        self.region, self.replica, self.up = region, Replica(region, KEYS[region][0]), True
        self.acked = {}               # peer -> {origin: seq} the peer holds, learnt at each sync
        self.lose_next_answer = False  # commit the next write, then never answer (timeout after commit)
        self.carry = Counter()

    def handle(self, method, path, body, now):
        parts = path.strip("/").split("/")
        if (method, path) == ("GET", "/healthz"):
            return 200, {"region": self.region, "ok": True}
        if (method, path) == ("GET", "/v1/status"):
            return 200, {"region": self.region, "heads": self.replica.heads(),
                         "peers": {p: {"acked": a} for p, a in self.acked.items()}}
        if (method, path) == ("POST", "/v1/carry"):
            return 200, {"results": [self._carry_one(e, now) for e in body["events"][:200]]}
        if (method, path) == ("POST", "/v1/events/has"):
            return 200, {"missing": [i for i in body["ids"] if i not in self.replica.events]}
        if (method, path) == ("POST", "/v1/reports"):
            value = {k: body[k] for k in ("title", "description", "category", "severity")}
            return self._write(now, body, "CREATE", body["report_id"], value=dict(value, status="open"))
        if method == "POST" and parts[-1] == "notes":
            return self._write(now, body, "NOTE", parts[2], value={"text": body["text"]})
        if method == "PATCH":
            return self._write(now, body, "SET", parts[2], body["field"], body["value"])
        return 404, None

    def _write(self, now, body, type_, rid, field=None, value=None):
        held = set(self.replica.events)
        try:
            ev = self.replica.write(now, type_, rid, field, value, op_id=body["op_id"])
        except KeyError:
            return 404, None
        if self.lose_next_answer:
            self.lose_next_answer = False
            raise Unreachable(f"{self.region} committed {ev['event_id']} but the answer never arrived")
        code = 201 if ev["event_id"] not in held else 200  # 200: the op_id was already here
        return code, {"report": self.replica.view(ev["report_id"]), "event": ev}

    def _carry_one(self, ev, now):
        eid = ev.get("event_id") if isinstance(ev, dict) else None
        reason = admit.reject_reason(ev, PUBLIC)
        if reason:
            result = {"event_id": eid, "result": "rejected", "reason": reason}
        elif eid in self.replica.events:
            same = self.replica.events[eid]["hash"] == ev["hash"]
            result = {"event_id": eid, "result": "dup"} if same else \
                {"event_id": eid, "result": "rejected", "reason": "id_reused"}
        else:
            self.replica.receive([ev], now, PUBLIC)
            result = {"event_id": eid, "result": "new"}
        self.carry[result["result"]] += 1
        return result


class Cluster:
    def __init__(self):
        self.nodes = {r: Node(r) for r in REGIONS}
        self.now = 1_760_277_060_000

    def send(self, region, method, path, body):
        node = self.nodes[region]
        if not node.up:
            raise Unreachable(f"{region} is down")
        self.now += 7
        return node.handle(method, path, json.loads(json.dumps(body)) if body else body, self.now)

    def sync(self, a, b):
        """Pull both ways between two live nodes; each learns what the other now holds."""
        na, nb = self.nodes[a], self.nodes[b]
        if na.up and nb.up:
            nb.replica.receive(list(na.replica.events.values()), self.now, PUBLIC)
            na.replica.receive(list(nb.replica.events.values()), self.now, PUBLIC)
            na.acked[b], nb.acked[a] = nb.replica.heads(), na.replica.heads()

    def sync_all(self):
        for _ in range(2):
            for i, a in enumerate(REGIONS):
                for b in REGIONS[i + 1:]:
                    self.sync(a, b)

    def has(self, region, ids):
        return self.send(region, "POST", "/v1/events/has", {"ids": list(ids)})[1]["missing"]


def add(shop, sku, user="Alice"):
    text = json.dumps({"kind": "ADD", "sku": sku, "qty": 1, "user": user}, separators=(",", ":"))
    return shop.call("POST", f"/v1/reports/{CART}/notes", {"text": text})


def open_cart(shop):
    return shop.call("POST", "/v1/reports", {"report_id": CART, "title": "Alice", "description": "",
                                            "category": "cart", "severity": 1})


def items(node):
    return [json.loads(n["text"])["sku"] for n in node.replica.view(CART)["state"]["notes"]]


# ---- T17 ---------------------------------------------------------------------
def test_t17_a_node_dies_holding_writes_no_peer_has_and_nothing_acknowledged_is_lost():
    c = Cluster()
    shop = Shopper(c.send, REGIONS)
    assert open_cart(shop)[0] == 201
    for sku in PRODUCTS[:3]:
        assert add(shop, sku)[0] == 201
    c.sync_all()
    shop.prune()
    assert shop.outbox == {}  # everything replicated, so the browser holds nothing

    for sku in PRODUCTS[3:]:  # Mumbai acknowledges three adds, then dies before any peer copies them
        assert add(shop, sku)[0] == 201
    shop.prune()
    at_risk = [i for i in shop.outbox if i.startswith("mum:")]
    assert len(at_risk) == 3 and c.has("pun", at_risk) == at_risk  # only Mumbai's disk and the outbox hold them
    c.nodes["mum"].up = False  # kill -9

    for sku in ("KURTA-01", "BOTTLE-01"):  # the shopper keeps adding; the first add fails over
        assert add(shop, sku)[0] == 201
    assert shop.errors == 0
    assert shop.timeline[-2][:2] == ("pun", 1) and shop.timeline[-1][:2] == ("pun", 0)  # one slower request
    assert sorted(r["event_id"] for r in shop.carried if r["result"] == "new") == sorted(at_risk)

    c.sync("pun", "del")
    for survivor in ("pun", "del"):  # acknowledged = present on survivors
        assert c.has(survivor, shop.acknowledged) == []
    assert items(c.nodes["pun"]) == PRODUCTS + ["KURTA-01", "BOTTLE-01"]

    c.nodes["mum"].up = True  # restart: replay the log, pull what it missed
    c.sync_all()
    reps = [c.nodes[r].replica for r in REGIONS]
    assert len({r.event_digest() for r in reps}) == 1 and len({r.state_hash() for r in reps}) == 1  # CONVERGED
    assert items(c.nodes["mum"]) == PRODUCTS + ["KURTA-01", "BOTTLE-01"]  # no duplicate items
    assert reps[0].view(CART)["conflicts"] == []


def test_t17_a_write_that_committed_but_never_answered_is_retried_once_and_counts_once():
    c = Cluster()
    shop = Shopper(c.send, REGIONS)
    open_cart(shop)
    add(shop, "FCOFF-500")
    c.nodes["mum"].lose_next_answer = True  # Mumbai commits the gift card; the answer times out
    status, resp = add(shop, "GIFT-500")
    assert status == 201 and resp["event"]["origin"] == "pun" and shop.errors == 0  # retried on Pune, same op_id
    c.sync_all()
    copies = [e for e in c.nodes["mum"].replica.events.values() if e["type"] == "NOTE" and "GIFT" in e["value"]["text"]]
    assert len(copies) == 2 and len({e["op_id"] for e in copies}) == 1  # two events, one write
    for r in REGIONS:
        assert items(c.nodes[r]) == ["FCOFF-500", "GIFT-500"] and c.nodes[r].replica.view(CART)["conflicts"] == []


def test_t17_a_retry_at_a_node_that_already_has_the_op_returns_the_original():
    c = Cluster()
    shop = Shopper(c.send, REGIONS)
    open_cart(shop)
    _, first = add(shop, "MAT-01")
    c.sync_all()
    again = shop.call("POST", f"/v1/reports/{CART}/notes", {"text": "ignored", "op_id": first["event"]["op_id"]})
    assert again[0] == 200 and again[1]["event"] == first["event"]
    c.nodes["mum"].up = False
    on_pun = shop.call("POST", f"/v1/reports/{CART}/notes", {"text": "ignored", "op_id": first["event"]["op_id"]})
    assert on_pun[0] == 200 and on_pun[1]["event"]["event_id"] == first["event"]["event_id"]  # arrived by sync
    assert items(c.nodes["pun"]) == ["MAT-01"]


def test_t17_failback_after_three_good_probes_carries_what_the_survivor_acknowledged():
    c = Cluster()
    shop = Shopper(c.send, REGIONS)
    open_cart(shop)
    c.nodes["mum"].up = False
    add(shop, "CHAI-01")  # acknowledged by Pune while Mumbai is dead
    c.nodes["mum"].up = True
    shop.probe(); shop.probe()
    assert "mum" in shop.down  # two good probes are not enough
    shop.probe()
    assert "mum" not in shop.down
    status, resp = add(shop, "LAMP-01")
    assert status == 201 and resp["event"]["origin"] == "mum"  # back on the preferred node
    assert c.has("mum", shop.acknowledged) == []  # Pune's add reached Mumbai through the carry
    assert shop.errors == 0


def test_t17_every_node_down_is_the_only_error_and_the_outbox_is_kept():
    c = Cluster()
    shop = Shopper(c.send, REGIONS)
    open_cart(shop)
    for node in c.nodes.values():
        node.up = False
    assert add(shop, "BAG-01") == (None, None)
    assert shop.errors == 1 and len(shop.outbox) == 1


def test_the_outbox_never_grows_past_its_bound():
    c = Cluster()
    shop = Shopper(c.send, REGIONS, max_outbox=4)
    open_cart(shop)
    for sku in PRODUCTS:
        add(shop, sku)
    assert len(shop.outbox) == 4 and list(shop.outbox)[-1] == f"mum:{len(PRODUCTS) + 1}"  # the oldest go first


# ---- T18 -----------------------------------------------------------------------
def test_t18_carry_rejects_forged_edited_and_unknown_events_and_changes_nothing():
    c = Cluster()
    shop = Shopper(c.send, REGIONS)
    open_cart(shop)
    _, resp = add(shop, "EARBUD-01")
    good = resp["event"]
    pun = c.nodes["pun"].replica
    before = (pun.event_digest(), pun.state_hash())

    edited = dict(good, value={"text": '{"kind":"ADD","sku":"EARBUD-01","qty":50}'})
    rehashed = dict(edited, hash=canonical.event_hash(edited))
    forged = dict(rehashed, sig=signing.sign_with(KEYS["pun"][0], rehashed["hash"]))
    stranger = dict(good, origin="xyz", event_id="xyz:2")
    bad = [edited, rehashed, forged, stranger, {"event_id": "mum:9"}]
    results = c.send("pun", "POST", "/v1/carry", {"events": bad})[1]["results"]
    assert [(r["result"], r["reason"]) for r in results] == [
        ("rejected", "bad_hash"), ("rejected", "bad_sig"), ("rejected", "bad_sig"),
        ("rejected", "unknown_origin"), ("rejected", "shape")]
    assert (pun.event_digest(), pun.state_hash()) == before


def test_t18_a_duplicate_carry_is_dup_and_state_is_unchanged():
    c = Cluster()
    shop = Shopper(c.send, REGIONS)
    open_cart(shop)
    add(shop, "SANDAL-01")
    outbox = list(shop.outbox.values())
    first = c.send("pun", "POST", "/v1/carry", {"events": outbox})[1]["results"]
    snapshot = (c.nodes["pun"].replica.event_digest(), c.nodes["pun"].replica.state_hash())
    again = c.send("pun", "POST", "/v1/carry", {"events": outbox + outbox})[1]["results"]
    assert {r["result"] for r in first} == {"new"} and {r["result"] for r in again} == {"dup"}
    assert (c.nodes["pun"].replica.event_digest(), c.nodes["pun"].replica.state_hash()) == snapshot
    c.sync_all()  # the same events also arrive by replication: still one copy each
    assert items(c.nodes["pun"]) == ["SANDAL-01"]


@pytest.mark.parametrize("seed", range(10))
def test_t17_zero_loss_holds_whenever_the_node_dies(seed):
    """Kill Mumbai after a different number of adds, 0 to 3 of them not yet copied by any peer."""
    c = Cluster()
    shop = Shopper(c.send, REGIONS)
    open_cart(shop)
    at_risk = None
    for i in range(12):
        if i % 4 == 0:  # replication catches up every fourth add
            c.sync("mum", "pun")
            shop.prune()
        if i == seed + 2:
            at_risk = sorted(set(c.nodes["mum"].replica.events) - set(c.nodes["pun"].replica.events))
            c.nodes["mum"].up = False
        add(shop, PRODUCTS[i % len(PRODUCTS)])
    c.sync_all()
    assert len(at_risk) == (seed + 2) % 4
    assert sorted(r["event_id"] for r in shop.carried if r["result"] == "new") == at_risk  # carried, nothing more
    assert shop.errors == 0 and len(shop.acknowledged) == 13
    assert c.has("pun", shop.acknowledged) == [] and c.has("del", shop.acknowledged) == []
    assert len(items(c.nodes["pun"])) == 12
