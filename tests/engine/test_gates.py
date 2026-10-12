"""The plan's numbered gate tests that Lane B owns (owner: Rahil). Plan section 10.

T1 runs the real store and ingest on a temp database; the others are pure.
T4 and T6 replay the worked example of plan 10.1 on in-memory nodes.
"""
import itertools
import json
import os

os.environ.setdefault("ENV_FILE", "none")  # never read a developer's .env

from region import store  # noqa: E402
from region.engine import canonical, digest, signing  # noqa: E402
from region.engine.derive import derive  # noqa: E402
from region.engine.model import Replica, make_event as mk  # noqa: E402
from region.sync import ingest  # noqa: E402

T0 = 1_791_813_000_000  # 12 Oct 2026, 19:20 IST
NEW_CART = {"title": "Alice", "description": "", "category": "cart", "severity": 1, "status": "open",
            "assigned_team": None, "location": None, "people_estimate": None}


def heal(*nodes, at: int) -> None:
    """The link is back: every node receives what the others hold."""
    everything = [e for n in nodes for e in n.events.values()]
    for n in nodes:
        n.receive(everything, at)


def split_brain():
    """Plan 10.1: Alice's cart is on both nodes, the link is cut, and both keep writing."""
    mum, pun, rid = Replica("mum"), Replica("pun"), "R-a11ce001"
    pun.receive([mum.write(T0, "CREATE", rid, value=dict(NEW_CART), op_id="alice-1")], T0 + 1_000)
    # --- link mum-pun cut ---
    mum.write(T0 + 10_000, "SET", rid, "assigned_team", "Bhiwandi", op_id="alice-2")
    pun.write(T0 + 11_000, "SET", rid, "assigned_team", "Chakan", op_id="ops-pun-1")
    mum.write(T0 + 20_000, "SET", rid, "severity", 5, op_id="alice-3")       # Priority delivery
    pun.write(T0 + 21_000, "SET", rid, "severity", 4, op_id="ops-pun-2")     # Same-day delivery
    mum.write(T0 + 30_000, "SET", rid, "status", "closed", op_id="alice-4")  # order placed on Mumbai
    gift = {"kind": "ADD", "sku": "GIFT-500", "name": "Gift card", "qty": 1, "price": 500, "user": "Alice"}
    pun.write(T0 + 31_000, "NOTE", rid, value={"text": json.dumps(gift, separators=(",", ":"))}, op_id="alice-5")
    # --- healed ---
    heal(mum, pun, at=T0 + 60_000)
    return mum, pun, rid


def six(a="mum", b="pun"):
    """Alice's cart is made on node a, then a and b both write while cut off."""
    return [
        mk(a, 1, "CREATE", value=NEW_CART, hlc=(1000, 0)),
        mk(a, 2, "SET", "severity", 5, [f"{a}:1"], hlc=(2000, 0)),
        mk(b, 1, "SET", "severity", 4, [f"{a}:1"], hlc=(2100, 0)),
        mk(a, 3, "SET", "assigned_team", "Bhiwandi", [f"{a}:1"], hlc=(2200, 0)),
        mk(b, 2, "SET", "assigned_team", "Chakan", [f"{a}:1"], hlc=(2300, 0)),
        mk(b, 3, "NOTE", value={"text": "gift card"}, hlc=(2400, 0)),
    ]


PEER_KEYS = {r: signing.generate() for r in ("pun", "del")}


def test_t1_same_pages_twice_change_nothing(tmp_path, provision):
    """T1 idempotent ingest: the same pages again leave event count, digest and state hash unchanged."""
    provision({}, {r: public for r, (_, public) in PEER_KEYS.items()})  # ingest verifies every origin's signature
    events = [dict(e, sig=signing.sign_with(PEER_KEYS[e["origin"]][0], e["hash"]))  # two peers, as received
              for e in six("pun", "del")]
    pages = [events[:2], events[2:5], events[5:]]
    store.init(str(tmp_path / "t1.db"))
    try:
        def snapshot():
            with store.read() as conn:
                return (store.event_count(conn), digest.event_digest(store.event_pairs(conn)),
                        digest.state_hash(store.all_views(conn, include_incomplete=True)),
                        store.quarantine_count(conn))

        assert sum(ingest.ingest(page, source="pun")["stored"] for page in pages) == 6
        first = snapshot()
        again = [ingest.ingest(page, source="del") for page in reversed(pages)]  # other peer, other order
        assert [(s["stored"], s["duplicate"]) for s in again] == [(0, 1), (0, 3), (0, 2)]
        assert snapshot() == first and first[0] == 6 and first[3] == 0
    finally:
        store.close()


