"""Policy v1 tests (owner: Rahil): one test per rule of the table in docs/conflict-policy.md.

Shop wording: severity = delivery speed; status open / assigned / resolved / closed =
shopping / checking out / paid / order placed; assigned_team = fulfilment centre;
location = delivery pin.
"""
import pytest

from region.engine import policy
from region.engine.derive import POLICY_VERSION, derive, well_formed
from region.engine.model import make_event as mk
from region.engine.policy import POLICY_V1, Policy

NEW_CART = {"title": "Alice", "description": "", "category": "cart", "severity": 1, "status": "open",
            "assigned_team": None, "location": None, "people_estimate": None}
CART = mk("mum", 1, "CREATE", value=NEW_CART, hlc=(1000, 0))


def split(field, mum_value, pun_value, mum_at=2000, pun_at=2001):
    """Both nodes write the same field while cut off: neither saw the other. Pun writes later by default."""
    return [CART,
            mk("mum", 2, "SET", field, mum_value, ["mum:1"], hlc=(mum_at, 0)),
            mk("pun", 1, "SET", field, pun_value, ["mum:1"], hlc=(pun_at, 0))]


def only(view):
    (receipt,) = view["conflicts"]
    return receipt


def pin(dlat=0, dlon=0):
    return {"lat_e6": 19_072_800 + dlat, "lon_e6": 72_882_600 + dlon}


# ---- title, description, category: latest ----------------------------------
@pytest.mark.parametrize("field", ["title", "description", "category"])
def test_low_stakes_fields_take_the_latest_without_review(field):
    v = derive(split(field, "from mum", "from pun"))
    r = only(v)
    assert v["state"][field] == "from pun" and (r["rule"], r["needs_review"], r["flags"]) == ("LATEST", False, [])
    assert derive(split(field, "from mum", "from pun", mum_at=2002))["state"][field] == "from mum"


# ---- severity (delivery speed): highest, receipt only ----------------------
def test_severity_takes_the_highest_without_review():
    v = derive(split("severity", 5, 4))  # the lower value was written later and still loses
    r = only(v)
    assert v["state"]["severity"] == 5 and (r["rule"], r["needs_review"]) == ("SEVERITY_HIGHEST", False)
    assert only(derive(split("severity", 5, 4), Policy(severity_review=True)))["needs_review"] is True  # D2 option b


# ---- status: most active, always reviewed ----------------------------------
@pytest.mark.parametrize("mum,pun,shown", [
    ("closed", "assigned", "assigned"),    # order placed vs still checking out: keep the cart live
    ("open", "resolved", "open"),
    ("resolved", "cancelled", "resolved"),
    ("closed", "cancelled", "cancelled"),  # same rung: the later write (pun)
    ("cancelled", "closed", "closed"),
])
def test_status_takes_the_most_active_and_always_needs_review(mum, pun, shown):
    v = derive(split("status", mum, pun))
    r = only(v)
    assert v["state"]["status"] == shown and (r["rule"], r["needs_review"]) == ("STATUS_MOST_ACTIVE", True)


def test_status_furthest_is_one_setting_away():  # D1 option b
    v = derive(split("status", "closed", "assigned"), Policy(status_rule="furthest"))
    assert v["state"]["status"] == "closed" and only(v)["rule"] == "STATUS_FURTHEST"


# ---- assigned_team (fulfilment centre): earliest + DOUBLE_DISPATCH ---------
def test_two_fulfilment_centres_keep_the_earliest_and_flag_double_dispatch():
    v = derive(split("assigned_team", "Bhiwandi", "Chakan"))
    r = only(v)
    assert v["state"]["assigned_team"] == "Bhiwandi"
    assert (r["rule"], r["needs_review"], r["flags"]) == ("TEAM_EARLIEST", True, ["DOUBLE_DISPATCH"])
    assert derive(split("assigned_team", "Bhiwandi", "Chakan", mum_at=2002))["state"]["assigned_team"] == "Chakan"


