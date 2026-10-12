"""Write path v2 (owner: Akshit): op_id idempotency, client report ids, signed events in 2xx bodies,
observed plumbing, priority, resolve / review (PDF SyncShield v1.1 sections 5, 6.1, 6.7; tests T6, T7, T16, A9)."""
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from region import store
from region.api import writes
from region.sync import ingest
from region.trust import signing

from conftest import KEY, SUP, create_value

CART = "R-3f9a2c10"


def stored(db_path):
    con = sqlite3.connect(db_path)
    try:
        return [json.loads(r[0]) for r in con.execute("SELECT body FROM events ORDER BY seq")]
    finally:
        con.close()


def new_cart(client, **over):
    body = {"report_id": CART, "op_id": "op-create-1", "title": "Alice", "category": "cart", "severity": 1, **over}
    return client.post("/v1/reports", json=body, headers=KEY)


def put(client, field, value, **extra):
    return client.patch(f"/v1/reports/{CART}", json={"field": field, "value": value, **extra}, headers=KEY)


# ---- client report ids and the signed event in the body --------------------------------
def test_a_client_report_id_is_used_as_given_and_the_signed_event_is_in_the_body(client, region):
    r = new_cart(client)
    assert r.status_code == 201
    body = r.json()
    assert body["report"]["report_id"] == CART and body["event_id"] == "mum:1"
    ev = body["event"]
    assert ev["report_id"] == CART and ev["op_id"] == "op-create-1" and signing.verify("mum", ev["hash"], ev["sig"])
    assert ev == stored(region)[0]  # the very envelope that was stored and will replicate


@pytest.mark.parametrize("bad", ["R-mum-1", "r-3f9a2c10", "R-3F9A2C10", "R-3f9a2c1", "R-3f9a2c100", "cart-1", ""])
def test_a_malformed_client_report_id_is_422(client, region, bad):
    assert new_cart(client, report_id=bad).status_code == 422
    assert stored(region) == []


def test_an_existing_client_report_id_with_a_new_op_id_is_409_report_exists(client, region):
    assert new_cart(client).status_code == 201
    r = new_cart(client, op_id="a-different-op")
    assert r.status_code == 409 and r.json() == {"error": "report_exists"}
    assert len(stored(region)) == 1


def test_without_a_client_id_the_server_still_makes_one(client):
    r = client.post("/v1/reports", json={"title": "x", "category": "cart", "severity": 1}, headers=KEY)
    assert r.json()["report"]["report_id"] == "R-mum-1"


def test_cart_is_a_valid_category(client):
    assert new_cart(client).status_code == 201


# ---- op_id idempotency (T16) -------------------------------------------------------------
def test_the_same_create_twice_is_one_event_and_the_replay_is_flagged(client, region):
    first = new_cart(client)
    again = new_cart(client)
    assert first.status_code == 201 and "idempotent-replay" not in first.headers
    assert again.status_code == 200 and again.headers["idempotent-replay"] == "true"
    assert again.json()["event"] == first.json()["event"]
    assert len(stored(region)) == 1


def test_a_replayed_write_does_not_use_up_a_sequence_number(client, region):
    put_note = lambda: client.post(f"/v1/reports/{CART}/notes", json={"text": "item", "op_id": "op-n1"}, headers=KEY)
    new_cart(client)
    a, b = put_note(), put_note()
    assert (a.status_code, b.status_code) == (201, 200) and a.json()["event_id"] == b.json()["event_id"] == "mum:2"
    assert client.post(f"/v1/reports/{CART}/notes", json={"text": "next", "op_id": "op-n2"}, headers=KEY).json()["event_id"] == "mum:3"


def test_the_same_op_id_with_a_different_body_is_409_op_id_reuse(client, region):
    new_cart(client)
    assert put(client, "severity", 4, op_id="op-x").status_code == 200
    r = put(client, "severity", 5, op_id="op-x")
    assert r.status_code == 409 and r.json() == {"error": "op_id_reuse"}
    assert client.post("/v1/reports", json={"report_id": "R-0000aaaa", "op_id": "op-x", "title": "t", "category": "cart", "severity": 1},
                       headers=KEY).status_code == 409
    assert len(stored(region)) == 2