def test_t2_every_arrival_order_gives_the_same_view():
    """T2 order independence: all 720 permutations of a 6-event fixture."""
    events = six()
    want = canonical.dumps(derive(events))
    assert all(canonical.dumps(derive(order)) == want for order in itertools.permutations(events))
    assert [c["field"] for c in derive(events)["conflicts"]] == ["severity", "assigned_team"]


def test_t5_changes_to_different_fields_both_survive_without_conflict():
    """T5 disjoint fields: paid on one node, faster delivery on the other."""
    cart = six()[0]
    paid = mk("mum", 2, "SET", "status", "resolved", ["mum:1"], hlc=(2000, 0))
    faster = mk("pun", 1, "SET", "severity", 3, ["mum:1"], hlc=(2001, 0))
    v = derive([cart, faster, paid])
    assert v["state"]["status"] == "resolved" and v["state"]["severity"] == 3 and v["conflicts"] == []


def test_t4_split_brain_outcomes_are_the_same_on_both_nodes():
    """T4: the table of plan 10.1, after both nodes wrote Alice's cart while cut off."""
    mum, pun, rid = split_brain()
    assert canonical.dumps(mum.view(rid)) == canonical.dumps(pun.view(rid))
    assert (mum.state_hash(), mum.event_digest()) == (pun.state_hash(), pun.event_digest())
    view = mum.view(rid)
    receipt = {c["field"]: c for c in view["conflicts"]}
    assert list(receipt) == ["severity", "assigned_team", "status"]  # field conflicts, then the review item
    # delivery speed: Mumbai 5, Pune 4 -> the highest, receipt only
    assert view["state"]["severity"] == 5
    assert (receipt["severity"]["rule"], receipt["severity"]["needs_review"]) == ("HIGHEST", False)
    # fulfilment centre: Bhiwandi was first -> kept, flagged, reviewed
    assert view["state"]["assigned_team"] == "Bhiwandi"
    assert (receipt["assigned_team"]["flags"], receipt["assigned_team"]["needs_review"]) == (["DOUBLE_DISPATCH"], True)
    # status: only Mumbai changed it, so no field conflict, but the order did not see Pune's writes
    assert view["live"]["status"] == ["mum:4"] and view["state"]["status"] == "closed"
    assert (receipt["status"]["rule"], receipt["status"]["needs_review"]) == ("CLOSE_VS_NEW_INFO", True)
    assert receipt["status"]["event_ids"] == ["mum:4", "pun:2", "pun:3"]  # the order, then what it did not see
    # cart activity: Pune's gift card is visible on Mumbai
    assert len(view["state"]["notes"]) == 1 and "Gift card" in view["state"]["notes"][0]["text"]


def test_t6_resolve_replaces_exactly_its_candidates_and_a_write_that_missed_it_reopens_review():
    """T6: a supervisor decides, the decision replicates, and an edit that never saw it is not hidden."""
    mum, pun, rid = split_brain()
    delhi, t = Replica("del"), T0 + 100_000
    delhi.receive([pun.events["mum:1"], pun.events["pun:1"]], t)  # Delhi only ever got the cart and Pune's centre

    candidates = mum.view(rid)["live"]["assigned_team"]
    decision = mum.write(t + 1_000, "RESOLVE", rid, "assigned_team", "Chakan", reason="Chakan has the stock")
    assert decision["supersedes"] == candidates == ["mum:2", "pun:1"] and decision["priority"] == 0
    view = mum.view(rid)
    assert view["state"]["assigned_team"] == "Chakan" and view["live"]["assigned_team"] == ["mum:5"]
    assert [c["field"] for c in view["conflicts"]] == ["severity", "status"]  # the other items are untouched

    late = delhi.write(t + 5_000, "SET", rid, "assigned_team", "Gurgaon")  # written without the decision
    assert late["supersedes"] == ["pun:1"]
    heal(mum, pun, delhi, at=t + 9_000)
    for node in (mum, pun, delhi):
        (again,) = [c for c in node.view(rid)["conflicts"] if c["field"] == "assigned_team"]
        assert again["event_ids"] == ["mum:5", "del:1"] and again["provisional"] == "Chakan"  # the earlier of the two
        assert again["needs_review"] is True and again["flags"] == ["DOUBLE_DISPATCH"]

    final = pun.write(t + 20_000, "RESOLVE", rid, "assigned_team", "Gurgaon", reason="Delhi already shipped")
    assert final["supersedes"] == ["mum:5", "del:1"]
    heal(mum, pun, delhi, at=t + 30_000)
    assert len({n.state_hash() for n in (mum, pun, delhi)}) == 1
    assert mum.view(rid)["state"]["assigned_team"] == "Gurgaon"
    assert "assigned_team" not in [c["field"] for c in mum.view(rid)["conflicts"]]