def test_assign_against_unassign_needs_review_but_is_not_double_dispatch():
    assigned = mk("mum", 2, "SET", "assigned_team", "Bhiwandi", ["mum:1"], hlc=(1500, 0))
    cleared = mk("mum", 3, "SET", "assigned_team", None, ["mum:2"], hlc=(2000, 0))
    moved = mk("pun", 1, "SET", "assigned_team", "Chakan", ["mum:2"], hlc=(2001, 0))
    r = only(derive([CART, assigned, cleared, moved]))
    assert r["provisional"] is None and r["needs_review"] is True and r["flags"] == []  # only one real centre


# ---- location (delivery pin): latest, review if far ------------------------
def test_delivery_pins_close_together_take_the_latest_without_review():
    v = derive(split("location", pin(), pin(dlat=1798)))  # 199.9 m apart
    r = only(v)
    assert v["state"]["location"] == pin(dlat=1798)
    assert (r["rule"], r["needs_review"], r["flags"]) == ("LOCATION_LATEST", False, [])


def test_delivery_pins_200_m_apart_need_review():
    r = only(derive(split("location", pin(), pin(dlat=1799))))  # 200.04 m
    assert r["needs_review"] is True and r["flags"] == ["FAR_APART"] and r["provisional"] == pin(dlat=1799)
    assert only(derive(split("location", pin(), pin(dlat=1799)), Policy(location_review_m=500)))["flags"] == []  # D3


def test_a_missing_delivery_pin_cannot_be_measured_so_it_needs_review():
    r = only(derive(split("location", pin(), None)))
    assert r["needs_review"] is True and r["flags"] == ["LOCATION_MISSING"] and r["provisional"] is None


def test_distance_is_whole_number_maths_and_east_west_shrinks_with_latitude():
    assert policy._cos_e6(0) == 1_000_000 and abs(policy._cos_e6(60_000_000) - 500_000) < 5
    assert policy._cos_e6(90_000_000) < 50
    assert policy.metres_apart_at_least(pin(), pin(dlat=1800), 200)      # 1800 micro-degrees north: 200.2 m
    assert not policy.metres_apart_at_least(pin(), pin(dlon=1800), 200)  # the same step east at 19 N: 189 m
    date_line = lambda edge: ({"lat_e6": 0, "lon_e6": edge}, {"lat_e6": 0, "lon_e6": -edge})
    assert policy.metres_apart_at_least(*date_line(179_999_000), 200)      # 222 m across the date line
    assert not policy.metres_apart_at_least(*date_line(179_999_500), 200)  # 111 m


# ---- people_estimate: highest, never summed --------------------------------
def test_people_estimate_takes_the_highest_never_the_sum():
    v = derive(split("people_estimate", 25, 20))
    assert v["state"]["people_estimate"] == 25
    assert (only(v)["rule"], only(v)["needs_review"]) == ("PEOPLE_HIGHEST", True)


# ---- notes: union ----------------------------------------------------------
def test_notes_from_both_sides_are_all_kept_and_never_conflict():
    a = mk("mum", 2, "NOTE", value={"text": "kurta"}, hlc=(2000, 0))
    b = mk("pun", 1, "NOTE", value={"text": "gift card"}, hlc=(2001, 0))
    v = derive([b, CART, a])
    assert [n["text"] for n in v["state"]["notes"]] == ["kurta", "gift card"] and v["conflicts"] == []


# ---- RESOLVE vs RESOLVE ----------------------------------------------------
def test_two_supervisors_deciding_the_same_is_not_a_conflict():
    a = mk("mum", 3, "RESOLVE", "status", "closed", ["mum:2", "pun:1"], hlc=(3000, 0), reason="stock confirmed")
    b = mk("pun", 2, "RESOLVE", "status", "closed", ["mum:2", "pun:1"], hlc=(3001, 0), reason="stock confirmed")
    v = derive(split("status", "closed", "assigned") + [a, b])
    assert v["conflicts"] == [] and v["state"]["status"] == "closed" and v["live"]["status"] == ["mum:3", "pun:2"]


def test_two_supervisors_deciding_differently_is_a_conflict_again_and_needs_review():
    a = mk("mum", 3, "RESOLVE", "severity", 2, ["mum:2", "pun:1"], hlc=(3000, 0), reason="x")
    b = mk("pun", 2, "RESOLVE", "severity", 3, ["mum:2", "pun:1"], hlc=(3001, 0), reason="y")
    r = only(derive(split("severity", 5, 4) + [a, b]))
    assert (r["provisional"], r["rule"]) == (3, "SEVERITY_HIGHEST")  # still the field's own rule
    assert r["needs_review"] is True and r["flags"] == ["RESOLVE_VS_RESOLVE"]  # even though severity alone needs none


