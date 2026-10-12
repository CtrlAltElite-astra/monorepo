"""The plan's numbered gate tests that Lane B owns (owner: Rahil). Plan section 10.

T1 runs the real store and ingest on a temp database; the others are pure.
"""
import itertools
import os

os.environ.setdefault("ENV_FILE", "none")  # never read a developer's .env

from region import store  # noqa: E402
from region.engine import canonical, digest  # noqa: E402
from region.engine.derive import derive  # noqa: E402
from region.engine.model import make_event as mk  # noqa: E402
from region.sync import ingest  # noqa: E402

NEW_CART = {"title": "Alice", "description": "", "category": "cart", "severity": 1, "status": "open",
            "assigned_team": None, "location": None, "people_estimate": None}


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


def test_t1_same_pages_twice_change_nothing(tmp_path):
    """T1 idempotent ingest: the same pages again leave event count, digest and state hash unchanged."""
    events = six("pun", "del")  # two peers, the way this node receives them
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
