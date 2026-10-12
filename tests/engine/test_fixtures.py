"""The fixtures agree with the engine (owner: Rahil).

Lanes C and D build against contracts/fixtures/* and fixtures/events_*.json. If
the engine and a fixture drift apart this fails here, not on a screen. Regenerate
with python scripts/gen_fixtures.py and python scripts/gen_shop_fixtures.py.
"""
import json
import runpy
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


def test_shop_fixtures_are_what_the_engine_produces():
    # fixtures/events_*.json feed the web mock and the scenario scripts
    root = Path(__file__).resolve().parents[2]
    built = runpy.run_path(str(root / "scripts" / "gen_shop_fixtures.py"))["build"]()
    assert sorted(built) == ["events_close_vs_new_info.json", "events_op_id_retry.json", "events_split_brain.json"]
    for name, content in built.items():
        on_disk = json.loads((root / "fixtures" / name).read_text(encoding="utf-8"))
        assert on_disk == json.loads(json.dumps(content)), f"{name} is stale: run python scripts/gen_shop_fixtures.py"
        assert all(canonical.event_hash(ev) == ev["hash"] for ev in on_disk["events"])
        assert {"report_id": on_disk["report"]["report_id"], **derive(on_disk["events"])} == on_disk["report"]
