"""Real end-to-end flows: TRUE credential -> regional session -> authorized SyncShield writes.

Real partner crypto (collaboration/true), real issuer and sidecar processes, real FastAPI region processes.
"""
import httpx
import pytest

from tests.convergence.conftest import REGION_URL, bearer, until

C = httpx.Client(timeout=10)


def create_cart(token, region="mum", title="Cart", op_id=None, report_id=None, **extra):
    body = {"title": title, "category": "cart", "severity": 1, **extra}
    if op_id:
        body["op_id"] = op_id
    if report_id:
        body["report_id"] = report_id
    return C.post(REGION_URL[region] + "/v1/reports", json=body, headers=bearer(token))


@pytest.fixture(scope="module")
def alice(make_wallet, members):
    return make_wallet(members["alice"], "alice")


@pytest.fixture(scope="module")
def bob(make_wallet, members):
    return make_wallet(members["bob"], "bob")


@pytest.fixture(scope="module")
def sup(make_wallet, members):
    return make_wallet(members["sup"], "sup")


@pytest.fixture(scope="module")
def admin(make_wallet, members):
    return make_wallet(members["admin"], "admin")


def login(wallet, region="mum"):
    until(lambda: httpx.get(REGION_URL[region] + "/v1/auth/status", timeout=2).json().get("status"), timeout=15)
    out = wallet.login(REGION_URL[region])
    assert out["ok"], out
    return out


def test_member_login_and_first_authorized_write(cluster, alice):
    out = login(alice)
    assert out["role"] == "officer" and out["actor_id"] == alice.actor_id
    assert out["auth_mode"] in ("online", "cached")
    # nothing personal in the session response
    assert set(out) >= {"token", "expires_at", "actor_id", "role", "credential_id", "auth_mode"}

    r = create_cart(out["token"], title="Alice cart")
    assert r.status_code == 201, r.text
    ev = r.json()["event"]
    assert ev["actor"] == alice.actor_id                       # from the session, never the body
    assert ev["owner"] == alice.actor_id and ev["role"] == "officer"
    assert ev["credential_id"] == alice.credential_id and ev["auth_mode"] in ("online", "cached")
    assert ev["sig"]                                           # origin-signed (Ed25519), separate from TRUE's ES256

    me = C.get(REGION_URL["mum"] + "/v1/auth/me", headers=bearer(out["token"])).json()
    assert me["actor_id"] == alice.actor_id and me["role"] == "officer" and me["credential_id"] == alice.credential_id


def test_legacy_api_key_cannot_bypass_the_gate(cluster, alice):
    h = {"X-API-Key": "dev-officer"}
    body = {"title": "sneaky", "category": "cart", "severity": 1}
    assert C.post(REGION_URL["mum"] + "/v1/reports", json=body, headers=h).status_code == 401
    assert C.get(REGION_URL["mum"] + "/v1/reports", headers=h).status_code == 401
    assert C.get(REGION_URL["mum"] + "/admin/links", headers=h).status_code == 401
    assert C.post(REGION_URL["mum"] + "/v1/carry", json={"events": []}, headers=h).status_code == 401
    # even a valid session plus a legacy key: the key is ignored, the session decides
    out = login(alice)
    r = C.post(REGION_URL["mum"] + "/v1/reports", json=body, headers={**bearer(out["token"]), **h})
    assert r.json()["event"]["actor"] == alice.actor_id


def test_unauthorized_writes_change_nothing(cluster, alice):
    before = cluster.status("mum")
    bad = [
        {},                                                     # no credential
        {"Authorization": "Bearer not-a-token"},
        {"Authorization": "Basic YWxpY2U6cHc="},
    ]
    for h in bad:
        r = C.post(REGION_URL["mum"] + "/v1/reports", json={"title": "x", "category": "cart", "severity": 1}, headers=h)
        assert r.status_code == 401
    after = cluster.status("mum")
    assert (after["event_count"], after["heads"]) == (before["event_count"], before["heads"])


