"""T16, engine half (owner: Rahil): a write retried under the same op_id counts once.

A browser that gets no answer from one node retries on the next with the same
op_id. If the first node had already applied the write, the same write now
exists as two events. derive() must show one item and no conflict (plan 6.5).
"""
import itertools
import json

from region.engine import canonical
from region.engine.derive import derive
from region.engine.model import make_event as mk

NEW_CART = {"title": "Alice", "description": "", "category": "cart", "severity": 1, "status": "open",
            "assigned_team": None, "location": None, "people_estimate": None}
CART = mk("mum", 1, "CREATE", value=NEW_CART, hlc=(1000, 0))
GIFT = json.dumps({"kind": "ADD", "sku": "GIFT-500", "name": "Gift card", "qty": 1, "price": 500, "user": "Alice"})

# Alice adds a gift card on mum; mum dies before answering; her browser retries on pun.
ADD = mk("mum", 2, "NOTE", value={"text": GIFT}, op_id="op-add-1", hlc=(2000, 0))
ADD_RETRY = mk("pun", 1, "NOTE", value={"text": GIFT}, op_id="op-add-1", hlc=(2600, 0))
# The same for "check out".
CHECKOUT = mk("mum", 3, "SET", "status", "assigned", ["mum:1"], op_id="op-co-1", hlc=(3000, 0))
CHECKOUT_RETRY = mk("pun", 2, "SET", "status", "assigned", ["mum:1"], op_id="op-co-1", hlc=(3600, 0))


def test_add_retried_on_another_node_is_one_item():
    assert derive([CART, ADD, ADD_RETRY])["state"]["notes"] == [{"id": "mum:2", "text": GIFT}]
    # a node that only has the retry so far also shows exactly one
    assert derive([CART, ADD_RETRY])["state"]["notes"] == [{"id": "pun:1", "text": GIFT}]


def test_separate_adds_of_the_same_product_stay_separate():
    second_click = mk("pun", 1, "NOTE", value={"text": GIFT}, op_id="op-add-2", hlc=(2600, 0))
    assert len(derive([CART, ADD, second_click])["state"]["notes"]) == 2
    no_op_a = mk("mum", 2, "NOTE", value={"text": GIFT}, hlc=(2000, 0))
    no_op_b = mk("pun", 1, "NOTE", value={"text": GIFT}, hlc=(2600, 0))
    assert len(derive([CART, no_op_a, no_op_b])["state"]["notes"]) == 2  # no op_id: never merged


def test_retried_field_write_is_one_live_value():
    v = derive([CART, CHECKOUT, CHECKOUT_RETRY])
    assert v["live"]["status"] == ["mum:3"] and v["state"]["status"] == "assigned" and v["conflicts"] == []


def test_replacing_one_copy_replaces_the_other_too():
    # mum pays having seen only its own copy; pun's copy must not come back as a rival value
    paid = mk("mum", 4, "SET", "status", "resolved", ["mum:3"], hlc=(4000, 0))
    v = derive([CART, CHECKOUT, CHECKOUT_RETRY, paid])
    assert v["live"]["status"] == ["mum:4"] and v["state"]["status"] == "resolved" and v["conflicts"] == []
    paid_on_pun = mk("pun", 3, "SET", "status", "resolved", ["pun:2"], hlc=(4000, 0))
    v = derive([CART, CHECKOUT, CHECKOUT_RETRY, paid_on_pun])
    assert v["live"]["status"] == ["pun:3"] and v["conflicts"] == []


def test_retried_create_is_one_cart():
    first = mk("mum", 1, "CREATE", value=NEW_CART, op_id="op-new-1", hlc=(1000, 0), report_id="R-3f9a2c10")
    retry = mk("pun", 1, "CREATE", value=NEW_CART, op_id="op-new-1", hlc=(1600, 0), report_id="R-3f9a2c10")
    v = derive([retry, first])
    assert v["conflicts"] == [] and v["incomplete"] is False
    assert all(ids == ["mum:1"] for ids in v["live"].values())


def test_arrival_order_and_duplicates_do_not_matter_with_retries():
    paid = mk("mum", 4, "SET", "status", "resolved", ["mum:3"], hlc=(4000, 0))
    events = [CART, ADD, ADD_RETRY, CHECKOUT, CHECKOUT_RETRY, paid]
    want = canonical.dumps(derive(events))
    for perm in itertools.permutations(events):
        assert canonical.dumps(derive(perm)) == want
    assert canonical.dumps(derive(events + events[::-1])) == want
