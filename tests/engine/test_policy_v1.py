"""Policy v1 (plan v1.1 section 6.6), op_id groups and CLOSE_VS_NEW_INFO. One test per rule."""
import itertools
import random

from region.engine import canonical
from region.engine.derive import POLICY_VERSION, derive


def mk(origin, seq, type_, field=None, value=None, supersedes=(), t=0, op=None, observed=None, report="R-1"):
    ev = dict(v=1, event_id=f"{origin}:{seq}", origin=origin, seq=seq, report_id=report, type=type_,
              field=field, value=value, supersedes=list(supersedes), hlc=[1000 + t, 0], priority=1,
              actor=f"a-{origin}", reason=None, wall="x", sig=None, op_id=op, observed=observed)
    ev["hash"] = canonical.event_hash(ev)
    return ev


CREATE = mk("mum", 1, "CREATE", value={"title": "Cart", "category": "cart", "severity": 1, "status": "open",
                                       "assigned_team": None, "location": None, "description": "",
                                       "people_estimate": None}, t=0)


def conflict(view, field, rule=None):
    c = [x for x in view["conflicts"] if x["field"] == field and (rule is None or x["rule"] == rule)]
    return c[0] if c else None


def test_policy_version_is_1():
    assert POLICY_VERSION == 1 and derive([CREATE])["policy_version"] == 1


# ---- per-field rules ------------------------------------------------------
def test_text_fields_latest_by_hlc_no_review():
    a = mk("mum", 2, "SET", "title", "Alice", ["mum:1"], t=10)
    b = mk("pun", 1, "SET", "title", "Alice B", ["mum:1"], t=20)
    v = derive([CREATE, a, b])
    c = conflict(v, "title")
    assert v["state"]["title"] == "Alice B" and c["rule"] == "LATEST_HLC" and c["needs_review"] is False


def test_severity_highest_wins_even_if_older_receipt_only():
    a = mk("mum", 2, "SET", "severity", 5, ["mum:1"], t=10)
    b = mk("pun", 1, "SET", "severity", 4, ["mum:1"], t=20)
    v = derive([CREATE, a, b])
    c = conflict(v, "severity")
    assert v["state"]["severity"] == 5 and c["rule"] == "HIGHEST" and c["needs_review"] is False
    assert c["provisional"] == 5 and [x["value"] for x in c["candidates"]] == [5, 4]


def test_a_write_that_saw_both_values_can_lower_severity():
    a = mk("mum", 2, "SET", "severity", 5, ["mum:1"], t=10)
    b = mk("pun", 1, "SET", "severity", 4, ["mum:1"], t=20)
    lower = mk("mum", 3, "SET", "severity", 2, ["mum:2", "pun:1"], t=30)
    v = derive([CREATE, a, b, lower])
    assert v["state"]["severity"] == 2 and v["conflicts"] == []


def test_status_most_active_wins_and_needs_review():
    closed = mk("mum", 2, "SET", "status", "closed", ["mum:1"], t=10)
    assigned = mk("pun", 1, "SET", "status", "assigned", ["mum:1"], t=20)
    v = derive([CREATE, closed, assigned])
    c = conflict(v, "status", "STATUS_MOST_ACTIVE")
    assert v["state"]["status"] == "assigned" and c["needs_review"] is True


def test_status_tie_on_the_ladder_picks_latest():
    closed = mk("mum", 2, "SET", "status", "closed", ["mum:1"], t=10)
    cancelled = mk("pun", 1, "SET", "status", "cancelled", ["mum:1"], t=20)
    assert derive([CREATE, closed, cancelled])["state"]["status"] == "cancelled"


