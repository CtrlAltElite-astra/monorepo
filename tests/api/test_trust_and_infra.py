"""Signing keys, integrity at ingest (T10), CORS, body bound and the schema migration (owner: Akshit)."""
import base64
import json
import sqlite3

import pytest

from region import config, store
from region.engine import canonical
from region.main import cors_origins
from region.sync import ingest
from region.trust import keys, signing

from conftest import KEY, create_value

ORIGIN_OK = {"Origin": "http://localhost:5173"}


# ---- keys and signatures ---------------------------------------------------------------------
def test_ensure_region_key_is_idempotent_and_publishes_only_the_public_half(region, tmp_path):
    first = keys.ensure_region_key("mum")
    assert keys.ensure_region_key("mum") == first
    assert (tmp_path / "keys" / "mum.pub").read_text() == first
    assert first not in (tmp_path / "keys" / "mum.key").read_text()  # the seed is not the public key
    assert keys.registry()["mum"] == first


def test_only_this_regions_key_signs_and_everyone_with_the_registry_can_verify(region):
    keys.ensure_region_key("pun")
    sig = signing.sign("ab" * 32)
    assert signing.verify("mum", "ab" * 32, sig)
    assert not signing.verify("pun", "ab" * 32, sig)       # a node never signs another node's events
    assert not signing.verify("mum", "cd" * 32, sig)       # a different hash
    assert signing.is_known_origin("pun") and not signing.is_known_origin("nobody")


@pytest.mark.parametrize("origin,hash_hex,sig", [
    ("mum", "ab" * 32, "not base64 !!"), ("mum", "ab" * 32, ""), ("mum", "ab" * 32, "AAAA"),
    ("ghost", "ab" * 32, "AAAA"), (None, "ab" * 32, "AAAA"), ("mum", None, "AAAA"), ("mum", "ab" * 32, None),
    ("mum", "ab" * 32, 42),
])
def test_verify_is_false_and_never_raises_on_garbage(region, origin, hash_hex, sig):
    assert signing.verify(origin, hash_hex, sig) is False


def test_the_registry_merges_committed_generated_and_local_keys_later_wins(region, tmp_path, monkeypatch):
    keys_dir = tmp_path / "keys"
    (keys_dir / "public_keys.json").write_text(json.dumps({"del": "AAAA", "mum": "STALE"}))
    assert keys.registry()["del"] == "AAAA"
    assert keys.registry()["mum"] == (keys_dir / "mum.pub").read_text()  # a region's own .pub wins over the file
    other = tmp_path / "only.json"
    other.write_text(json.dumps({"pun": "BBBB"}))
    monkeypatch.setenv("PUBLIC_KEYS_PATH", str(other))
    assert keys.registry()["pun"] == "BBBB" and "del" not in keys.registry()


def test_a_region_id_cannot_silently_take_a_new_key(region, tmp_path):
    """A wiped key under a known id must not be able to sign: peers would reject, or worse accept, its history."""
    registered = keys.public_key("mum")
    assert registered is not None
    (tmp_path / "keys" / "mum.key").unlink()
    (tmp_path / "keys" / "mum.pub").unlink()
    (tmp_path / "keys" / "public_keys.json").write_text(json.dumps({"mum": base64.b64encode(b"x" * 32).decode()}))
    keys.reset_cache()
    with pytest.raises(RuntimeError, match="already registered"):
        keys.ensure_region_key("mum")


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
    keys.ensure_region_key("del")
    ev = signed_event(mk_event)
    ev["sig"] = base64.b64encode(keys.private_key("del").sign(ev["hash"].encode("ascii"))).decode()  # del signs pun's event
    assert rejected(ingest.ingest([ev], source="pun")) == {"pun:1": "bad_sig"}


def test_a_signed_event_from_an_unknown_origin_is_quarantined(client, mk_event):
    ev = mk_event("ghost", 1, "CREATE", "R-bbbbbbbb", value=create_value())
    ev["sig"] = base64.b64encode(b"\0" * 64).decode()
    assert rejected(ingest.ingest([ev], source="pun")) == {"ghost:1": "bad_sig"}


def test_a_duplicate_of_a_good_event_is_ignored_and_a_reused_id_is_quarantined(client, mk_event):
    ev = signed_event(mk_event)
    assert ingest.ingest([ev], source="pun")["new"] == 1
    assert ingest.ingest([ev], source="pun")["dup"] == 1
    other = signed_event(mk_event, hlc=(5_000_000_000_000, 0))  # same event_id, different content
    assert rejected(ingest.ingest([other], source="pun")) == {"pun:1": "id_reused"}


@pytest.mark.xfail(strict=True, reason="Lane D's ingest verifies only events that carry a sig; unsigned foreign events "
                                       "are accepted (docs/lane-a-handoff.md). Remove this xfail when ingest fails closed.")
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


def test_a_database_from_before_v11_is_upgraded_in_place_and_keeps_its_data(tmp_path, monkeypatch):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript(LEGACY)
    con.execute("INSERT INTO node_meta VALUES ('region','mum'), ('next_seq','2')")
    con.execute("INSERT INTO events VALUES ('mum:1','mum',1,'R-mum-1','CREATE',NULL,1,1,0,'h','{}',1.0)")
    con.commit()
    con.close()
    monkeypatch.setattr(config, "settings", __import__("dataclasses").replace(config.settings, db_path=str(db), region="mum"))
    store.init()
    store.init()  # a second start must not try to add the column again
    with store.read() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(events)")}
        assert "op_id" in cols
        assert c.execute("SELECT op_id FROM events WHERE event_id='mum:1'").fetchone()[0] is None  # the old row is intact
        assert {r[0] for r in c.execute("SELECT name FROM schema_migrations")} == {"002_op_id.sql"}
        assert c.execute("SELECT COUNT(*) FROM carried_events").fetchone()[0] == 0
        assert any(r[1] == "ix_events_op" for r in c.execute("SELECT type, name FROM sqlite_master WHERE type='index'"))
    store.close()
