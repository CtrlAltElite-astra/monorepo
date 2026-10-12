"""Shop fixtures (owner: Miti). Integrity tests must pass now; the policy test is the
target for Rahil's policy v1 and is expected to fail until it lands (see xfail reason)."""
import glob
import json
from pathlib import Path

import pytest

from region.engine import canonical
from region.engine.derive import derive

FIX = Path(__file__).resolve().parents[2] / "fixtures"
FILES = sorted(glob.glob(str(FIX / "*.json")))


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", FILES)
def test_hashes_verify_and_sequences_are_gap_free(path):
    events = json.loads(Path(path).read_text(encoding="utf-8"))["events"]
    assert events, path
    seqs = {}
    for e in events:
        assert canonical.event_hash(e) == e["hash"], e["event_id"]
        assert e["event_id"] == f"{e['origin']}:{e['seq']}"
        seqs.setdefault(e["origin"], []).append(e["seq"])
    for origin, s in seqs.items():
        assert s == list(range(1, len(s) + 1)), (origin, s)
    assert len({e["event_id"] for e in events}) == len(events)


def test_carts_fixture_has_five_partly_filled_carts():
    doc = load("carts.json")
    assert [c["owner"] for c in doc["carts"]] == ["Alice", "Bob", "Chitra", "Dev", "Esha"]
    assert all(c["skus"] for c in doc["carts"])


def test_derive_is_order_independent_on_split_brain():
    events = load("events_split_brain.json")["events"]
    a = canonical.dumps(derive(events))
    b = canonical.dumps(derive(list(reversed(events)) + events[:3]))
    assert a == b


def test_split_brain_matches_plan_section_10_1():
    doc = load("events_split_brain.json")
    view = derive(doc["events"])
    for field, want in doc["expected"]["state"].items():
        assert view["state"][field] == want, field
    flagged = {c["field"] for c in view["conflicts"] if c["needs_review"]}
    assert "assigned_team" in flagged
    rules = {c["rule"] for c in view["conflicts"]}
    assert "CLOSE_VS_NEW_INFO" in rules
    assert [n["id"] for n in view["state"]["notes"]] == doc["expected"]["notes_event_ids"]


def test_close_vs_new_info_fixture_and_its_control():
    hit = derive(load("events_close_vs_new_info.json")["events"])
    ctl = derive(load("events_close_vs_new_info_control.json")["events"])
    assert [c["rule"] for c in hit["conflicts"]] == ["CLOSE_VS_NEW_INFO"] and hit["state"]["status"] == "closed"
    assert ctl["conflicts"] == []


def test_op_id_retry_fixture_counts_each_operation_once():
    doc = load("events_op_id_retry.json")
    view = derive(doc["events"])
    assert view["conflicts"] == [] and view["state"]["status"] == "assigned"
    assert len(view["state"]["notes"]) == 1
