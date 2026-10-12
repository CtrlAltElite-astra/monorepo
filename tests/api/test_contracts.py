"""Contract guards (owner: Akshit): fixtures stay valid, schemas stays pure, docs match code."""
import ast
import json
from pathlib import Path

import pytest

from contracts import schemas
from region.engine import canonical
from region.engine.derive import derive

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "contracts" / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_split_brain_fixture_events_are_well_formed():
    events = load("split_brain_events.json")
    assert [e["event_id"] for e in events] == ["mum:1", "mum:2", "pun:1", "pun:2"]
    for e in events:
        assert set(e) == set(schemas.EVENT_KEYS)
        assert e["hash"] == canonical.event_hash(e)
        assert all(k in e for k in schemas.EVENT_REQUIRED)
        assert e["event_id"] == f"{e['origin']}:{e['seq']}"


def test_split_brain_fixture_reproduces_the_severity_conflict():
    v = derive(load("split_brain_events.json"))
    assert [c["field"] for c in v["conflicts"]] == ["severity"]
    assert sorted(v["conflicts"][0]["values"]) == [4, 5]
    assert v["state"]["notes"] == [{"id": "pun:2", "text": "two people still trapped"}]


def test_report_fixture_is_what_derive_produces():
    v = derive(load("split_brain_events.json"))
    r = load("report.json")
    assert r["state"] == v["state"] and r["live"] == v["live"] and r["conflicts"] == v["conflicts"]
    assert r["consistency"]["replication"] in schemas.REPLICATION_STATES


def test_consistency_block_keys_match_fixture_and_live_api(client, create):
    assert set(load("report.json")["consistency"]) == set(schemas.CONSISTENCY_KEYS)
    create()
    live = client.get("/v1/reports/R-mum-1").json()["consistency"]
    assert set(live) == set(schemas.CONSISTENCY_KEYS)
    assert live["replication"] in schemas.REPLICATION_STATES


def test_contract_docs_cover_history_roles_and_the_consistency_block():
    doc = (ROOT / "contracts" / "contracts.md").read_text(encoding="utf-8")
    for needle in ("GET /v1/reports/{id}/history", "### 3.1 Roles", "### 4.0 Report `consistency` block",
                   "confirmed_by", "awaiting", "stale_possible", "401", "403"):
        assert needle in doc, f"contracts.md is missing: {needle}"
    # history, resolve and review are implemented, so none of them may still be listed as reserved
    reserved = doc.split("Reserved, not built yet (do not reuse):")[1].split("\n\n")[0]
    for built in ("/history", "/resolve", "/v1/review"):
        assert built not in reserved
    assert "/v1/metrics" in reserved


def test_the_v11_contract_documents_every_new_key_endpoint_and_error():
    doc = (ROOT / "contracts" / "contracts.md").read_text(encoding="utf-8")
    for needle in (*schemas.EVENT_OPTIONAL_KEYS, "Idempotent-Replay", "POST /v1/carry", "POST /v1/events/has", "GET /healthz",
                   "POST /v1/reports/{id}/resolve", "GET /v1/review", "op_id_reuse", "report_exists", "no_conflict", "stale_edit",
                   "CORS_ORIGINS", "sig_status", "carried", "cart", "R-[0-9a-f]{8}"):
        assert needle in doc, f"contracts.md is missing: {needle}"
    assert "cart" in schemas.CATEGORIES and schemas.AUTH_MODES == ("online", "cached", "stale")
    assert (schemas.MAX_SUPERSEDES, schemas.MAX_CARRY, schemas.MAX_HAS, schemas.MAX_BODY_BYTES) == (16, 200, 500, 512 * 1024)


def test_the_v12_contract_documents_scaling_rate_limiting_and_identity_claims():
    """Section 8 is what four lanes build against: every name in it must stay in the document."""
    doc = (ROOT / "contracts" / "contracts.md").read_text(encoding="utf-8")
    assert "## 8. v1.2 additions" in doc
    for needle in ("RATE_ACTOR_BURST", "RATE_ACTOR_REFILL_PER_S", "REGION_WRITE_LIMIT_PER_S", "POWER",
                   "rate_actor_burst", "rate_actor_refill_per_s", "region_write_limit_per_s",
                   '"error": "rate_limited"', '"scope": "actor"', "retry_after_ms", '"error": "overloaded"',
                   '"error": "claim_required"', "X-RateLimit-Limit", "X-RateLimit-Remaining",
                   "writes_per_s", "queue_depth", "actor_burst", "actor_refill_per_s", "region_limit_per_s",
                   "refused_actor", "refused_region", "`blr`", "8004", "subject_key", "has_claim", "is_revoked",
                   "revoked_credential", '"requires": "age_over_18"'):
        assert needle in doc, f"contracts.md (v1.2) is missing: {needle}"
    changelog = (ROOT / "contracts" / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "v1.2" in changelog and "rate_limited" in changelog


def test_partner_contract_matches_the_adapter_interface():
    from region.trust import adapter
    doc = (ROOT / "contracts" / "partner-contract.md").read_text(encoding="utf-8")
    for endpoint in ("/trust/keys", "/credentials/verify", "/credentials/status-list", "/anchor"):
        assert endpoint in doc
    for method in ("verify_actor", "check_evidence", "anchor", "peer_trust"):
        assert method in doc and callable(getattr(adapter.TrustAdapter, method))
    assert "must never block a local write" in doc


def test_status_fixture_carries_the_additive_blocks():
    s = load("status.json")
    assert set(s["risk"]) == set(schemas.RISK_KEYS) and set(s["queues"]) == set(schemas.QUEUE_KEYS)
    assert s["level"] in schemas.LEVELS


def test_schemas_module_is_stdlib_only_and_does_no_io():
    tree = ast.parse((ROOT / "contracts" / "schemas.py").read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]


def test_every_documented_level_and_state_is_in_the_contract_doc():
    doc = (ROOT / "contracts" / "contracts.md").read_text(encoding="utf-8")
    for name in (*schemas.LEVELS, *schemas.REPLICATION_STATES):
        assert name in doc, f"{name} missing from contracts.md"


def test_required_event_keys_are_a_subset_of_the_envelope():
    assert set(schemas.EVENT_REQUIRED) <= set(schemas.EVENT_KEYS)
    assert set(schemas.FIELDS) == {"title", "description", "category", "location",
                                   "severity", "status", "assigned_team", "people_estimate"}


@pytest.mark.parametrize("field,value", [
    ("title", "a"), ("title", "x" * 120), ("description", None), ("description", "x" * 2000),
    ("category", "road_block"), ("severity", 1), ("severity", 5), ("status", "cancelled"),
    ("location", None), ("location", {"lat_e6": -1, "lon_e6": 0}), ("assigned_team", None),
    ("assigned_team", "x" * 80), ("people_estimate", None), ("people_estimate", 0),
])
def test_validate_field_accepts_boundary_values(field, value):
    assert schemas.validate_field(field, value) == ("" if (field, value) == ("description", None) else value)


@pytest.mark.parametrize("field,value", [
    ("title", ""), ("title", 5), ("severity", 0), ("severity", 6), ("severity", 4.0), ("severity", False),
    ("status", "open "), ("category", "Flood"), ("location", {"lat_e6": 1, "lon_e6": 2, "x": 3}),
    ("location", {"lat_e6": True, "lon_e6": 2}), ("assigned_team", "x" * 81), ("people_estimate", -1),
    ("people_estimate", True), ("notes", []), ("unknown", 1),
])
def test_validate_field_rejects_bad_values(field, value):
    with pytest.raises(ValueError):
        schemas.validate_field(field, value)