def test_the_idempotency_key_header_works_like_op_id(client, region):
    h = {**KEY, "Idempotency-Key": "hdr-1"}
    body = {"report_id": CART, "title": "Alice", "category": "cart", "severity": 1}
    a, b = client.post("/v1/reports", json=body, headers=h), client.post("/v1/reports", json=body, headers=h)
    assert (a.status_code, b.status_code) == (201, 200) and len(stored(region)) == 1
    assert stored(region)[0]["op_id"] == "hdr-1"


def test_a_replay_wins_over_a_stale_expected(client, region):
    new_cart(client)
    first = put(client, "severity", 4, op_id="op-sev", expected=["mum:1"])
    assert first.status_code == 200
    again = put(client, "severity", 4, op_id="op-sev", expected=["mum:1"])  # live is mum:2 now: would be 409 if not a replay
    assert again.status_code == 200 and again.headers["idempotent-replay"] == "true"
    assert len(stored(region)) == 2


def test_an_op_that_arrived_by_replication_or_carry_is_not_applied_a_second_time(client, region, mk_event):
    new_cart(client)
    remote = mk_event("pun", 1, "NOTE", CART, value={"text": "item"}, op_id="op-remote", signed=True)
    assert ingest.ingest([remote], source="pun")["new"] == 1
    r = client.post(f"/v1/reports/{CART}/notes", json={"text": "item", "op_id": "op-remote"}, headers=KEY)
    assert r.status_code == 200 and r.headers["idempotent-replay"] == "true" and r.json()["event_id"] == "pun:1"
    assert len(stored(region)) == 2  # mum:1 and pun:1, no mum:2
    clash = client.post(f"/v1/reports/{CART}/notes", json={"text": "different", "op_id": "op-remote"}, headers=KEY)
    assert clash.status_code == 409 and clash.json() == {"error": "op_id_reuse"}


def test_parallel_retries_of_one_op_id_store_exactly_one_event(client, region):
    new_cart(client)
    note = lambda _: client.post(f"/v1/reports/{CART}/notes", json={"text": "once", "op_id": "op-race"}, headers=KEY).status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = sorted(pool.map(note, range(16)))
    assert codes == [200] * 15 + [201]
    assert sum(1 for e in stored(region) if e["op_id"] == "op-race") == 1


# ---- observed plumbing (CLOSE_VS_NEW_INFO evidence) ------------------------------------------
def test_close_lists_the_notes_and_severity_it_saw_and_new_info_lists_the_status(client, region):
    new_cart(client)
    client.post(f"/v1/reports/{CART}/notes", json={"text": "a"}, headers=KEY)   # mum:2
    client.post(f"/v1/reports/{CART}/notes", json={"text": "b"}, headers=KEY)   # mum:3
    put(client, "severity", 4)                                                   # mum:4 (saw status mum:1)
    put(client, "status", "closed")                                              # mum:5
    client.post(f"/v1/reports/{CART}/notes", json={"text": "late"}, headers=KEY)  # mum:6 (saw the close)
    ev = {e["event_id"]: e for e in stored(region)}
    assert ev["mum:1"]["observed"] is None                       # CREATE
    assert ev["mum:2"]["observed"] == ["mum:1"]                  # a NOTE lists the live status
    assert ev["mum:4"]["observed"] == ["mum:1"]                  # a severity write lists the live status
    # the close lists every NOTE and every severity write it holds (the CREATE carries a severity too)
    assert ev["mum:5"]["observed"] == ["mum:1", "mum:2", "mum:3", "mum:4"]
    assert ev["mum:6"]["observed"] == ["mum:5"]                  # the late note saw the close


def test_other_writes_carry_no_observed_list(client, region):
    new_cart(client)
    put(client, "assigned_team", "Bhiwandi")
    put(client, "status", "assigned")
    assert [e["observed"] for e in stored(region)][1:] == [None, None]