# ---- the receipt -----------------------------------------------------------
def test_receipt_shows_every_candidate_and_the_rule():
    placed = mk("mum", 2, "SET", "status", "closed", ["mum:1"], hlc=(2000, 0), wall="2026-10-12T14:31:00+05:30")
    checkout = mk("pun", 1, "SET", "status", "assigned", ["mum:1"], hlc=(2001, 0), wall="2026-10-12T14:33:00+05:30")
    v = derive([CART, placed, checkout])
    assert only(v) == {
        "field": "status",
        "relationship": "CONCURRENT",
        "candidates": [
            {"event_id": "mum:2", "value": "closed", "origin": "mum", "actor": "agent-mum-1",
             "wall": "2026-10-12T14:31:00+05:30"},
            {"event_id": "pun:1", "value": "assigned", "origin": "pun", "actor": "agent-pun-1",
             "wall": "2026-10-12T14:33:00+05:30"}],
        "rule": "STATUS_MOST_ACTIVE",
        "policy_version": 1,
        "provisional": "assigned",
        "needs_review": True,
        "flags": [],
        "values": ["closed", "assigned"],  # the two v0 keys stay: the contract only grows
        "event_ids": ["mum:2", "pun:1"],
    }
    assert v["policy_version"] == POLICY_VERSION == POLICY_V1.version == 1
    assert v["state"]["status"] == only(v)["provisional"] and v["live"]["status"] == only(v)["event_ids"]


def test_at_most_one_receipt_per_field_in_field_order():
    events = [CART,
              mk("mum", 2, "SET", "status", "closed", ["mum:1"], hlc=(2000, 0)),
              mk("pun", 1, "SET", "status", "assigned", ["mum:1"], hlc=(2001, 0)),
              mk("mum", 3, "SET", "severity", 5, ["mum:1"], hlc=(2002, 0)),
              mk("pun", 2, "SET", "severity", 4, ["mum:1"], hlc=(2003, 0)),
              mk("mum", 4, "SET", "title", "Alice S", ["mum:1"], hlc=(2004, 0)),
              mk("pun", 3, "SET", "title", "Alice K", ["mum:1"], hlc=(2005, 0))]
    assert [c["field"] for c in derive(events)["conflicts"]] == ["title", "severity", "status"]


# ---- events that are not what the rules expect -----------------------------
GOOD = mk("pun", 9, "SET", "severity", 4, ["mum:1"])
JUNK = [None, 7, "x", [], {}, {"event_id": "pun:9"}, {k: v for k, v in GOOD.items() if k != "value"},
        dict(GOOD, hlc="soon"), dict(GOOD, hlc=[1, 2, 3]), dict(GOOD, hlc=[1.5, 0]), dict(GOOD, seq="9"),
        dict(GOOD, seq=True), dict(GOOD, origin=5), dict(GOOD, supersedes="mum:1"), dict(GOOD, supersedes=[["mum:1"]]),
        dict(GOOD, field=None), dict(GOOD, type="NOTE"), dict(GOOD, type="NOTE", value={"text": 5}),
        dict(GOOD, type="CREATE")]


@pytest.mark.parametrize("junk", JUNK)
def test_malformed_events_are_ignored_never_fatal(junk):
    assert well_formed(GOOD) and not well_formed(junk)
    assert derive([CART, junk]) == derive([CART])


def test_rules_cope_with_values_nobody_validated():
    # a received event is never re-validated, so a rule must not trip over an odd value
    assert derive(split("severity", "urgent", 4))["state"]["severity"] == 4       # only numbers compete for highest
    assert derive(split("severity", "urgent", "soon"))["state"]["severity"] == "soon"
    assert derive(split("status", "teleported", "assigned"))["state"]["status"] == "assigned"  # known rungs first
    assert derive(split("status", {"a": 1}, "y"))["state"]["status"] == "y"
    assert only(derive(split("assigned_team", ["A"], "Chakan")))["flags"] == []   # one real centre only
    assert only(derive(split("location", "nowhere", pin())))["flags"] == ["LOCATION_MISSING"]
