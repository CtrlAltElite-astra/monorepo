"""Reports API (owner: Akshit). PDF 1 Lane A acceptance checks plus the error paths.

In-process region on a temp SQLite file; see conftest.py.
"""
import json
import socket
import sqlite3
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from contracts.schemas import EVENT_KEYS
from region import config, store
from region.api import writes
from region.engine import canonical
from region.main import app
from region.sync import ingest, state

from conftest import KEY, create_value


def stored_events(db_path):
    """Read the events straight from the SQLite file with a SECOND connection."""
    con = sqlite3.connect(db_path)
    try:
        return [json.loads(r[0]) for r in con.execute("SELECT body FROM events ORDER BY seq")]
    finally:
        con.close()


def patch(client, rid, field, value, **extra):
    return client.patch(f"/v1/reports/{rid}", json={"field": field, "value": value, **extra}, headers=KEY)


# ---- acceptance 1: 201 + mum:1 + durable ------------------------------------
def test_create_returns_201_with_first_event_id(create):
    r = create()
    assert r.status_code == 201
    body = r.json()
    assert body["event_id"] == "mum:1"
    rep = body["report"]
    assert rep["report_id"] == "R-mum-1"
    assert rep["state"] == {
        "title": "Wall collapse, Kurla", "description": "", "category": "collapse", "location": None,
        "severity": 3, "status": "open", "assigned_team": None, "people_estimate": None, "notes": [],
    }
    assert rep["live"]["severity"] == ["mum:1"] and rep["conflicts"] == [] and rep["incomplete"] is False
    # pun is configured but has never confirmed anything and was never reached
    assert body["consistency"] == rep["consistency"] == {
        "replication": "LOCAL_ONLY", "confirmed_by": [], "awaiting": ["pun"], "stale_possible": True}


def test_201_is_returned_only_after_commit(create, region):
    assert create().status_code == 201
    # a different connection to the same file already sees the committed event and view
    con = sqlite3.connect(region)
    try:
        assert con.execute("SELECT event_id, type FROM events").fetchall() == [("mum:1", "CREATE")]
        assert con.execute("SELECT report_id FROM reports").fetchall() == [("R-mum-1",)]
        assert con.execute("SELECT v FROM node_meta WHERE k='next_seq'").fetchone()[0] == "2"
    finally:
        con.close()


def test_stored_event_matches_the_contract_envelope(create, region):
    create(actor="evil", description="x")  # an actor in the body must be ignored
    (ev,) = stored_events(region)
    assert set(ev) == set(EVENT_KEYS)
    assert ev["v"] == 1 and ev["origin"] == "mum" and ev["seq"] == 1 and ev["type"] == "CREATE"
    assert ev["field"] is None and ev["supersedes"] == [] and ev["priority"] == 1
    assert ev["actor"] == "officer-mum-1"  # taken from the API key, never the body
    assert ev["hash"] == canonical.event_hash(ev) and ev["sig"] is None
    assert len(ev["hlc"]) == 2 and all(isinstance(x, int) for x in ev["hlc"])


def test_restart_keeps_data_and_the_sequence_continues(create, client, region):
    create()
    patch(client, "R-mum-1", "severity", 4)
    before = stored_events(region)
    store.close()
    store.init()  # same file: what a restarted process does
    rep = client.get("/v1/reports/R-mum-1").json()
    assert rep["state"]["severity"] == 4
    r = create(title="Second")
    assert r.json()["event_id"] == "mum:3" and r.json()["report"]["report_id"] == "R-mum-3"
    after = stored_events(region)
    assert after[:2] == before  # history untouched
    hlcs = [tuple(e["hlc"]) for e in after]
    assert hlcs == sorted(hlcs) and len(set(hlcs)) == 3  # clock never repeats or goes back
    assert client.get("/v1/status").json()["heads"] == {"mum": 3}


