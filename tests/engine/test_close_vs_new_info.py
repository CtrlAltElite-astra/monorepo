"""CLOSE_VS_NEW_INFO (owner: Rahil): an order placed without seeing a new item goes to a supervisor.

A close (status closed or cancelled) lists in `observed` the cart activity and delivery
speed its node had. A cart-activity note or a delivery-speed write lists the status its
node had. If neither lists the other, they did not see each other (plan 6.7).
"""
from region.engine.derive import derive
from region.engine.model import make_event as mk
from region.engine.policy import observed_for

NEW_CART = {"title": "Chitra", "description": "", "category": "cart", "severity": 1, "status": "open",
            "assigned_team": None, "location": None, "people_estimate": None}
CART = mk("mum", 1, "CREATE", value=NEW_CART, hlc=(1000, 0))
# Mumbai places the order having seen no cart activity; Pune adds a gift card having seen status "open".
PLACED = mk("mum", 2, "SET", "status", "closed", ["mum:1"], hlc=(2000, 0), observed=["mum:1"])
GIFT = mk("pun", 1, "NOTE", value={"text": "gift card"}, hlc=(2001, 0), observed=["mum:1"])


def receipts(events):
    return derive(events)["conflicts"]


def test_order_placed_without_seeing_a_new_item_goes_to_review():
    v = derive([CART, PLACED, GIFT])
    assert v["state"]["status"] == "closed"  # the status itself does not change
    assert v["conflicts"] == [{
        "field": "status",
        "relationship": "CONCURRENT",
        "candidates": [{"event_id": "mum:2", "value": "closed", "origin": "mum", "actor": "agent-mum-1", "wall": ""}],
        "rule": "CLOSE_VS_NEW_INFO",
        "policy_version": 1,
        "provisional": "closed",
        "needs_review": True,
        "flags": ["CLOSE_VS_NEW_INFO"],
        "values": ["closed"],
        "event_ids": ["mum:2"],
        "new_info": [{"event_id": "pun:1", "kind": "NOTE", "value": "gift card", "origin": "pun",
                      "actor": "agent-pun-1", "wall": ""}],
    }]


def test_no_review_when_either_side_saw_the_other():
    knew_it_was_placed = mk("pun", 1, "NOTE", value={"text": "gift card"}, hlc=(2001, 0), observed=["mum:2"])
    assert receipts([CART, PLACED, knew_it_was_placed]) == []
    placed_after_the_gift = mk("mum", 2, "SET", "status", "closed", ["mum:1"], hlc=(2002, 0), observed=["pun:1", "mum:1"])
    assert receipts([CART, GIFT, placed_after_the_gift]) == []


def test_a_same_day_upgrade_counts_as_new_information_but_a_smaller_one_does_not():
    upgrade = mk("pun", 1, "SET", "severity", 4, ["mum:1"], hlc=(2001, 0), observed=["mum:1"])
    (r,) = receipts([CART, PLACED, upgrade])
    assert r["rule"] == "CLOSE_VS_NEW_INFO" and [(n["kind"], n["value"]) for n in r["new_info"]] == [("SEVERITY", 4)]
    express = mk("pun", 1, "SET", "severity", 3, ["mum:1"], hlc=(2001, 0), observed=["mum:1"])
    assert receipts([CART, PLACED, express]) == []
    withdrawn = mk("pun", 2, "SET", "severity", 2, ["pun:1"], hlc=(2002, 0), observed=["mum:1"])
    assert receipts([CART, PLACED, upgrade, withdrawn]) == []  # only a delivery speed that still stands counts


def test_cancel_is_a_close_and_paid_is_not():
    cancelled = mk("mum", 2, "SET", "status", "cancelled", ["mum:1"], hlc=(2000, 0), observed=["mum:1"])
    assert [r["rule"] for r in receipts([CART, cancelled, GIFT])] == ["CLOSE_VS_NEW_INFO"]
    paid = mk("mum", 2, "SET", "status", "resolved", ["mum:1"], hlc=(2000, 0), observed=["mum:1"])
    assert receipts([CART, paid, GIFT]) == []  # even if the writer recorded a list, paid is not a close


