"""Signatures at the API, integrity at ingest (T10), CORS, body bound and the schema migration (owner: Akshit)."""
import json
import sqlite3

import pytest

from region import config, store
from region.engine import canonical, signing
from region.main import cors_origins
from region.sync import ingest

from conftest import KEY, create_value

ORIGIN_OK = {"Origin": "http://localhost:5173"}


# ---- signatures (the signing implementation is region/engine/signing.py) ------------------------------
def test_ensure_keys_is_idempotent_and_the_registry_holds_only_public_keys(region, tmp_path):
    first = signing.public_keys()["mum"]
    signing.ensure_keys(["mum"])
    assert signing.public_keys()["mum"] == first
    private = (tmp_path / "keys" / "mum.key").read_text().strip()
    assert private != first and private not in (tmp_path / "pub.json").read_text()  # the seed never leaves keys/


def test_a_node_signs_with_its_own_key_and_everyone_with_the_registry_can_verify(region):
    signing.ensure_keys(["pun"])
    sig = signing.sign("mum", "ab" * 32)
    assert signing.verify("mum", "ab" * 32, sig)
    assert not signing.verify("pun", "ab" * 32, sig)       # a node never signs another node's events
    assert not signing.verify("mum", "cd" * 32, sig)       # a different hash
    assert signing.has_key("pun") and not signing.has_key("nobody")
    assert signing.sign("nobody", "ab" * 32) is None       # no key provisioned: unsigned, never a made-up signature


@pytest.mark.parametrize("origin,sig", [
    ("mum", "not base64 !!"), ("mum", ""), ("mum", "AAAA"), ("ghost", "AAAA"), (None, "AAAA"), ("mum", None), ("mum", 42),
])
def test_verify_fails_closed_and_never_raises_on_garbage(region, origin, sig):
    assert signing.verify(origin, "ab" * 32, sig) is False


# ---- T10: integrity at ingest -------------------------------------------------------------------------
def signed_event(mk_event, seq=1, **over):
    return mk_event("pun", seq, "CREATE", "R-aaaaaaaa", value=create_value("Dev"), signed=True, **over)


def rejected(result):
    return {r["event_id"]: r["reason"] for r in result["rejected_list"]}


def status_digest(client):
    s = client.get("/v1/status").json()
    return s["event_digest"], s["state_hash"], s["quarantined"]


def test_a_correctly_signed_foreign_event_is_stored(client, mk_event):
    assert ingest.ingest([signed_event(mk_event)], source="pun")["new"] == 1


def test_an_edited_body_is_quarantined_and_state_is_unchanged(client, mk_event):
    before = status_digest(client)
    ev = signed_event(mk_event)
    ev["value"] = create_value("Mallory")  # the hash no longer matches the body
    assert rejected(ingest.ingest([ev], source="pun")) == {"pun:1": "bad_hash"}
    digest, state_hash, quarantined = status_digest(client)
    assert (digest, state_hash) == before[:2] and quarantined == before[2] + 1


def test_an_edited_body_with_a_recomputed_hash_fails_the_signature(client, mk_event):
    before = status_digest(client)
    ev = signed_event(mk_event)
    ev["value"] = create_value("Mallory")
    ev["hash"] = canonical.event_hash(ev)  # the attacker fixes the hash but cannot sign it
    assert rejected(ingest.ingest([ev], source="pun")) == {"pun:1": "bad_sig"}
    assert status_digest(client)[:2] == before[:2]


def test_an_event_signed_with_the_wrong_key_is_quarantined(client, mk_event):
    signing.ensure_keys(["del"])
    ev = signed_event(mk_event)
    ev["sig"] = signing.sign("del", ev["hash"])  # del signs pun's event
    assert rejected(ingest.ingest([ev], source="pun")) == {"pun:1": "bad_sig"}


def test_a_signed_event_from_an_unknown_origin_is_quarantined(client, mk_event):
    ev = mk_event("ghost", 1, "CREATE", "R-bbbbbbbb", value=create_value())
    ev["sig"] = "AAAA"
    assert rejected(ingest.ingest([ev], source="pun")) == {"ghost:1": "bad_sig"}


def test_a_duplicate_of_a_good_event_is_ignored_and_a_reused_id_is_quarantined(client, mk_event):
    ev = signed_event(mk_event)
    assert ingest.ingest([ev], source="pun")["new"] == 1
    assert ingest.ingest([ev], source="pun")["dup"] == 1
    other = signed_event(mk_event, hlc=(5_000_000_000_000, 0))  # same event_id, different content
    assert rejected(ingest.ingest([other], source="pun")) == {"pun:1": "id_reused"}


def test_an_event_that_arrives_by_carry_is_marked_carried_in_history(client, mk_event):
    ev = signed_event(mk_event)
    r = client.post("/v1/carry", json={"events": [ev]}, headers=KEY)
    assert r.json()["results"] == [{"event_id": "pun:1", "result": "new"}]
    (row,) = client.get("/v1/reports/R-aaaaaaaa/history").json()
    assert row["carried"] is True and row["sig_status"] == "verified"
    again = client.post("/v1/carry", json={"events": [ev]}, headers=KEY)
    assert again.json()["results"] == [{"event_id": "pun:1", "result": "dup"}]


