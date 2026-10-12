"""Generate fixtures/*.json for the shop demo (owner: Miti, shared with Rahil).

    python scripts/gen_shop_fixtures.py

Events follow the v1.1 envelope (plan section 5.1) and are hashed with the real
engine (region.engine.canonical). `sig` stays null until engine/signing.py lands;
regenerate then. `expected` blocks are copied from the plan (section 10.1), NOT
computed from derive(), so they are an independent check of the engine.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from region.engine import canonical  # noqa: E402

OUT = ROOT / "fixtures"
BASE_MS = 1760277000000  # 12 Oct 2026, ~14:00 IST; only relative order matters

PRODUCTS = {
    "SANDAL-01": ("Kolhapuri sandals", 1299), "LAMP-01": ("Desk lamp", 1099),
    "BOTTLE-01": ("Steel water bottle", 499), "BAG-01": ("Backpack", 1599),
    "KURTA-01": ("Cotton kurta", 899), "GIFT-500": ("Gift card", 500),
    "FCOFF-500": ("Filter coffee 500 g", 449), "CHAI-01": ("Masala chai box", 349),
    "EARBUD-01": ("Wireless earbuds", 1999), "MAT-01": ("Yoga mat", 799),
}
STATUS_LABEL = {"open": "Shopping", "assigned": "Checking out", "resolved": "Paid",
                "closed": "Order placed", "cancelled": "Cancelled"}
SPEED_LABEL = {1: "Standard", 2: "Plus", 3: "Express", 4: "Same-day", 5: "Priority"}


class Node:
    """Per-origin gap-free sequence counter."""
    def __init__(self):
        self.seq = {}

    def next(self, origin):
        self.seq[origin] = self.seq.get(origin, 0) + 1
        return self.seq[origin]


def ev(nodes, origin, report_id, type_, field=None, value=None, supersedes=(), dt=0,
       op=None, observed=None, actor=None, priority=None):
    seq = nodes.next(origin)
    if priority is None:
        priority = 2 if type_ == "NOTE" else 1
    e = {
        "v": 1, "event_id": f"{origin}:{seq}", "origin": origin, "seq": seq,
        "report_id": report_id, "type": type_, "field": field, "value": value,
        "supersedes": list(supersedes), "hlc": [BASE_MS + dt, 0], "priority": priority,
        "op_id": op, "actor": actor or f"agent-{origin}-1", "role": "officer",
        "auth_mode": "online", "reason": None, "wall": "2026-10-12T14:00:00+05:30",
    }
    if observed is not None:
        e["observed"] = list(observed)
    e["hash"] = canonical.event_hash(e)
    e["sig"] = None
    return e


def create(nodes, origin, rid, owner, dt, op):
    return ev(nodes, origin, rid, "CREATE", value={
        "title": owner, "description": "", "category": "cart", "severity": 1, "status": "open",
        "assigned_team": None, "location": None, "people_estimate": None}, dt=dt, op=op,
        priority=1)


def add(nodes, origin, rid, owner, sku, dt, op, qty=1):
    name, price = PRODUCTS[sku]
    text = json.dumps({"kind": "ADD", "sku": sku, "name": name, "qty": qty, "price": price, "user": owner},
                      separators=(",", ":"))
    return ev(nodes, origin, rid, "NOTE", value={"text": text}, dt=dt, op=op)


def write(name, doc):
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote fixtures/" + name, f"({len(doc.get('events', []))} events)")


# ---- carts.json: five shoppers, partly filled so no screen starts empty ----
def carts():
    n = Node()
    spec = [("Alice", "mum", "R-a11ce001", ["FCOFF-500", "BOTTLE-01"]),
            ("Bob", "mum", "R-b0b00002", ["EARBUD-01"]),
            ("Chitra", "pun", "R-c4177003", ["KURTA-01", "CHAI-01"]),
            ("Dev", "pun", "R-de400004", ["LAMP-01"]),
            ("Esha", "del", "R-e5ba0005", ["MAT-01", "GIFT-500", "SANDAL-01"])]
    events, summary, t = [], [], 0
    for owner, origin, rid, skus in spec:
        events.append(create(n, origin, rid, owner, t, f"op-create-{owner.lower()}"))
        t += 1000
        for i, sku in enumerate(skus):
            events.append(add(n, origin, rid, owner, sku, t, f"op-{owner.lower()}-{i + 1}"))
            t += 1000
        summary.append({"report_id": rid, "owner": owner, "home_node": origin, "skus": skus,
                        "status": STATUS_LABEL["open"], "delivery_speed": SPEED_LABEL[1]})
    write("carts.json", {"description": "Five shoppers, one cart each, partly filled (Appendix B).",
                         "products": {k: {"name": v[0], "price": v[1]} for k, v in PRODUCTS.items()},
                         "carts": summary, "events": events})


# ---- events_split_brain.json: plan section 10.1 (T4) ----
def split_brain():
    n = Node()
    rid = "R-a11ce001"
    base = [create(n, "mum", rid, "Alice", 0, "op-create-alice"),
            add(n, "mum", rid, "Alice", "FCOFF-500", 1000, "op-alice-1")]
    c = base[0]["event_id"]
    mum = [
        ev(n, "mum", rid, "SET", "severity", 5, [c], 10_000, "op-m-speed"),
        ev(n, "mum", rid, "SET", "assigned_team", "Bhiwandi", [c], 11_000, "op-m-centre"),
        ev(n, "mum", rid, "SET", "status", "closed", [c], 12_000, "op-m-order",
           observed=[c, base[1]["event_id"], "mum:3"], priority=0),
    ]
    pun = [
        ev(n, "pun", rid, "SET", "severity", 4, [c], 10_500, "op-p-speed", priority=0),
        ev(n, "pun", rid, "SET", "assigned_team", "Chakan", [c], 13_000, "op-p-centre", priority=0),
        add(n, "pun", rid, "Alice", "GIFT-500", 14_000, "op-p-gift"),
    ]
    pun[2]["observed"] = [c]  # live status ids pun had: the CREATE only
    pun[2]["hash"] = canonical.event_hash(pun[2])
    write("events_split_brain.json", {
        "description": "Plan 10.1. mum:1-2 are replicated before the link is cut; mum:3-5 and pun:1-3 are written "
                       "during the partition. After heal both nodes must derive the `expected` result.",
        "partition_starts_after": "mum:2",
        "events": base + mum + pun,
        "expected": {
            "state": {"severity": 5, "assigned_team": "Bhiwandi", "status": "closed"},
            "status_label": STATUS_LABEL["closed"],
            "conflicts": {
                "severity": {"rule": "highest", "needs_review": False},
                "assigned_team": {"rule": "earliest_hlc", "needs_review": True, "flag": "DOUBLE_DISPATCH"},
            },
            "notes_event_ids": ["mum:2", "pun:3"],
            "review_items": ["DOUBLE_DISPATCH on assigned_team", "CLOSE_VS_NEW_INFO on status"],
        },
    })


# ---- events_close_vs_new_info.json ----
def close_vs_new():
    n = Node()
    rid = "R-b0b00002"
    base = [create(n, "mum", rid, "Bob", 0, "op-create-bob"),
            add(n, "mum", rid, "Bob", "EARBUD-01", 1000, "op-bob-1")]
    c = base[0]["event_id"]
    close = ev(n, "mum", rid, "SET", "status", "closed", [c], 5000, "op-bob-order",
               observed=[c, base[1]["event_id"]], priority=0)
    item = add(n, "pun", rid, "Bob", "CHAI-01", 5200, "op-bob-2")
    item["observed"] = [c]
    item["hash"] = canonical.event_hash(item)
    write("events_close_vs_new_info.json", {
        "description": "Order placed on mum while pun adds an item; neither observed the other.",
        "events": base + [close, item],
        "expected": {
            "state": {"status": "closed"},
            "status_label": STATUS_LABEL["closed"],
            "review_items": ["CLOSE_VS_NEW_INFO"],
            "notes_event_ids": ["mum:2", "pun:1"],
        },
    })
    # control: the same order, but the new item was added BEFORE the order and observed by it
    n2 = Node()
    base2 = [create(n2, "mum", rid, "Bob", 0, "op-create-bob"),
             add(n2, "mum", rid, "Bob", "EARBUD-01", 1000, "op-bob-1"),
             add(n2, "pun", rid, "Bob", "CHAI-01", 1500, "op-bob-2")]
    close2 = ev(n2, "mum", rid, "SET", "status", "closed", [base2[0]["event_id"]], 5000, "op-bob-order",
                observed=[base2[0]["event_id"], base2[1]["event_id"], base2[2]["event_id"]], priority=0)
    write("events_close_vs_new_info_control.json", {
        "description": "Control: the order observed every item, so there must be NO review item.",
        "events": base2 + [close2],
        "expected": {"state": {"status": "closed"}, "review_items": []},
    })


# ---- events_op_id_retry.json ----
def op_id_retry():
    n = Node()
    rid = "R-c4177003"
    base = [create(n, "mum", rid, "Chitra", 0, "op-create-chitra")]
    c = base[0]["event_id"]
    first = add(n, "mum", rid, "Chitra", "KURTA-01", 1000, "op-retry-add")       # mum:2, then mum dies
    retry = add(n, "pun", rid, "Chitra", "KURTA-01", 1400, "op-retry-add")       # pun:1, SAME op_id
    st1 = ev(n, "mum", rid, "SET", "status", "assigned", [c], 2000, "op-retry-checkout")   # mum:3
    st2 = ev(n, "pun", rid, "SET", "status", "assigned", [c], 2400, "op-retry-checkout")   # pun:2, SAME op_id
    write("events_op_id_retry.json", {
        "description": "A retried operation (same op_id) produced two events on two nodes. derive() must "
                       "count each operation once: quantity 1, and no phantom conflict.",
        "events": base + [first, retry, st1, st2],
        "expected": {
            "sku_quantities": {"KURTA-01": 1},
            "state": {"status": "assigned"},
            "conflicts": [],
            "op_groups": {"op-retry-add": ["mum:2", "pun:1"], "op-retry-checkout": ["mum:3", "pun:2"]},
        },
    })


if __name__ == "__main__":
    carts()
    split_brain()
    close_vs_new()
    op_id_retry()