def test_team_earliest_wins_and_double_dispatch_flag():
    a = mk("mum", 2, "SET", "assigned_team", "Bhiwandi", ["mum:1"], t=10)
    b = mk("pun", 1, "SET", "assigned_team", "Chakan", ["mum:1"], t=5)
    v = derive([CREATE, a, b])
    c = conflict(v, "assigned_team")
    assert v["state"]["assigned_team"] == "Chakan"          # pun's write is earlier by HLC
    assert c["flags"] == ["DOUBLE_DISPATCH"] and c["needs_review"] is True


def test_same_team_twice_is_not_a_conflict():
    a = mk("mum", 2, "SET", "assigned_team", "Alpha", ["mum:1"], t=10)
    b = mk("pun", 1, "SET", "assigned_team", "Alpha", ["mum:1"], t=20)
    assert derive([CREATE, a, b])["conflicts"] == []


def test_location_far_apart_needs_review_near_does_not():
    here = {"lat_e6": 19_076_000, "lon_e6": 72_877_000}
    near = {"lat_e6": 19_076_500, "lon_e6": 72_877_000}      # about 55 m
    far = {"lat_e6": 19_100_000, "lon_e6": 72_877_000}       # about 2.6 km
    base = mk("mum", 2, "SET", "location", here, ["mum:1"], t=10)
    v_near = derive([CREATE, base, mk("pun", 1, "SET", "location", near, ["mum:1"], t=20)])
    v_far = derive([CREATE, base, mk("pun", 1, "SET", "location", far, ["mum:1"], t=20)])
    assert conflict(v_near, "location")["needs_review"] is False
    assert conflict(v_far, "location")["needs_review"] is True and v_far["state"]["location"] == far


def test_people_estimate_highest_never_summed():
    a = mk("mum", 2, "SET", "people_estimate", 20, ["mum:1"], t=10)
    b = mk("pun", 1, "SET", "people_estimate", 25, ["mum:1"], t=5)
    v = derive([CREATE, a, b])
    assert v["state"]["people_estimate"] == 25 and conflict(v, "people_estimate")["needs_review"] is True


def test_disjoint_fields_never_conflict():
    a = mk("mum", 2, "SET", "status", "assigned", ["mum:1"], t=10)
    b = mk("pun", 1, "SET", "severity", 4, ["mum:1"], t=10)
    assert derive([CREATE, a, b])["conflicts"] == []


# ---- op_id groups ----------------------------------------------------------
def test_retried_write_on_two_nodes_is_one_live_value_and_no_conflict():
    first = mk("mum", 2, "SET", "status", "assigned", ["mum:1"], t=10, op="op-x")
    retry = mk("pun", 1, "SET", "status", "assigned", ["mum:1"], t=11, op="op-x")
    v = derive([CREATE, first, retry])
    assert v["conflicts"] == [] and v["live"]["status"] == ["mum:2"]


def test_superseding_one_member_supersedes_the_whole_group():
    first = mk("mum", 2, "SET", "severity", 3, ["mum:1"], t=10, op="op-y")
    retry = mk("pun", 1, "SET", "severity", 3, ["mum:1"], t=11, op="op-y")
    later = mk("mum", 3, "SET", "severity", 2, ["mum:2"], t=20)  # saw only the first member
    v = derive([CREATE, first, retry, later])
    assert v["live"]["severity"] == ["mum:3"] and v["state"]["severity"] == 2 and v["conflicts"] == []


def test_retried_note_is_shown_once():
    n1 = mk("mum", 2, "NOTE", value={"text": "add coffee"}, t=10, op="op-n")
    n2 = mk("pun", 1, "NOTE", value={"text": "add coffee"}, t=11, op="op-n")
    other = mk("pun", 2, "NOTE", value={"text": "add tea"}, t=12, op="op-m")
    assert [n["id"] for n in derive([CREATE, n1, n2, other])["state"]["notes"]] == ["mum:2", "pun:2"]


