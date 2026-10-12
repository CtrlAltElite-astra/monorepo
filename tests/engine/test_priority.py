"""Priority lanes and data at risk (owner: Rahil): plan 4.2 and 6.8. 0 critical, 1 normal, 2 bulk."""
import pytest

from region.engine.model import make_event as mk
from region.engine.priority import classify
from region.engine.risk import data_at_risk

P0, P1, P2 = 0, 1, 2


def cart(severity, **more):
    return mk("mum", 1, "CREATE", value={"title": "Alice", "category": "cart", "severity": severity, **more})


def was(severity):
    return {"state": {"severity": severity}}


@pytest.mark.parametrize("ev,before,lane", [
    (mk("mum", 2, "SET", "status", "resolved"), None, P0),         # paid
    (mk("mum", 2, "SET", "status", "closed"), None, P0),           # order placed
    (mk("mum", 2, "SET", "status", "cancelled"), None, P0),
    (mk("mum", 2, "SET", "status", "assigned"), None, P1),         # checking out is not final
    (mk("mum", 2, "SET", "severity", 4), was(3), P0),              # raised to Same-day
    (mk("mum", 2, "SET", "severity", 5), was(4), P0),
    (mk("mum", 2, "SET", "severity", 5), None, P0),                # nothing known before: treat as raised
    (mk("mum", 2, "SET", "severity", 4), was(5), P1),              # lowered
    (mk("mum", 2, "SET", "severity", 4), was(4), P1),              # unchanged
    (mk("mum", 2, "SET", "severity", 3), was(1), P1),              # raised, but below Same-day
    (mk("mum", 2, "SET", "assigned_team", "Bhiwandi"), None, P0),  # fulfilment centre assigned
    (mk("mum", 2, "SET", "assigned_team", None), None, P1),
    (mk("mum", 2, "RESOLVE", "title", "Alice", reason="typo"), None, P0),  # any supervisor decision
    (cart(4), None, P0),
    (cart(5), None, P0),
    (cart(3), None, P1),
    (mk("mum", 2, "NOTE", value={"text": "add"}), None, P2),       # cart activity
    (mk("mum", 2, "SET", "title", "Alice S"), None, P1),
    (mk("mum", 2, "SET", "location", None), None, P1),
])
def test_lane(ev, before, lane):
    assert classify(ev, before) == lane


def test_category_does_not_decide_priority():
    assert classify(cart(1, category="collapse")) == P1  # plan v1.0 made some categories critical; v1.1 does not


def test_bulk_seed_is_always_bulk():
    assert classify(cart(5), bulk=True) == P2


def test_odd_events_get_the_normal_lane_and_never_raise():
    assert classify({}) == P1 and classify({"type": "CREATE", "value": None}) == P1
    assert classify({"type": "SET", "field": "severity", "value": "high"}, {"state": {}}) == P1


# ---- data at risk (the arithmetic behind T22) ------------------------------
OWN = [(seq, 0 if seq in (4, 7) else 1, 1_000 * seq) for seq in range(1, 9)]  # (seq, priority, written_ms)


def test_events_above_every_peer_head_are_at_risk():
    assert data_at_risk(OWN, {"pun": 3, "del": 1}, now_ms=10_000) == {
        "unreplicated": 5, "critical_unreplicated": 2, "oldest_age_ms": 6_000}


def test_one_peer_holding_an_event_makes_it_safe():
    assert data_at_risk(OWN, {"pun": 0, "del": 8}, 10_000) == {
        "unreplicated": 0, "critical_unreplicated": 0, "oldest_age_ms": 0}
    assert data_at_risk(OWN, {}, 10_000)["unreplicated"] == 8  # no peers: everything is at risk


def test_cut_link_write_five_then_heal():
    events = [(1, 1, 0), (2, 1, 0)] + [(seq, 1, 5_000) for seq in range(3, 8)]  # five writes after the cut
    assert data_at_risk(events, {"pun": 2}, 6_000)["unreplicated"] == 5
    assert data_at_risk(events, {"pun": 7}, 9_000)["unreplicated"] == 0
