"""GET /v1/status and /v1/health (owner: Akshit). PDF 1 acceptance 4 + the contract fixture."""
import dataclasses
import json
import re
from pathlib import Path

from contracts.schemas import LEVELS
from region import config, store
from region.engine import digest
from region.sync import ingest

from conftest import KEY, create_value

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def status(client):
    return client.get("/v1/status").json()


def test_status_keys_match_the_contract_fixture(client):
    fixture = json.loads((FIXTURES / "status.json").read_text(encoding="utf-8"))
    live = status(client)
    assert set(live) == set(fixture)
    assert set(live["peers"]["pun"]) == set(fixture["peers"]["pun"])


def test_empty_region(client):
    s = status(client)
    assert s["region"] == "mum" and s["schema_version"] == 1 and isinstance(s["policy_version"], int)
    assert s["heads"] == {"mum": 0} and s["event_count"] == 0
    assert HEX64.match(s["event_digest"]) and HEX64.match(s["state_hash"])
    assert s["quarantined"] == 0 and s["incomplete_reports"] == 0
    assert s["level"] in LEVELS


def test_heads_and_event_count_follow_local_writes(create, client):
    create()
    client.patch("/v1/reports/R-mum-1", json={"field": "severity", "value": 4}, headers=KEY)
    client.post("/v1/reports/R-mum-1/notes", json={"text": "x"}, headers=KEY)
    s = status(client)
    assert s["heads"] == {"mum": 3} and s["event_count"] == 3


def test_digests_are_the_engine_digests_and_stable(create, client):
    create()
    first = status(client)
    with store.read() as conn:
        assert first["event_digest"] == digest.event_digest(store.event_pairs(conn))
        assert first["state_hash"] == digest.state_hash(store.all_views(conn, include_incomplete=True))
    again = status(client)
    assert (again["event_digest"], again["state_hash"]) == (first["event_digest"], first["state_hash"])
    client.patch("/v1/reports/R-mum-1", json={"field": "severity", "value": 4}, headers=KEY)
    changed = status(client)
    assert changed["event_digest"] != first["event_digest"] and changed["state_hash"] != first["state_hash"]


def test_risk_and_queues_are_present_and_null_until_other_lanes_fill_them(client):
    s = status(client)
    assert "risk" in s
    if s["risk"] is not None:
        assert isinstance(s["risk"], dict)
        assert {"unreplicated", "critical_unreplicated", "oldest_age_ms"} <= set(s["risk"])
    assert "queues" in s
    if s["queues"] is not None:
        assert isinstance(s["queues"], dict)
        assert {"P0", "P1", "P2"} <= set(s["queues"])



def test_never_contacted_peer_counts_as_unreachable(client):
    s = status(client)
    p = s["peers"]["pun"]
    assert p["link"] == "up" and p["reachable"] is False and p["last_ok_s"] is None
    assert s["level"] == "ISOLATED"  # honest: we have not heard from the peer


def test_region_without_peers_is_healthy(client, monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, peers={}))
    s = status(client)
    assert s["peers"] == {} and s["level"] == "HEALTHY"


def test_ingested_events_move_the_heads_and_bad_ones_are_quarantined(client, mk_event):
    good = mk_event("pun", 1, "CREATE", "R-pun-1", value=create_value())
    tampered = dict(mk_event("pun", 2, "SET", "R-pun-1", field="severity", value=4), value=5)  # hash no longer matches
    ingest.ingest([good, tampered], source="pun")
    s = status(client)
    assert s["heads"] == {"mum": 0, "pun": 1} and s["event_count"] == 1 and s["quarantined"] == 1