# ---- acceptance 2: sequential edits are not conflicts -------------------------
def test_two_quick_patches_on_one_field_make_no_conflict(create, client, region):
    create()
    assert patch(client, "R-mum-1", "severity", 4).status_code == 200
    assert patch(client, "R-mum-1", "severity", 5).status_code == 200
    rep = client.get("/v1/reports/R-mum-1").json()
    assert rep["conflicts"] == [] and rep["state"]["severity"] == 5
    assert rep["live"]["severity"] == ["mum:3"]
    ev = stored_events(region)
    assert ev[1]["supersedes"] == ["mum:1"] and ev[2]["supersedes"] == ["mum:2"]


@pytest.mark.parametrize("field,value", [
    ("title", "Wall collapse (updated)"), ("description", "road blocked"), ("category", "fire"),
    ("location", {"lat_e6": 19_076_000, "lon_e6": 72_877_000}), ("severity", 5), ("status", "assigned"),
    ("assigned_team", "Alpha"), ("people_estimate", 12),
])
def test_every_field_can_be_set(create, client, field, value):
    create()
    r = patch(client, "R-mum-1", field, value)
    assert r.status_code == 200 and r.json()["event_id"] == "mum:2"
    assert r.json()["report"]["state"][field] == value


def test_null_clears_nullable_fields(create, client):
    create()
    patch(client, "R-mum-1", "assigned_team", "Alpha")
    r = patch(client, "R-mum-1", "assigned_team", None)
    assert r.status_code == 200 and r.json()["report"]["state"]["assigned_team"] is None


# ---- validation (write time only) --------------------------------------------
@pytest.mark.parametrize("body", [
    {"category": "collapse", "severity": 3},                                      # no title
    {"title": "", "category": "collapse", "severity": 3},
    {"title": "x" * 121, "category": "collapse", "severity": 3},
    {"title": "t", "category": "earthquake", "severity": 3},
    {"title": "t", "category": "collapse", "severity": 0},
    {"title": "t", "category": "collapse", "severity": 6},
    {"title": "t", "category": "collapse", "severity": 3, "location": {"lat_e6": 1}},
    {"title": "t", "category": "collapse", "severity": 3, "people_estimate": -1},
    {"title": "t", "category": "collapse", "severity": 3, "description": "x" * 2001},
])
def test_create_rejects_bad_input_and_stores_nothing(client, region, body):
    assert client.post("/v1/reports", json=body, headers=KEY).status_code == 422
    assert stored_events(region) == []


@pytest.mark.parametrize("field,value", [
    ("severity", 0), ("severity", 6), ("severity", "high"), ("severity", True), ("severity", None),
    ("status", "archived"), ("category", "earthquake"), ("title", ""), ("title", "x" * 121),
    ("description", "x" * 2001), ("location", {"lat_e6": 1}), ("location", {"lat_e6": 1.5, "lon_e6": 2}),
    ("people_estimate", -1), ("assigned_team", ""), ("notes", "x"), ("nonsense", 1),
])
def test_patch_rejects_bad_values_and_stores_nothing(create, client, region, field, value):
    create()
    assert patch(client, "R-mum-1", field, value).status_code == 422
    assert len(stored_events(region)) == 1  # only the CREATE


def test_patch_requires_a_value_key(create, client, region):
    create()
    patch(client, "R-mum-1", "assigned_team", "Alpha")
    # a forgotten "value" must not silently clear a nullable field (null has to be explicit)
    r = client.patch("/v1/reports/R-mum-1", json={"field": "assigned_team"}, headers=KEY)
    assert r.status_code == 422
    assert client.get("/v1/reports/R-mum-1").json()["state"]["assigned_team"] == "Alpha"
    assert len(stored_events(region)) == 2


# ---- auth ----------------------------------------------------------------------
@pytest.mark.parametrize("hdrs", [{}, {"X-API-Key": "nope"}])
def test_writes_need_a_known_api_key(create, client, region, hdrs):
    create()
    body = {"title": "t", "category": "fire", "severity": 2}
    assert client.post("/v1/reports", json=body, headers=hdrs).status_code == 401
    assert client.patch("/v1/reports/R-mum-1", json={"field": "severity", "value": 2}, headers=hdrs).status_code == 401
    assert client.post("/v1/reports/R-mum-1/notes", json={"text": "x"}, headers=hdrs).status_code == 401
    assert len(stored_events(region)) == 1