def test_a_supervisor_who_saw_the_item_settles_it_either_way():
    keep = mk("mum", 3, "RESOLVE", "status", "closed", ["mum:2"], hlc=(3000, 0), observed=["pun:1", "mum:1"],
              reason="ship with the gift card")
    v = derive([CART, PLACED, GIFT, keep])
    assert v["conflicts"] == [] and v["state"]["status"] == "closed"
    reopen = mk("mum", 3, "RESOLVE", "status", "assigned", ["mum:2"], hlc=(3000, 0), reason="refund and re-order")
    v = derive([CART, PLACED, GIFT, reopen])
    assert v["conflicts"] == [] and v["state"]["status"] == "assigned"


def test_a_later_item_nobody_saw_reopens_the_review():
    keep = mk("mum", 3, "RESOLVE", "status", "closed", ["mum:2"], hlc=(3000, 0), observed=["pun:1", "mum:1"],
              reason="ship with the gift card")
    mat = mk("pun", 2, "NOTE", value={"text": "yoga mat"}, hlc=(3001, 0), observed=["mum:1"])  # Pune still cut off
    (r,) = receipts([CART, PLACED, GIFT, keep, mat])
    assert [n["event_id"] for n in r["new_info"]] == ["pun:2"]  # the gift card was settled, the mat was not


def test_status_conflict_and_new_item_share_one_receipt():
    checkout = mk("pun", 1, "SET", "status", "assigned", ["mum:1"], hlc=(2001, 0))
    gift = mk("pun", 2, "NOTE", value={"text": "gift card"}, hlc=(2002, 0), observed=["pun:1"])
    (r,) = receipts([CART, PLACED, checkout, gift])
    assert (r["field"], r["rule"], r["provisional"]) == ("status", "STATUS_MOST_ACTIVE", "assigned")
    assert r["flags"] == ["CLOSE_VS_NEW_INFO"] and [n["event_id"] for n in r["new_info"]] == ["pun:2"]
    assert r["needs_review"] is True


def test_a_close_that_recorded_nothing_raises_no_alarm():
    placed_by_an_older_writer = mk("mum", 2, "SET", "status", "closed", ["mum:1"], hlc=(2000, 0))  # no observed key
    assert receipts([CART, placed_by_an_older_writer, GIFT]) == []


def test_a_retried_order_saw_the_item_only_if_every_copy_did():
    first = mk("mum", 2, "SET", "status", "closed", ["mum:1"], hlc=(2000, 0), observed=["mum:1"], op_id="place")
    retry = mk("pun", 2, "SET", "status", "closed", ["mum:1"], hlc=(2600, 0), observed=["pun:1", "mum:1"], op_id="place")
    (r,) = receipts([CART, GIFT, first, retry])  # the shopper clicked on Mumbai, where the gift card was not shown
    assert r["rule"] == "CLOSE_VS_NEW_INFO" and r["event_ids"] == ["mum:2"]
    both_saw = mk("mum", 2, "SET", "status", "closed", ["mum:1"], hlc=(2000, 0), observed=["pun:1", "mum:1"], op_id="place")
    assert receipts([CART, GIFT, both_saw, retry]) == []


def test_a_retried_add_is_one_piece_of_new_information():
    add = mk("pun", 1, "NOTE", value={"text": "gift card"}, hlc=(2001, 0), observed=["mum:1"], op_id="add")
    again = mk("del", 1, "NOTE", value={"text": "gift card"}, hlc=(2600, 0), observed=["mum:1"], op_id="add")
    (r,) = receipts([CART, PLACED, again, add])
    assert [n["event_id"] for n in r["new_info"]] == ["pun:1"]


def test_what_each_kind_of_write_records():
    view = derive([CART, GIFT, mk("pun", 2, "SET", "severity", 3, ["mum:1"], hlc=(2002, 0))])
    assert observed_for("SET", "status", "closed", view) == ["pun:1", "pun:2"]      # cart activity + delivery speed
    assert observed_for("RESOLVE", "status", "cancelled", view) == ["pun:1", "pun:2"]
    assert observed_for("NOTE", None, {"text": "x"}, view) == ["mum:1"]            # the status it saw
    assert observed_for("SET", "severity", 4, view) == ["mum:1"]
    assert observed_for("SET", "status", "assigned", view) is None                 # not a close
    assert observed_for("SET", "status", "resolved", view) is None                 # paid is not a close either
    assert observed_for("SET", "title", "Chitra K", view) is None and observed_for("CREATE", None, NEW_CART, None) is None