@pytest.mark.xfail(strict=True, reason="Plan 6.2: a received event is checked for signature too. Today peers authenticate "
                                       "with X-Peer-Token, so ingest accepts an UNSIGNED foreign event (contracts.md 7). "
                                       "Remove this xfail when ingest fails closed.")
def test_an_unsigned_foreign_event_is_rejected(client, mk_event):
    ev = mk_event("pun", 1, "CREATE", "R-aaaaaaaa", value=create_value("Dev"))  # no signature at all
    assert ingest.ingest([ev], source="pun")["rejected"] == 1


# ---- CORS and body bound ------------------------------------------------------------------------------
def test_the_web_origin_is_allowed_with_the_documented_headers(client):
    r = client.options("/v1/reports", headers={**ORIGIN_OK, "Access-Control-Request-Method": "POST",
                                               "Access-Control-Request-Headers": "x-api-key,content-type,idempotency-key"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "idempotency-key" in r.headers["access-control-allow-headers"].lower()


def test_another_origin_gets_no_cors_headers(client):
    r = client.options("/v1/reports", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers


def test_replay_and_retry_headers_are_exposed_to_the_browser(client):
    body = {"report_id": "R-3f9a2c10", "op_id": "o1", "title": "A", "category": "cart", "severity": 1}
    client.post("/v1/reports", json=body, headers=KEY)
    r = client.post("/v1/reports", json=body, headers={**KEY, **ORIGIN_OK})
    assert r.headers["idempotent-replay"] == "true"
    assert "idempotent-replay" in r.headers["access-control-expose-headers"].lower()


def test_cors_origins_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", " https://a.example , https://b.example ,")
    assert cors_origins() == ["https://a.example", "https://b.example"]
    monkeypatch.delenv("CORS_ORIGINS")
    assert "http://localhost:5173" in cors_origins()


def test_an_oversized_body_is_422_and_carries_cors_headers(client):
    big = "x" * (512 * 1024 + 1)
    r = client.post("/v1/reports", content=json.dumps({"title": big}), headers={**KEY, **ORIGIN_OK, "Content-Type": "application/json"})
    assert r.status_code == 422 and "too large" in r.json()["detail"]
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


# ---- schema migration -----------------------------------------------------------------------------------
LEGACY = """
PRAGMA journal_mode=WAL;
CREATE TABLE node_meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE events (event_id TEXT PRIMARY KEY, origin TEXT NOT NULL, seq INTEGER NOT NULL, report_id TEXT NOT NULL,
  type TEXT NOT NULL, field TEXT, priority INTEGER NOT NULL, hlc_l INTEGER NOT NULL, hlc_c INTEGER NOT NULL,
  hash TEXT NOT NULL, body TEXT NOT NULL, received_at REAL NOT NULL, UNIQUE (origin, seq));
CREATE TABLE reports (report_id TEXT PRIMARY KEY, view TEXT NOT NULL, n_conflicts INTEGER NOT NULL,
  needs_review INTEGER NOT NULL, incomplete INTEGER NOT NULL, updated_at REAL NOT NULL);
"""


def legacy_db(tmp_path, monkeypatch, already_has_op_id=False):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript(LEGACY)
    if already_has_op_id:  # what Lane C's first cut of the migration did before this runner existed
        con.execute("ALTER TABLE events ADD COLUMN op_id TEXT")
        con.execute("CREATE INDEX ix_events_op ON events (op_id)")
    con.execute("INSERT INTO node_meta VALUES ('region','mum'), ('next_seq','2')")
    con.execute("INSERT INTO events (event_id,origin,seq,report_id,type,field,priority,hlc_l,hlc_c,hash,body,received_at) "
                "VALUES ('mum:1','mum',1,'R-mum-1','CREATE',NULL,1,1,0,'h','{}',1.0)")
    con.commit()
    con.close()
    monkeypatch.setattr(config, "settings", __import__("dataclasses").replace(config.settings, db_path=str(db), region="mum"))


@pytest.mark.parametrize("already_has_op_id", [False, True], ids=["from-v1", "already-upgraded-by-lane-c"])
def test_a_database_from_before_v11_is_upgraded_in_place_and_keeps_its_data(tmp_path, monkeypatch, already_has_op_id):
    legacy_db(tmp_path, monkeypatch, already_has_op_id)
    store.init()
    store.init()  # a second start must not try to add the column again
    with store.read() as c:
        assert "op_id" in {r[1] for r in c.execute("PRAGMA table_info(events)")}
        assert c.execute("SELECT op_id FROM events WHERE event_id='mum:1'").fetchone()[0] is None  # the old row is intact
        assert {r[0] for r in c.execute("SELECT name FROM schema_migrations")} == {"002_op_id.sql"}
        assert c.execute("SELECT COUNT(*) FROM carried_events").fetchone()[0] == 0
        assert any(r[1] == "ix_events_op" for r in c.execute("SELECT type, name FROM sqlite_master WHERE type='index'"))
    store.close()