def test_reads_are_open(create, client):
    create()
    assert client.get("/v1/reports").status_code == 200
    assert client.get("/v1/reports/R-mum-1").status_code == 200
    assert client.get("/v1/status").status_code == 200
    assert client.get("/v1/health").json() == {"region": "mum", "ok": True}


# ---- 404 / 409 -----------------------------------------------------------------
def test_unknown_report_is_404(client):
    assert patch(client, "R-nope-9", "severity", 2).status_code == 404
    assert client.post("/v1/reports/R-nope-9/notes", json={"text": "x"}, headers=KEY).status_code == 404
    assert client.get("/v1/reports/R-nope-9").status_code == 404


def test_stale_expected_returns_409_with_live_ids_and_stores_nothing(create, client, region):
    create()
    assert patch(client, "R-mum-1", "severity", 4, expected=["mum:1"]).status_code == 200  # matches live
    r = patch(client, "R-mum-1", "severity", 2, expected=["mum:1"])  # live is now mum:2
    assert r.status_code == 409
    assert r.json() == {"error": "stale_edit", "live": ["mum:2"]}
    assert len(stored_events(region)) == 2
    assert patch(client, "R-mum-1", "severity", 2, expected=["mum:2"]).status_code == 200


# ---- notes ---------------------------------------------------------------------
def test_notes_are_added_and_kept(create, client):
    create()
    a = client.post("/v1/reports/R-mum-1/notes", json={"text": "two people trapped"}, headers=KEY)
    b = client.post("/v1/reports/R-mum-1/notes", json={"text": "fire brigade on site"}, headers=KEY)
    assert a.status_code == b.status_code == 201
    assert a.json()["event_id"] == "mum:2" and b.json()["event_id"] == "mum:3"
    assert client.get("/v1/reports/R-mum-1").json()["state"]["notes"] == [
        {"id": "mum:2", "text": "two people trapped"}, {"id": "mum:3", "text": "fire brigade on site"}]


@pytest.mark.parametrize("text", ["", "x" * 2001])
def test_bad_notes_are_rejected(create, client, text):
    create()
    assert client.post("/v1/reports/R-mum-1/notes", json={"text": text}, headers=KEY).status_code == 422


# ---- listing -------------------------------------------------------------------
def test_list_is_newest_first_and_pages(create, client):
    for i in range(3):
        create(title=f"r{i}")
    ids = lambda **q: [r["report_id"] for r in client.get("/v1/reports", params=q).json()]
    assert ids() == ["R-mum-3", "R-mum-2", "R-mum-1"]
    assert ids(limit=2) == ["R-mum-3", "R-mum-2"]
    assert ids(limit=2, cursor=2) == ["R-mum-1"]
    assert ids(limit=0) == ["R-mum-3"]  # clamped to 1
    assert client.get("/v1/reports", params={"limit": -1}).status_code == 422
    assert client.get("/v1/reports", params={"cursor": -1}).status_code == 422


def test_list_order_follows_create_hlc_across_origins(create, client, mk_event):
    create()  # R-mum-1, hlc ~ now
    newer = mk_event("pun", 1, "CREATE", "R-pun-1", value=create_value("newer"), hlc=(9_000_000_000_000, 0))
    older = mk_event("pun", 7, "CREATE", "R-pun-7", value=create_value("older"), hlc=(1_000, 0))
    ingest.ingest([older, newer], source="pun")
    ids = [r["report_id"] for r in client.get("/v1/reports").json()]
    # per-origin seq alone would put R-pun-7 first; the CREATE's hlc puts it last
    assert ids == ["R-pun-1", "R-mum-1", "R-pun-7"]


def test_incomplete_reports_are_hidden_from_the_list(client, mk_event):
    orphan = mk_event("pun", 10, "SET", "R-pun-9", field="severity", value=4)
    ingest.ingest([orphan], source="pun")
    assert client.get("/v1/reports").json() == []
    assert client.get("/v1/reports/R-pun-9").json()["incomplete"] is True
    assert client.get("/v1/status").json()["incomplete_reports"] == 1
    ingest.ingest([mk_event("pun", 9, "CREATE", "R-pun-9", value=create_value())], source="pun")
    assert [r["report_id"] for r in client.get("/v1/reports").json()] == ["R-pun-9"]
    assert client.get("/v1/status").json()["incomplete_reports"] == 0