# ---- priority (shop rules, PDF 4.2) -----------------------------------------------------------
def test_priority_follows_the_shop_rules(client, region):
    new_cart(client)                                         # P1: severity 1
    put(client, "severity", 2)                               # P1
    put(client, "severity", 4)                               # P0: Same-day or better
    put(client, "assigned_team", "Chakan")                   # P0: fulfilment centre assigned
    put(client, "status", "assigned")                        # P1: checking out
    put(client, "status", "resolved")                        # P0: paid
    client.post(f"/v1/reports/{CART}/notes", json={"text": "item"}, headers=KEY)   # P2: cart activity
    client.post("/v1/reports", json={"title": "Bob", "category": "cart", "severity": 5}, headers=KEY)  # P0 create
    assert [e["priority"] for e in stored(region)] == [1, 1, 0, 0, 1, 0, 2, 0]


# ---- push hand-off -------------------------------------------------------------------------------
def test_a_committed_event_is_handed_to_the_push_queue_through_the_loop(client):
    calls = []

    class FakeLoop:
        def call_soon_threadsafe(self, fn, *args):
            calls.append((fn.__name__, args[0]["event_id"]))

    writes.set_loop(FakeLoop())
    try:
        new_cart(client)
        put(client, "severity", 3)
        new_cart(client)  # a replay stores nothing and must not enqueue again
    finally:
        writes.set_loop(None)
    assert calls == [("enqueue", "mum:1"), ("enqueue", "mum:2")]


def test_a_broken_push_hand_off_never_fails_the_write(client):
    class BrokenLoop:
        def call_soon_threadsafe(self, *a):
            raise RuntimeError("loop closed")

    writes.set_loop(BrokenLoop())
    try:
        assert new_cart(client).status_code == 201
    finally:
        writes.set_loop(None)


# ---- resolve + review (T6, T7, A9) --------------------------------------------------------------------
def conflicted_status(client, mk_event):
    """mum:1 CREATE; mum:2 status=assigned; concurrent pun:1 status=closed (never saw mum:2)."""
    new_cart(client)
    put(client, "status", "assigned")
    ingest.ingest([mk_event("pun", 1, "SET", CART, field="status", value="closed", supersedes=["mum:1"], signed=True,
                            actor="agent-pun-2")], source="pun")


def test_close_vs_new_info_item_has_the_contract_shape_and_a_resolve_clears_it(client, mk_event):
    """Order placed here, a new item added on another node that never saw the close (contract 3.4)."""
    new_cart(client)
    put(client, "status", "closed")                                                   # mum:2, observed = [mum:1]
    note = mk_event("pun", 1, "NOTE", CART, value={"text": json.dumps({"kind": "ADD", "sku": "S", "name": "Gift"})},
                    observed=["mum:1"], signed=True, actor="officer-pun-1")           # never saw mum:2
    ingest.ingest([note], source="pun")
    (item,) = client.get("/v1/review").json()
    r = item["receipt"]
    assert (r["relationship"], r["rule"], r["flags"], r["needs_review"]) == ("CONCURRENT", "CLOSE_VS_NEW_INFO", ["CLOSE_VS_NEW_INFO"], True)
    assert [c["event_id"] for c in r["candidates"]] == ["mum:2", "pun:1"]             # the close first, then what it did not see
    assert r["candidates"][0]["value"] == "closed" and "Gift" in r["candidates"][1]["value"]
    assert all(set(c) == {"event_id", "value", "origin", "actor", "wall"} for c in r["candidates"])  # no kind / field keys
    live = client.get(f"/v1/reports/{CART}").json()["live"]["status"]
    body = {"field": "status", "value": "closed", "candidates": live, "reason": "ship with the new item"}
    assert client.post(f"/v1/reports/{CART}/resolve", json=body, headers=KEY).status_code == 403   # an officer may not
    assert client.post(f"/v1/reports/{CART}/resolve", json=body, headers=SUP).status_code == 201
    assert client.get("/v1/review").json() == []