def test_challenge_cannot_be_replayed_and_tamper_is_denied(cluster, alice):
    ok = alice.login(REGION_URL["mum"], keep="1")
    assert ok["ok"]
    # the same presentation against the same (already consumed) challenge
    r = C.post(REGION_URL["mum"] + "/v1/auth/session",
               json={"challenge_id": ok["challenge_id"], "presentation": ok["presentation"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "challenge_consumed"
    # a changed disclosure value (role -> admin) is denied by the partner's verification
    bad = alice.login(REGION_URL["mum"], tamper="syncshield_role")
    assert bad["ok"] is False and bad["status"] == 401 and "token" not in bad


def test_officer_cannot_see_or_edit_another_members_cart(cluster, alice, bob):
    a, b = login(alice), login(bob)
    cart = create_cart(a["token"], title="private").json()["report"]["report_id"]
    listed = [r["report_id"] for r in C.get(REGION_URL["mum"] + "/v1/reports", headers=bearer(b["token"])).json()]
    assert cart not in listed
    assert C.get(REGION_URL["mum"] + f"/v1/reports/{cart}", headers=bearer(b["token"])).status_code == 404
    assert C.get(REGION_URL["mum"] + f"/v1/reports/{cart}/history", headers=bearer(b["token"])).status_code == 404
    r = C.patch(REGION_URL["mum"] + f"/v1/reports/{cart}", json={"field": "severity", "value": 5}, headers=bearer(b["token"]))
    assert r.status_code == 404
    n = C.post(REGION_URL["mum"] + f"/v1/reports/{cart}/notes", json={"text": "hi"}, headers=bearer(b["token"]))
    assert n.status_code == 404
    # the owner can still do everything
    assert C.patch(REGION_URL["mum"] + f"/v1/reports/{cart}", json={"field": "severity", "value": 2},
                   headers=bearer(a["token"])).status_code == 200


def test_supervisor_reads_all_but_may_not_edit_or_administer(cluster, alice, sup):
    a, s = login(alice), login(sup)
    cart = create_cart(a["token"], title="for review").json()["report"]["report_id"]
    assert C.get(REGION_URL["mum"] + f"/v1/reports/{cart}", headers=bearer(s["token"])).status_code == 200
    r = C.patch(REGION_URL["mum"] + f"/v1/reports/{cart}", json={"field": "severity", "value": 4}, headers=bearer(s["token"]))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "not_your_cart"
    assert C.get(REGION_URL["mum"] + "/admin/links", headers=bearer(s["token"])).status_code == 403


def test_role_gates_on_resolve_and_admin(cluster, alice, admin):
    a, ad = login(alice), login(admin)
    cart = create_cart(a["token"], title="gates").json()["report"]["report_id"]
    r = C.post(REGION_URL["mum"] + f"/v1/reports/{cart}/resolve",
               json={"field": "status", "value": "closed", "candidates": [], "reason": "try"}, headers=bearer(a["token"]))
    assert r.status_code == 403                                  # officer cannot resolve
    assert C.get(REGION_URL["mum"] + "/admin/links", headers=bearer(a["token"])).status_code == 403
    assert C.get(REGION_URL["mum"] + "/admin/links", headers=bearer(ad["token"])).status_code == 200
    # an admin is not a cart writer
    w = create_cart(ad["token"], title="admin cart")
    assert w.status_code == 403


def test_idempotency_is_bound_to_the_actor(cluster, alice, bob):
    a, b = login(alice), login(bob)
    r1 = create_cart(a["token"], title="op cart", op_id="op-shared-1", report_id="R-0a0b0c0d")
    assert r1.status_code == 201
    # the same actor retrying (what failover does): one operation, 200 replay
    r2 = create_cart(a["token"], title="op cart", op_id="op-shared-1", report_id="R-0a0b0c0d")
    assert r2.status_code == 200 and r2.headers.get("idempotent-replay") == "true"
    assert r2.json()["event_id"] == r1.json()["event_id"]
    # another member reusing the op_id gets nothing of the original
    r3 = create_cart(b["token"], title="op cart", op_id="op-shared-1", report_id="R-0a0b0c0d")
    assert r3.status_code == 409 and r3.json() == {"error": "op_id_reuse"}
    # same actor, changed payload: still rejected
    r4 = create_cart(a["token"], title="different", op_id="op-shared-1", report_id="R-0a0b0c0d")
    assert r4.status_code == 409
