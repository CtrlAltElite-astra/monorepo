"""Contract v1.1: signed events, /healthz, /v1/events/has, /v1/carry, op_id idempotency, migration."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from region import store
from region.engine import signing
from region.main import app
from tests.api.conftest import KEY, create_value


@pytest.fixture
def keys(tmp_path, monkeypatch):
    """Ed25519 keys for mum and pun in a temp dir (never the developer's real keys)."""
    monkeypatch.setenv("SIGNING_KEYS_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("PUBLIC_KEYS_FILE", str(tmp_path / "pub.json"))
    signing.ensure_keys(["mum", "pun"])
    return tmp_path


@pytest.fixture
def signed(mk_event):
    """A well-formed event from pun, signed with pun's key."""
    def _make(seq=1, type_="CREATE", report="R-pun-1", **kw):
        ev = mk_event("pun", seq, type_, report, **kw)
        ev["sig"] = signing.sign("pun", ev["hash"])
        return ev
    return _make


def carry(client, events, headers=KEY):
    return client.post("/v1/carry", json={"events": events}, headers=headers)


# ---- signing -----------------------------------------------------------------
def test_local_writes_are_signed_when_a_key_is_provisioned(keys, create, client):
    r = create()
    ev = r.json()["event"]
    assert ev["sig"] and signing.verify("mum", ev["hash"], ev["sig"])
    assert ev["op_id"] is None and ev["observed"] is None
    assert not signing.verify("pun", ev["hash"], ev["sig"])          # not pun's signature


def test_writes_stay_unsigned_and_work_without_keys(create, monkeypatch, tmp_path):
    monkeypatch.setenv("SIGNING_KEYS_DIR", str(tmp_path / "none"))
    monkeypatch.setenv("PUBLIC_KEYS_FILE", str(tmp_path / "none.json"))
    r = create()
    assert r.status_code == 201 and r.json()["event"]["sig"] is None


def test_history_shows_the_signature(keys, create, client):
    create()
    (ev,) = client.get("/v1/reports/R-mum-1/history").json()
    assert signing.verify("mum", ev["hash"], ev["sig"])


# ---- healthz / events/has ------------------------------------------------------
def test_healthz_needs_no_key(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["region"] == "mum"
    assert r.json()["level"] in ("HEALTHY", "SYNCING", "ISOLATED", "CONSTRAINED", "RECOVERING", "OVERLOADED")


def test_events_has_lists_only_what_is_missing(create, client):
    create()
    r = client.post("/v1/events/has", json={"ids": ["mum:1", "mum:2", "pun:7"]}, headers=KEY)
    assert r.status_code == 200 and r.json() == {"missing": ["mum:2", "pun:7"]}
    assert client.post("/v1/events/has", json={"ids": ["mum:1"]}).status_code == 401


# ---- carry -----------------------------------------------------------------------
def test_carry_accepts_a_signed_event_once_then_reports_dup(keys, client, signed, create_value=create_value):
    ev = signed(value=create_value())
    r = carry(client, [ev])
    assert r.status_code == 200 and r.json()["results"] == [{"event_id": "pun:1", "result": "new"}]
    assert client.get("/v1/reports/R-pun-1").status_code == 200
    again = carry(client, [ev]).json()["results"]
    assert again == [{"event_id": "pun:1", "result": "dup"}]
    assert client.get("/v1/status", headers=KEY).json()["carry"]["new"] == 1


def test_carry_rejects_an_edited_event(keys, client, signed):
    ev = signed(value=create_value("Original"))
    ev["value"]["title"] = "Edited by the browser"
    (res,) = carry(client, [ev]).json()["results"]
    assert res["result"] == "rejected" and res["reason"] == "bad_hash"
    assert client.get("/v1/reports/R-pun-1").status_code == 404


def test_carry_rejects_a_forged_signature(keys, client, mk_event):
    ev = mk_event("pun", 1, "CREATE", "R-pun-1", value=create_value())
    ev["sig"] = signing.sign("mum", ev["hash"])           # signed with the WRONG node's key
    (res,) = carry(client, [ev]).json()["results"]
    assert res == {"event_id": "pun:1", "result": "rejected", "reason": "bad_sig"}


def test_carry_rejects_unsigned_and_unknown_origin(keys, client, mk_event):
    unsigned = mk_event("pun", 1, "CREATE", "R-pun-1", value=create_value(), signed=False)
    stranger = mk_event("ghost", 1, "CREATE", "R-ghost-1", value=create_value(), signed=False)
    results = carry(client, [unsigned, stranger]).json()["results"]
    assert [r["reason"] for r in results] == ["bad_sig", "unknown_origin"]


def test_carry_is_disabled_without_a_key_registry(client, mk_event):
    ev = mk_event("pun", 1, "CREATE", "R-pun-1", value=create_value(), signed=False)
    (res,) = carry(client, [ev]).json()["results"]
    assert res["reason"] == "unknown_origin"        # fails closed, never trusts an unsigned event


def test_carry_never_imports_this_nodes_own_missing_history(keys, client, mk_event):
    ev = mk_event("mum", 5, "CREATE", "R-mum-5", value=create_value())
    ev["sig"] = signing.sign("mum", ev["hash"])
    (res,) = carry(client, [ev]).json()["results"]
    assert res == {"event_id": "mum:5", "result": "rejected", "reason": "own_origin"}
    assert client.get("/v1/status", headers=KEY).json()["heads"] == {"mum": 0}


def test_carry_shape_limits_and_auth(keys, client, signed):
    assert carry(client, [{"nonsense": 1}]).json()["results"][0]["reason"] == "shape"
    assert carry(client, [signed(i + 1, value=create_value()) for i in range(201)]).status_code == 422
    assert carry(client, [], headers={"X-API-Key": "nope"}).status_code == 401
    assert client.post("/v1/carry", json={"events": "x"}, headers=KEY).status_code == 422


def test_carried_events_replicate_like_any_other(keys, client, signed, mk_event):
    create_ev = signed(1, value=create_value())
    note = mk_event("pun", 2, "NOTE", "R-pun-1", value={"text": "two trapped"})
    note["sig"] = signing.sign("pun", note["hash"])
    carry(client, [create_ev, note])
    rep = client.get("/v1/reports/R-pun-1").json()
    assert rep["state"]["notes"][0]["text"] == "two trapped"
    assert client.get("/v1/status", headers=KEY).json()["heads"]["pun"] == 2


# ---- op_id idempotency ---------------------------------------------------------------
def test_same_op_id_twice_is_one_event_and_a_replay(client, region):
    body = {"title": "Cart", "category": "cart", "severity": 1, "op_id": "op-1"}
    first = client.post("/v1/reports", json=body, headers=KEY)
    second = client.post("/v1/reports", json=body, headers=KEY)
    assert first.status_code == 201 and second.status_code == 200
    assert second.headers.get("idempotent-replay") == "true"
    assert first.json()["event_id"] == second.json()["event_id"] == "mum:1"
    assert client.get("/v1/status", headers=KEY).json()["event_count"] == 1


def test_same_op_id_different_body_is_409(client, create):
    create(op_id="op-2")
    r = client.post("/v1/reports", json={"title": "Different", "category": "cart", "severity": 1, "op_id": "op-2"}, headers=KEY)
    assert r.status_code == 409 and r.json() == {"error": "op_id_reuse"}


def test_retry_after_the_original_arrived_by_carry_writes_nothing_new(keys, client, signed):
    carry(client, [signed(1, value=create_value())])
    note = signed(2, "NOTE", value={"text": "add coffee"})   # created on pun, op_id below
    note["op_id"] = "op-coffee"
    from region.engine import canonical
    note["hash"] = canonical.event_hash(note)
    note["sig"] = signing.sign("pun", note["hash"])
    assert carry(client, [note]).json()["results"][0]["result"] == "new"
    before = client.get("/v1/status", headers=KEY).json()["event_count"]
    r = client.post("/v1/reports/R-pun-1/notes", json={"text": "add coffee", "op_id": "op-coffee"}, headers=KEY)
    assert r.status_code == 200 and r.json()["event_id"] == "pun:2"
    assert client.get("/v1/status", headers=KEY).json()["event_count"] == before


def test_idempotency_key_header_is_accepted(client, region):
    body = {"title": "Cart", "category": "cart", "severity": 1}
    h = {**KEY, "Idempotency-Key": "hdr-1"}
    a = client.post("/v1/reports", json=body, headers=h)
    b = client.post("/v1/reports", json=body, headers=h)
    assert a.json()["event_id"] == b.json()["event_id"] and b.status_code == 200


def test_client_report_id_is_used_and_cannot_be_taken_twice(client, region):
    body = {"title": "Cart", "category": "cart", "severity": 1, "report_id": "R-0a1b2c3d"}
    r = client.post("/v1/reports", json=body, headers=KEY)
    assert r.status_code == 201 and r.json()["report"]["report_id"] == "R-0a1b2c3d"
    assert client.post("/v1/reports", json=body, headers=KEY).status_code == 409
    bad = client.post("/v1/reports", json={**body, "report_id": "R-nope"}, headers=KEY)
    assert bad.status_code == 422


def test_close_and_note_carry_observed_ids_and_raise_the_review_item(client, create):
    create(category="cart")
    client.patch("/v1/reports/R-mum-1", json={"field": "status", "value": "closed"}, headers=KEY)
    note = client.post("/v1/reports/R-mum-1/notes", json={"text": "late"}, headers=KEY).json()["event"]
    history = client.get("/v1/reports/R-mum-1/history").json()
    close = [e for e in history if e["field"] == "status"][0]
    assert close["observed"] is not None and note["observed"] == ["mum:2"]   # note saw the live close
    assert client.get("/v1/reports/R-mum-1").json()["conflicts"] == []       # sequential, not concurrent


# ---- migration -------------------------------------------------------------------------
def test_old_database_gets_op_id_column_and_stale_views_are_rebuilt(tmp_path, monkeypatch):
    import dataclasses
    from region import config
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(store.MIGRATION.read_text(encoding="utf-8"))   # v1.0 schema, no op_id
    con.close()
    s = dataclasses.replace(config.settings, region="mum", db_path=str(path), peers={})
    monkeypatch.setattr(config, "settings", s)
    store.init()
    with store.read() as conn:
        assert "op_id" in {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    # a stored view from an older policy is re-derived on start
    from tests.api.conftest import create_value as cv
    from region.api import writes
    writes.local_write(None, "CREATE", None, cv("x"), "a")
    with store.tx() as conn:
        v = store.get_view(conn, "R-mum-1")
        v["policy_version"] = 0
        store.save_view(conn, "R-mum-1", v)
    store.close()
    store.init()
    with store.read() as conn:
        assert store.get_view(conn, "R-mum-1")["policy_version"] == 1
    store.close()
