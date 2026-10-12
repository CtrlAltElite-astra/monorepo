"""The fixtures agree with the engine (owner: Rahil).

Lanes C and D build against contracts/fixtures/* and fixtures/events_*.json. If
the engine and a fixture drift apart this fails here, not on a screen. Regenerate
with python scripts/gen_fixtures.py (tests/shop checks fixtures/events_*.json).
"""
import json
from pathlib import Path

from region.engine import canonical
from region.engine.derive import derive

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_fixture_hashes_still_verify():
    # written by an earlier run; a mismatch means hashing changed and peers would quarantine
    for ev in load("split_brain_events.json"):
        assert canonical.event_hash(ev) == ev["hash"], ev["event_id"]


def test_report_fixture_is_what_derive_returns():
    view = derive(load("split_brain_events.json"))
    report = load("report.json")
    for key in ("state", "live", "conflicts", "incomplete"):
        assert view[key] == report[key], key