# ---- concurrent edits are detected (M1b in process) ------------------------------
def test_concurrent_edits_from_two_regions_are_a_conflict(create, client, mk_event):
    create()
    assert patch(client, "R-mum-1", "severity", 5).status_code == 200  # mum:2 supersedes mum:1
    remote = mk_event("pun", 1, "SET", "R-mum-1", field="severity", value=4, supersedes=["mum:1"])
    ingest.ingest([remote], source="pun")  # pun never saw mum:2
    rep = client.get("/v1/reports/R-mum-1").json()
    assert [c["field"] for c in rep["conflicts"]] == ["severity"]
    assert sorted(rep["conflicts"][0]["values"]) == [4, 5]
    assert sorted(rep["live"]["severity"]) == ["mum:2", "pun:1"]


# ---- replication badge ---------------------------------------------------------------
def test_replication_badge_follows_what_peers_confirm(create, client, monkeypatch):
    import dataclasses
    monkeypatch.setattr(config, "settings", dataclasses.replace(
        config.settings, peers={"pun": "http://127.0.0.1:9", "del": "http://127.0.0.1:9"}))
    badge = lambda: client.get("/v1/reports/R-mum-1").json()["consistency"]["replication"]
    create()
    assert badge() == "LOCAL_ONLY"
    state.save_peer_view("pun", {"mum": 1})
    assert badge() == "PARTIAL"
    state.save_peer_view("del", {"mum": 1})
    assert badge() == "REPLICATED"
    patch(client, "R-mum-1", "severity", 4)  # new event mum:2 that nobody has yet
    assert badge() == "LOCAL_ONLY"


def test_consistency_block_names_who_confirmed_and_who_is_awaited(create, client, monkeypatch):
    import dataclasses
    monkeypatch.setattr(config, "settings", dataclasses.replace(
        config.settings, peers={"pun": "http://127.0.0.1:9", "del": "http://127.0.0.1:9"}))
    create()
    cons = lambda: client.get("/v1/reports/R-mum-1").json()["consistency"]
    assert (cons()["confirmed_by"], cons()["awaiting"]) == ([], ["del", "pun"])  # sorted, stable
    state.save_peer_view("pun", {"mum": 1})
    assert (cons()["confirmed_by"], cons()["awaiting"]) == (["pun"], ["del"])
    state.save_peer_view("del", {"mum": 1})
    assert (cons()["confirmed_by"], cons()["awaiting"]) == (["del", "pun"], [])


def test_stale_possible_tracks_peer_reachability(create, client):
    create()
    stale = lambda: client.get("/v1/reports/R-mum-1").json()["consistency"]["stale_possible"]
    assert stale() is True        # pun never reached: a newer edit may exist there
    state.mark_ok("pun")
    assert stale() is False       # heard from pun just now
    for _ in range(3):
        state.mark_error("pun", "timeout")
    assert stale() is True        # failing again: the view may be stale
    # the list endpoint reports the same thing per report
    assert client.get("/v1/reports").json()[0]["consistency"]["stale_possible"] is True


def test_a_region_with_no_peers_is_never_stale(create, client, monkeypatch):
    import dataclasses
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, peers={}))
    create()
    assert client.get("/v1/reports/R-mum-1").json()["consistency"] == {
        "replication": "LOCAL_ONLY", "confirmed_by": [], "awaiting": [], "stale_possible": False}