def test_review_lists_the_open_conflict_with_a_full_receipt(client, mk_event):
    assert client.get("/v1/review").json() == []
    conflicted_status(client, mk_event)
    (item,) = client.get("/v1/review").json()
    assert (item["report_id"], item["title"], item["field"]) == (CART, "Alice", "status")
    r = item["receipt"]
    assert r["relationship"] == "CONCURRENT" and r["needs_review"] is True and r["flags"] == []
    assert {(c["event_id"], c["value"], c["origin"], c["actor"]) for c in r["candidates"]} == {
        ("mum:2", "assigned", "mum", "officer-mum-1"), ("pun:1", "closed", "pun", "agent-pun-2")}
    assert all(c["wall"] for c in r["candidates"]) and "provisional" in r and "policy_version" in r
    # the report itself carries the same receipt, identical shape for every consumer
    assert client.get(f"/v1/reports/{CART}").json()["conflicts"] == [r]


def test_a_supervisor_resolves_exactly_the_live_candidates(client, region, mk_event):
    conflicted_status(client, mk_event)
    r = client.post(f"/v1/reports/{CART}/resolve", headers=SUP,
                    json={"field": "status", "value": "closed", "candidates": ["pun:1", "mum:2"], "reason": "Field team confirmed"})
    assert r.status_code == 201
    ev = r.json()["event"]
    assert (ev["type"], ev["field"], ev["value"], ev["reason"]) == ("RESOLVE", "status", "closed", "Field team confirmed")
    assert sorted(ev["supersedes"]) == ["mum:2", "pun:1"] and ev["actor"] == "supervisor-mum-1" and ev["role"] == "supervisor"
    assert ev["priority"] == 0 and signing.verify("mum", ev["hash"], ev["sig"])
    rep = r.json()["report"]
    assert rep["conflicts"] == [] and rep["state"]["status"] == "closed" and rep["live"]["status"] == [ev["event_id"]]
    assert client.get("/v1/review").json() == []  # the review item is gone


def test_a_stale_resolve_is_409_with_the_live_ids_and_writes_nothing(client, region, mk_event):
    conflicted_status(client, mk_event)
    before = len(stored(region))
    r = client.post(f"/v1/reports/{CART}/resolve", headers=SUP,
                    json={"field": "status", "value": "closed", "candidates": ["mum:2"], "reason": "x"})
    assert r.status_code == 409 and r.json()["error"] == "stale_edit" and sorted(r.json()["live"]) == ["mum:2", "pun:1"]
    assert len(stored(region)) == before


def test_resolving_a_field_that_is_not_in_conflict_is_409_no_conflict(client, region):
    new_cart(client)
    r = client.post(f"/v1/reports/{CART}/resolve", headers=SUP,
                    json={"field": "status", "value": "closed", "candidates": ["mum:1"], "reason": "x"})
    assert r.status_code == 409 and r.json() == {"error": "no_conflict"} and len(stored(region)) == 1


def test_officers_and_admins_may_not_resolve_and_nothing_is_stored(client, region, mk_event):
    conflicted_status(client, mk_event)
    body = {"field": "status", "value": "closed", "candidates": ["mum:2", "pun:1"], "reason": "x"}
    before = len(stored(region))
    assert client.post(f"/v1/reports/{CART}/resolve", json=body, headers=KEY).status_code == 403   # A9
    assert client.post(f"/v1/reports/{CART}/resolve", json=body, headers={"X-API-Key": "dev-admin"}).status_code == 403
    assert client.post(f"/v1/reports/{CART}/resolve", json=body).status_code == 401
    assert len(stored(region)) == before


@pytest.mark.parametrize("patch_body", [
    {"field": "status", "value": "shipped"}, {"field": "nonsense", "value": 1}, {"field": "status", "value": "closed", "reason": ""},
    {"field": "status", "value": "closed", "reason": "x" * 501}, {"field": "status", "value": "closed", "candidates": []},
])
def test_bad_resolve_bodies_are_422(client, mk_event, patch_body):
    conflicted_status(client, mk_event)
    body = {"candidates": ["mum:2", "pun:1"], "reason": "ok", **patch_body}
    assert client.post(f"/v1/reports/{CART}/resolve", json=body, headers=SUP).status_code == 422