# ---- CLOSE_VS_NEW_INFO ------------------------------------------------------
def test_close_and_unobserved_note_raise_a_review_item():
    close = mk("mum", 2, "SET", "status", "closed", ["mum:1"], t=10, observed=["mum:1"])
    note = mk("pun", 1, "NOTE", value={"text": "two trapped"}, t=12, observed=["mum:1"])
    v = derive([CREATE, close, note])
    c = conflict(v, "status", "CLOSE_VS_NEW_INFO")
    assert v["state"]["status"] == "closed"            # status stays as derived
    assert c["needs_review"] is True and c["flags"] == ["CLOSE_VS_NEW_INFO"]


def test_close_that_observed_the_note_is_clean():
    note = mk("pun", 1, "NOTE", value={"text": "two trapped"}, t=5, observed=["mum:1"])
    close = mk("mum", 2, "SET", "status", "closed", ["mum:1"], t=10, observed=["mum:1", "pun:1"])
    assert derive([CREATE, note, close])["conflicts"] == []


def test_note_written_after_seeing_the_close_is_clean():
    close = mk("mum", 2, "SET", "status", "closed", ["mum:1"], t=10, observed=["mum:1"])
    note = mk("pun", 1, "NOTE", value={"text": "late"}, t=20, observed=["mum:2"])
    assert derive([CREATE, close, note])["conflicts"] == []


def test_big_severity_upgrade_concurrent_with_close_raises_review():
    close = mk("mum", 2, "SET", "status", "closed", ["mum:1"], t=10, observed=["mum:1"])
    up = mk("pun", 1, "SET", "severity", 5, ["mum:1"], t=12, observed=["mum:1"])
    assert conflict(derive([CREATE, close, up]), "status", "CLOSE_VS_NEW_INFO") is not None


def test_one_close_with_several_unseen_changes_is_one_review_item():
    close = mk("mum", 2, "SET", "status", "closed", ["mum:1"], t=10, observed=["mum:1"])
    note = mk("pun", 1, "NOTE", value={"text": "gift card"}, t=12, observed=["mum:1"])
    up = mk("pun", 2, "SET", "severity", 4, ["mum:1"], t=13, observed=["mum:1"])
    v = derive([CREATE, close, note, up])
    items = [c for c in v["conflicts"] if c["rule"] == "CLOSE_VS_NEW_INFO"]
    assert len(items) == 1 and items[0]["event_ids"] == ["mum:2", "pun:1", "pun:2"]


def test_events_without_observed_never_trigger_it():
    close = mk("mum", 2, "SET", "status", "closed", ["mum:1"], t=10)          # older node, no observed
    note = mk("pun", 1, "NOTE", value={"text": "x"}, t=12)
    assert derive([CREATE, close, note])["conflicts"] == []


# ---- convergence -------------------------------------------------------------
def test_v1_is_order_and_duplicate_independent():
    evs = [
        CREATE,
        mk("mum", 2, "SET", "severity", 5, ["mum:1"], t=10),
        mk("pun", 1, "SET", "severity", 4, ["mum:1"], t=11),
        mk("mum", 3, "SET", "assigned_team", "Bhiwandi", ["mum:1"], t=12),
        mk("pun", 2, "SET", "assigned_team", "Chakan", ["mum:1"], t=13),
        mk("mum", 4, "SET", "status", "closed", ["mum:1"], t=14, observed=["mum:1", "mum:2"]),
        mk("pun", 3, "NOTE", value={"text": "gift"}, t=15, observed=["mum:1"]),
        mk("pun", 4, "NOTE", value={"text": "gift"}, t=16, op="op-g"),
        mk("del", 1, "NOTE", value={"text": "gift"}, t=17, op="op-g"),
    ]
    want = canonical.dumps(derive(evs))
    for perm in itertools.islice(itertools.permutations(evs), 150):
        assert canonical.dumps(derive(list(perm))) == want
    rnd = random.Random(11)
    for _ in range(30):
        mix = evs + rnd.choices(evs, k=8)
        rnd.shuffle(mix)
        assert canonical.dumps(derive(mix)) == want
