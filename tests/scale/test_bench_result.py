"""The benchmark result file is well formed and never invents a number (owner: Rahil). Scaling plan, lane B task 1.

docs/scale/scale-result.json is written by scripts/bench_scale.py and copied to
frontend-new/public/scale-result.json for the Scaling tab. A mode the regions could not run is
"unsupported" with every number null; a measured mode has real counts that add up.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "docs" / "scale" / "scale-result.json"
WEB_COPY = ROOT / "frontend-new" / "public" / "scale-result.json"
NUMBERS = ("writes_per_s", "p50_ms", "p95_ms")


def rows(result):
    for row in result.get("vertical", []):
        yield f"vertical {row['power']}", row
    for row in result.get("horizontal", []):
        yield f"horizontal {row['regions']}", row
    for side, row in result.get("compare", {}).items():
        yield f"compare {side}", row


@pytest.fixture(scope="module")
def result():
    if not RESULT.exists():
        pytest.skip("no benchmark result yet: python scripts/bench_scale.py --vertical --horizontal --compare")
    return json.loads(RESULT.read_text(encoding="utf-8"))


def test_the_result_has_the_contract_shape(result):
    assert {"measured_at", "machine", "writes", "vertical", "horizontal", "compare"} <= set(result)
    assert [r["power"] for r in result["vertical"]] == ["low", "medium", "high"]
    assert [r["regions"] for r in result["horizontal"]] == list(range(1, len(result["horizontal"]) + 1))
    assert set(result["compare"]) == {"one_high", "three_low"}


def test_no_number_is_invented(result):
    for name, row in rows(result):
        assert row["status"] in ("measured", "unsupported"), name
        if row["status"] == "unsupported":
            assert all(row[k] is None for k in NUMBERS) and row.get("reason"), name
        else:
            assert row["accepted"] + row["refused"] + row["errors"] == result["writes"], name
            assert row["writes_per_s"] > 0 and 0 < row["p50_ms"] <= row["p95_ms"], name
            assert abs(row["writes_per_s"] - row["accepted"] / row["seconds"]) < 0.1, name


def test_power_modes_are_unsupported_exactly_when_power_is(result):
    needs_power = [row for name, row in rows(result) if not name.startswith("horizontal")]
    if not result["power_supported"]:
        assert all(row["status"] == "unsupported" for row in needs_power)
        assert all(row["power"] == "default" for row in result["horizontal"])


def test_the_console_reads_the_same_file(result):
    if WEB_COPY.parent.is_dir():
        assert json.loads(WEB_COPY.read_text(encoding="utf-8")) == result