def test_resolving_an_unknown_report_is_404(client):
    r = client.post("/v1/reports/R-00000000/resolve", headers=SUP,
                    json={"field": "status", "value": "closed", "candidates": ["mum:1"], "reason": "x"})
    assert r.status_code == 404


def test_a_write_that_never_saw_the_resolution_reopens_review(client, mk_event):
    """T6: a resolution never hides an edit it did not see."""
    conflicted_status(client, mk_event)
    client.post(f"/v1/reports/{CART}/resolve", headers=SUP,
                json={"field": "status", "value": "closed", "candidates": ["mum:2", "pun:1"], "reason": "x"})
    # del never saw the RESOLVE: it supersedes only the old pun:1
    ingest.ingest([mk_event("del", 1, "SET", CART, field="status", value="assigned", supersedes=["pun:1"], signed=True)], source="del")
    (item,) = client.get("/v1/review").json()
    assert item["field"] == "status" and len(item["receipt"]["candidates"]) == 2


def test_resolve_is_idempotent_by_op_id(client, region, mk_event):
    conflicted_status(client, mk_event)
    body = {"field": "status", "value": "closed", "candidates": ["mum:2", "pun:1"], "reason": "x", "op_id": "op-res"}
    a = client.post(f"/v1/reports/{CART}/resolve", json=body, headers=SUP)
    b = client.post(f"/v1/reports/{CART}/resolve", json=body, headers=SUP)
    assert (a.status_code, b.status_code) == (201, 200) and b.headers["idempotent-replay"] == "true"
    assert sum(1 for e in stored(region) if e["type"] == "RESOLVE") == 1


def test_review_is_newest_first(client, mk_event):
    for i, cid in enumerate(("R-aaaaaaaa", "R-bbbbbbbb")):
        client.post("/v1/reports", json={"report_id": cid, "title": f"c{i}", "category": "cart", "severity": 1}, headers=KEY)
    for n, cid in enumerate(("R-aaaaaaaa", "R-bbbbbbbb")):
        client.patch(f"/v1/reports/{cid}", json={"field": "status", "value": "assigned"}, headers=KEY)
        mum_create = {"R-aaaaaaaa": "mum:1", "R-bbbbbbbb": "mum:2"}[cid]
        ingest.ingest([mk_event("pun", n + 1, "SET", cid, field="status", value="closed", supersedes=[mum_create], signed=True)], source="pun")
    assert [i["report_id"] for i in client.get("/v1/review").json()] == ["R-bbbbbbbb", "R-aaaaaaaa"]


# ---- history: signature status and the carried flag ---------------------------------------------------
def test_history_reports_signature_status_and_carried(client, region, mk_event):
    new_cart(client)
    ingest.ingest([mk_event("pun", 2, "NOTE", CART, value={"text": "signed"}, signed=True)], source="pun")
    with store.tx() as conn:  # a legacy unsigned row cannot arrive through ingest any more; plant it directly
        store.insert_event_if_absent(conn, mk_event("pun", 1, "NOTE", CART, value={"text": "unsigned"}, signed=False))
        store.mark_carried(conn, ["pun:2"])
    by_id = {e["event_id"]: e for e in client.get(f"/v1/reports/{CART}/history").json()}
    assert by_id["mum:1"]["sig_status"] == "verified" and by_id["mum:1"]["carried"] is False
    assert by_id["pun:1"]["sig_status"] == "unsigned"
    assert by_id["pun:2"]["sig_status"] == "verified" and by_id["pun:2"]["carried"] is True


def test_history_flags_a_body_that_no_longer_matches_its_hash_as_bad(client, region):
    new_cart(client)
    con = sqlite3.connect(region)
    body = json.loads(con.execute("SELECT body FROM events WHERE event_id='mum:1'").fetchone()[0])
    body["value"]["title"] = "Mallory"  # tamper with the stored copy
    con.execute("UPDATE events SET body=? WHERE event_id='mum:1'", (json.dumps(body),))
    con.commit()
    con.close()
    assert client.get(f"/v1/reports/{CART}/history").json()[0]["sig_status"] == "bad"
