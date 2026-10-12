"""Roles and the trust adapter interface (owner: Akshit). PDF 2 section 5 role table, section 9, 16.1."""
import dataclasses
import sqlite3

import pytest

from region import config
from region.trust import adapter, roles

SUP = {"X-API-Key": "dev-supervisor"}
ADM = {"X-API-Key": "dev-admin"}
OFF = {"X-API-Key": "dev-officer"}
ODD = {"X-API-Key": "dev-viewer"}


@pytest.fixture
def keyed(client, monkeypatch):
    keys = {"dev-officer": ("officer-mum-1", "officer"), "dev-supervisor": ("supervisor-mum-1", "supervisor"),
            "dev-admin": ("admin-mum-1", "admin"), "dev-viewer": ("viewer-mum-1", "viewer")}
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, api_keys=keys))
    return client


def event_count(db_path):
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        con.close()


def body(**o):
    return {"title": "t", "category": "fire", "severity": 2, **o}


# ---- officers and supervisors write; the actor comes from the key -----------------------
@pytest.mark.parametrize("hdrs,actor", [(OFF, "officer-mum-1"), (SUP, "supervisor-mum-1")])
def test_officer_and_supervisor_can_create_set_and_note(keyed, region, hdrs, actor):
    r = keyed.post("/v1/reports", json=body(), headers=hdrs)
    assert r.status_code == 201
    assert keyed.patch("/v1/reports/R-mum-1", json={"field": "severity", "value": 4}, headers=hdrs).status_code == 200
    assert keyed.post("/v1/reports/R-mum-1/notes", json={"text": "x"}, headers=hdrs).status_code == 201
    history = keyed.get("/v1/reports/R-mum-1/history").json()
    assert {e["actor"] for e in history} == {actor}


# ---- everyone else gets 403, and nothing is stored --------------------------------------
@pytest.mark.parametrize("hdrs", [ADM, ODD], ids=["admin", "unknown-role"])
def test_other_roles_get_403_on_every_write_and_store_nothing(keyed, region, hdrs):
    keyed.post("/v1/reports", json=body(), headers=OFF)  # R-mum-1 exists
    before = event_count(region)
    assert keyed.post("/v1/reports", json=body(), headers=hdrs).status_code == 403
    assert keyed.patch("/v1/reports/R-mum-1", json={"field": "severity", "value": 4}, headers=hdrs).status_code == 403
    assert keyed.post("/v1/reports/R-mum-1/notes", json={"text": "x"}, headers=hdrs).status_code == 403
    assert event_count(region) == before


def test_403_names_the_role_and_401_is_still_for_unknown_keys(keyed):
    r = keyed.post("/v1/reports", json=body(), headers=ADM)
    assert r.status_code == 403 and "admin" in r.json()["detail"]
    assert keyed.post("/v1/reports", json=body(), headers={"X-API-Key": "nope"}).status_code == 401
    assert keyed.post("/v1/reports", json=body()).status_code == 401


def test_the_role_is_checked_before_the_body_is_looked_at(keyed):
    # a forbidden caller learns nothing about validation: 403, not 422
    assert keyed.post("/v1/reports", json={"title": ""}, headers=ADM).status_code == 403


def test_reads_stay_open_to_everyone(keyed):
    assert keyed.get("/v1/reports").status_code == 200
    assert keyed.get("/v1/status", headers=ODD).status_code == 200


# ---- the role table ----------------------------------------------------------------------
def test_role_table_matches_the_plan():
    assert roles.ROLES == ("officer", "supervisor", "admin")
    for action in ("create", "set", "note"):
        assert roles.may("officer", action) and roles.may("supervisor", action)
        assert not roles.may("admin", action)
    assert roles.may("supervisor", "resolve") and not roles.may("officer", "resolve")  # "officer resolve -> 403"
    assert roles.may("admin", "admin") and not roles.may("supervisor", "admin")
    assert not roles.may("officer", "no-such-action") and not roles.may(None, "create")


# ---- adapter interface ----------------------------------------------------------------------
def test_local_key_adapter_decisions(region):
    a = adapter.LocalKeyAdapter()
    ok = a.verify_actor("dev-officer", "create")
    assert (ok.allowed, ok.actor, ok.role, ok.mode, ok.reason) == (True, "officer-mum-1", "officer", "online", "")
    assert ok.region_scope == "mum"
    assert a.verify_actor("dev-officer").allowed is True  # authenticate only, no action check
    unknown = a.verify_actor("nope", "create")
    assert (unknown.allowed, unknown.reason, unknown.actor) == (False, adapter.UNKNOWN_CREDENTIAL, None)
    assert a.verify_actor(None, "create").reason == adapter.UNKNOWN_CREDENTIAL
    denied = a.verify_actor("dev-officer", "resolve")
    assert (denied.allowed, denied.reason, denied.role) == (False, adapter.FORBIDDEN, "officer")


def test_unsupported_partner_methods_return_neutral_results_instead_of_raising(region):
    a = adapter.LocalKeyAdapter()
    assert a.check_evidence("LOT-1", {}).verdict == "unverified"
    assert a.anchor("mum", 1, 10, "ab" * 32).anchored is False
    assert a.peer_trust("del", {}).risk == "unknown"
    with pytest.raises(NotImplementedError):
        adapter.TrustAdapter().verify_actor("k", "create")


def test_the_active_adapter_can_be_swapped_and_drives_the_write_gate(client):
    class Deny(adapter.TrustAdapter):
        def verify_actor(self, presentation, action=None):
            return adapter.AuthDecision(False, actor="x", role="officer", reason=adapter.FORBIDDEN)

    original = adapter.get_adapter()
    adapter.set_adapter(Deny())
    try:
        assert client.post("/v1/reports", json=body(), headers=OFF).status_code == 403
    finally:
        adapter.set_adapter(original)
    assert client.post("/v1/reports", json=body(), headers=OFF).status_code == 201
