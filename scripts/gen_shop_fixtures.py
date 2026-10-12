"""Regenerate fixtures/events_*.json from the real engine (owner: Rahil).   python scripts/gen_shop_fixtures.py

Three shop scenarios for the web mock, the scenario scripts and the tests. Each
file holds {"about", "events", "report"}: every event the nodes hold at the end,
and the view derive() gives for them, so a screen can be built without a node.
tests/engine/test_fixtures.py fails if a file and the engine drift apart.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from region.engine.derive import order  # noqa: E402
from region.engine.model import Replica  # noqa: E402

OUT = ROOT / "fixtures"
T0 = 1_791_813_000_000  # 12 Oct 2026, 19:20 IST


def cart(owner: str) -> dict:
    """A new cart as the write path stores it: delivery speed 1 (Standard), status open (Shopping)."""
    return {"title": owner, "description": "", "category": "cart", "severity": 1, "status": "open",
            "assigned_team": None, "location": None, "people_estimate": None}


def add(sku: str, name: str, price: int, user: str) -> dict:
    """One cart-activity note (plan 4.1)."""
    item = {"kind": "ADD", "sku": sku, "name": name, "qty": 1, "price": price, "user": user}
    return {"text": json.dumps(item, separators=(",", ":"))}


def heal(*nodes, at: int) -> None:
    """The link is back: every node receives what the others hold."""
    everything = [e for n in nodes for e in n.events.values()]
    for n in nodes:
        n.receive(everything, at)


def split_brain():
    """Plan 10.1: Alice's cart is on both nodes, the link is cut, and both keep writing."""
    mum, pun, rid = Replica("mum"), Replica("pun"), "R-a11ce001"
    pun.receive([mum.write(T0, "CREATE", rid, value=cart("Alice"), op_id="alice-1")], T0 + 1_000)
    # --- link mum-pun cut ---
    mum.write(T0 + 10_000, "SET", rid, "assigned_team", "Bhiwandi", op_id="alice-2")
    pun.write(T0 + 11_000, "SET", rid, "assigned_team", "Chakan", op_id="ops-pun-1")
    mum.write(T0 + 20_000, "SET", rid, "severity", 5, op_id="alice-3")       # Priority delivery
    pun.write(T0 + 21_000, "SET", rid, "severity", 4, op_id="ops-pun-2")     # Same-day delivery
    mum.write(T0 + 30_000, "SET", rid, "status", "closed", op_id="alice-4")  # order placed on Mumbai
    pun.write(T0 + 31_000, "NOTE", rid, value=add("GIFT-500", "Gift card", 500, "Alice"), op_id="alice-5")
    # --- healed ---
    heal(mum, pun, at=T0 + 60_000)
    return mum, pun, rid


def close_vs_new_info():
    """Chitra's order is placed on Mumbai while Pune adds an item. Neither saw the other."""
    mum, pun, rid = Replica("mum"), Replica("pun"), "R-c417a003"
    created = mum.write(T0, "CREATE", rid, value=cart("Chitra"), op_id="chitra-1")
    kurta = mum.write(T0 + 5_000, "NOTE", rid, value=add("KURTA-01", "Cotton kurta", 899, "Chitra"), op_id="chitra-2")
    pun.receive([created, kurta], T0 + 6_000)
    # --- link mum-pun cut ---
    mum.write(T0 + 20_000, "SET", rid, "status", "closed", op_id="chitra-3")
    pun.write(T0 + 21_000, "NOTE", rid, value=add("MAT-01", "Yoga mat", 799, "Chitra"), op_id="chitra-4")
    # --- healed ---
    heal(mum, pun, at=T0 + 60_000)
    return mum, pun, rid


def op_id_retry():
    """Bob adds filter coffee on Mumbai. Mumbai dies before it answers, so his browser retries on Pune."""
    mum, pun, rid = Replica("mum"), Replica("pun"), "R-b0b00002"
    pun.receive([mum.write(T0, "CREATE", rid, value=cart("Bob"), op_id="bob-1")], T0 + 1_000)
    coffee = add("FCOFF-500", "Filter coffee 500 g", 449, "Bob")
    mum.write(T0 + 10_000, "NOTE", rid, value=coffee, op_id="bob-2")  # committed on Mumbai, never answered
    pun.write(T0 + 10_600, "NOTE", rid, value=coffee, op_id="bob-2")  # the retry: same op_id, a second event
    # --- Mumbai restarts and the nodes exchange events ---
    heal(mum, pun, at=T0 + 60_000)
    return mum, pun, rid


ABOUT = {
    "events_split_brain.json": "Plan 10.1. Both nodes wrote Alice's cart while cut off. Delivery speed takes the "
                               "highest (receipt only). Two fulfilment centres is a double dispatch (review). The "
                               "order placed on Mumbai did not see Pune's gift card or its Same-day upgrade "
                               "(CLOSE_VS_NEW_INFO, review).",
    "events_close_vs_new_info.json": "Chitra's order was placed on Mumbai while Pune added a yoga mat. The status "
                                     "stays 'closed' and a supervisor must decide.",
    "events_op_id_retry.json": "Bob's add was retried on Pune with the same op_id after Mumbai died. Two events, "
                               "one item, no conflict.",
}


def build() -> dict:
    """{file name: {"about", "events", "report"}} for every fixture."""
    out = {}
    for name, scenario in (("events_split_brain.json", split_brain),
                           ("events_close_vs_new_info.json", close_vs_new_info),
                           ("events_op_id_retry.json", op_id_retry)):
        mum, pun, rid = scenario()
        assert mum.view(rid) == pun.view(rid), name  # every node shows the same
        out[name] = {"about": ABOUT[name], "events": sorted(mum.events.values(), key=order),
                     "report": {"report_id": rid, **mum.view(rid)}}
    return out


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for name, content in build().items():
        (OUT / name).write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote fixtures/{name}  ({len(content['events'])} events)")