# ---- history (audit trail) ------------------------------------------------------------
def test_history_lists_every_event_in_hlc_order_with_supersedes(create, client, region, mk_event):
    create()
    patch(client, "R-mum-1", "severity", 5)
    client.post("/v1/reports/R-mum-1/notes", json={"text": "two trapped"}, headers=KEY)
    remote = mk_event("pun", 1, "SET", "R-mum-1", field="status", value="assigned",
                      supersedes=["mum:1"], hlc=(1, 0))  # an old remote event sorts first
    ingest.ingest([remote], source="pun")
    h = client.get("/v1/reports/R-mum-1/history")
    assert h.status_code == 200
    events = h.json()
    assert [e["event_id"] for e in events] == ["pun:1", "mum:1", "mum:2", "mum:3"]
    by_id = {e["event_id"]: e for e in events}
    assert by_id["mum:2"]["supersedes"] == ["mum:1"] and by_id["pun:1"]["supersedes"] == ["mum:1"]
    assert by_id["mum:2"]["actor"] == "officer-mum-1" and by_id["mum:3"]["type"] == "NOTE"
    clocks = [(e["hlc"][0], e["hlc"][1], e["origin"], e["seq"]) for e in events]
    assert clocks == sorted(clocks)
    for e in events:  # exactly what was stored: every hash still verifies
        assert e["hash"] == canonical.event_hash(e)
    assert events == sorted(stored_events(region), key=lambda e: (e["hlc"][0], e["hlc"][1], e["origin"], e["seq"]))


def test_history_of_an_unknown_report_is_404(client):
    assert client.get("/v1/reports/R-nope-1/history").status_code == 404


def test_history_is_available_for_a_report_still_waiting_for_its_create(client, mk_event):
    ingest.ingest([mk_event("pun", 10, "SET", "R-pun-9", field="severity", value=4)], source="pun")
    assert [e["event_id"] for e in client.get("/v1/reports/R-pun-9/history").json()] == ["pun:10"]


# ---- acceptance 3: a dead peer never affects writes ------------------------------------
def test_write_path_never_calls_a_peer(create, client, monkeypatch):
    real_send = httpx.Client.send  # the TestClient is an httpx.Client too: only block real hosts

    def guarded(self, request, *a, **k):
        if request.url.host != "testserver":
            raise AssertionError(f"the write path must never call a peer: {request.url}")
        return real_send(self, request, *a, **k)

    async def guarded_async(self, request, *a, **k):
        raise AssertionError(f"the write path must never call a peer: {request.url}")

    monkeypatch.setattr(httpx.Client, "send", guarded)
    monkeypatch.setattr(httpx.AsyncClient, "send", guarded_async)
    assert create().status_code == 201
    assert patch(client, "R-mum-1", "severity", 4).status_code == 200
    assert client.post("/v1/reports/R-mum-1/notes", json={"text": "x"}, headers=KEY).status_code == 201


def test_a_hung_peer_does_not_slow_or_fail_writes(region, monkeypatch):
    import dataclasses
    hung = socket.socket()  # accepts the TCP handshake, never answers HTTP
    hung.bind(("127.0.0.1", 0))
    hung.listen(8)
    try:
        monkeypatch.setattr(config, "settings", dataclasses.replace(
            config.settings, peers={"pun": f"http://127.0.0.1:{hung.getsockname()[1]}"}, http_timeout_s=3.0))
        with TestClient(app) as c:  # lifespan on: the pull loop is really trying (and hanging) on pun
            durations = []
            for i in range(10):
                t = time.perf_counter()
                r = c.post("/v1/reports", json={"title": f"r{i}", "category": "flood", "severity": 2}, headers=KEY)
                durations.append(time.perf_counter() - t)
                assert r.status_code == 201
            assert max(durations) < 1.0  # peer timeout is 3 s: nothing waited on it
            s = c.get("/v1/status").json()
            assert s["level"] == "ISOLATED" and s["peers"]["pun"]["reachable"] is False
            assert s["event_count"] == 10
    finally:
        hung.close()


def test_local_write_function_is_independent_of_the_http_layer(region):
    ev = writes.local_write(None, "CREATE", None, create_value("direct"), "officer-mum-1")
    assert ev["event_id"] == "mum:1" and ev["report_id"] == "R-mum-1"
    with pytest.raises(writes.NotFound):
        writes.local_write("R-nope-1", "NOTE", None, {"text": "x"}, "a")
    with pytest.raises(writes.StaleEdit) as e:
        writes.local_write("R-mum-1", "SET", "severity", 4, "a", expected=["mum:99"])
    assert e.value.live == ["mum:1"]
