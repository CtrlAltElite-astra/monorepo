"""GET /v1/status and /v1/health (owner: Akshit). PDF 1 acceptance 4 + the contract fixture."""
import dataclasses
import json
import re
from pathlib import Path

from contracts.schemas import LEVELS
from region import config, store
from region.engine import digest
from region.sync import ingest, state

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


def test_queue_depth_risk_and_carry_counters_are_present(client):
    s = status(client)
    assert set(s["queues"]) == {"P0", "P1", "P2"} and set(s["risk"]) == {"unreplicated", "critical_unreplicated", "oldest_age_ms"}
    assert set(s["carry"]) == {"received", "new", "dup", "rejected"}  # this node's carry counters since it started


def test_peers_carry_the_v11_keys(client):
    p = status(client)["peers"]["pun"]
    assert p["acked"] == p["their_heads"] == {}  # nothing acknowledged yet
    assert p["last_ok_ms"] is None and p["reachable"] is False


def test_data_at_risk_counts_events_no_peer_has_acknowledged(create, client, headers):
    assert status(client)["risk"] == {"unreplicated": 0, "critical_unreplicated": 0, "oldest_age_ms": 0}
    create(category="cart", severity=1)                                  # P1
    create(category="cart", severity=5)                                  # P0 (severity >= 4)
    client.post("/v1/reports/R-mum-1/notes", json={"text": "x"}, headers=headers)  # P2 bulk
    risk = status(client)["risk"]
    assert (risk["unreplicated"], risk["critical_unreplicated"]) == (3, 1)
    assert risk["oldest_age_ms"] >= 0
    state.save_peer_view("pun", {"mum": 2})                              # pun acknowledges mum:1 and mum:2
    assert status(client)["risk"]["unreplicated"] == 1                   # only mum:3 is still only here
    state.save_peer_view("pun", {"mum": 3})
    assert status(client)["risk"] == {"unreplicated": 0, "critical_unreplicated": 0, "oldest_age_ms": 0}


def test_risk_uses_the_most_advanced_peer_and_survives_a_dead_one(create, client, monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(
        config.settings, peers={"pun": "http://127.0.0.1:9", "del": "http://127.0.0.1:9"}))
    create(category="cart", severity=1)
    create(category="cart", severity=1)
    state.save_peer_view("del", {"mum": 2})   # del holds both; pun (silent) holds none
    assert status(client)["risk"]["unreplicated"] == 0  # one surviving copy is enough to not be "at risk"


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
