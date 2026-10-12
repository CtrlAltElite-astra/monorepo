"""Revocation, issuer outage, regional failover with the same session, and sidecar loss. REAL processes."""
import json
import time

import httpx
import pytest

from tests.convergence.conftest import REGION_URL, bearer, until

C = httpx.Client(timeout=10)


def member(stack, make_wallet, name, role="officer"):
    m = stack.admin("POST", "/v1/syncshield/members", {"name": name, "role": role})[1]
    return make_wallet(m, name.lower().replace(" ", "-")), m


def login(wallet, region="mum"):
    until(lambda: httpx.get(REGION_URL[region] + "/v1/auth/status", timeout=2).json().get("status"), timeout=15)
    out = wallet.login(REGION_URL[region])
    assert out["ok"], out
    return out["token"]


def cart(token, region="mum", **kw):
    body = {"title": "c", "category": "cart", "severity": 1, **kw}
    return C.post(REGION_URL[region] + "/v1/reports", json=body, headers=bearer(token))


def has_event(region, event_id):
    origin, seq = event_id.split(":")
    return cluster_status(region)["heads"].get(origin, 0) >= int(seq)


def cluster_status(region):
    return httpx.get(REGION_URL[region] + "/v1/status", timeout=3).json()


def test_revoked_credential_is_denied_everywhere_after_refresh(cluster, stack, make_wallet):
    w, m = member(stack, make_wallet, "Revoke Me")
    tok_mum, tok_pun = login(w, "mum"), login(w, "pun")
    assert cart(tok_mum).status_code == 201
    assert cart(tok_pun, "pun").status_code == 201

    status, body = stack.admin("POST", f"/v1/syncshield/members/{m['actor_id']}/revoke", {"reason": "left"})
    assert status == 200 and body["revokedCredentials"] == 1

    # each region's sidecar fetches the new signed snapshot; the EXISTING sessions stop working
    def denied(token, region):
        r = cart(token, region)
        return r.status_code == 401 and r.json()["detail"]["code"] == "credential_revoked"
    until(lambda: denied(tok_mum, "mum"), timeout=10)
    until(lambda: denied(tok_pun, "pun"), timeout=10)

    # and renewal with the stored credential is refused too
    again = w.login(REGION_URL["mum"])
    assert again["ok"] is False and again["status"] == 403 and again["error"]["code"] == "credential_revoked"
    # a revoked member can no longer be given a new claim code
    assert stack.admin("POST", "/v1/syncshield/claim-codes", {"actorId": m["actor_id"]})[0] == 409


def test_issuer_outage_does_not_stop_valid_sessions_or_new_logins(cluster, stack, make_wallet):
    w, _ = member(stack, make_wallet, "Outage Officer")
    token = login(w)
    stack.stop("issuer")
    try:
        assert cart(token).status_code == 201                      # verified locally, status from cache
        me = C.get(REGION_URL["mum"] + "/v1/auth/me", headers=bearer(token)).json()
        assert me["auth_mode"] in ("online", "cached")             # honest label, from the status AGE
        assert me["status_age_s"] is not None
        # a brand-new login also works while the issuer is down (cached status within 10 minutes)
        assert w.login(REGION_URL["pun"])["ok"]
    finally:
        stack.start_issuer()


def test_same_session_continues_at_the_other_region_after_the_first_dies(cluster, stack, make_wallet):
    w, m = member(stack, make_wallet, "Failover Officer")
    token = login(w, "mum")
    created = cart(token, title="survives", op_id="op-fo-1", report_id="R-f0f0f0f0")
    assert created.status_code == 201
    ev_id = created.json()["event_id"]
    until(lambda: has_event("pun", ev_id), timeout=10)             # replicated before the crash

    cluster.kill("mum")
    try:
        # SAME token, no new login, mum not contacted, issuer not contacted
        note = C.post(REGION_URL["pun"] + "/v1/reports/R-f0f0f0f0/notes", json={"text": "after crash", "op_id": "op-fo-2"},
                      headers=bearer(token))
        assert note.status_code == 201, note.text
        assert note.json()["event"]["actor"] == m["actor_id"]
        # the original create retried at the survivor: one operation, not two
        retry = cart(token, "pun", title="survives", op_id="op-fo-1", report_id="R-f0f0f0f0")
        assert retry.status_code == 200 and retry.headers.get("idempotent-replay") == "true"
        view = C.get(REGION_URL["pun"] + "/v1/reports/R-f0f0f0f0", headers=bearer(token)).json()
        assert len(view["state"]["notes"]) == 1
        # ownership came with the replicated, signed create event
        assert C.get(REGION_URL["pun"] + "/v1/reports/R-f0f0f0f0/history", headers=bearer(token)).status_code == 200
    finally:
        cluster.restart("mum")
    until(lambda: cluster_status("mum")["event_digest"] == cluster_status("pun")["event_digest"], timeout=10)


def test_unreplicated_signed_events_are_carried_by_a_different_authenticated_user(cluster, stack, make_wallet):
    owner_w, _ = member(stack, make_wallet, "Carry Owner")
    carrier_w, _ = member(stack, make_wallet, "Carry Courier")
    admin_w, _ = member(stack, make_wallet, "Carry Admin", "admin")
    owner, carrier, admin = login(owner_w), login(carrier_w), login(admin_w)

    # cut the link both ways so the next writes exist ONLY on mum
    for a, b in (("mum", "pun"), ("pun", "mum")):
        r = C.post(REGION_URL[a] + f"/admin/links/{b}", json={"mode": "down"}, headers=bearer(admin))
        assert r.status_code == 200, r.text
    events = []
    for i in range(3):
        r = cart(owner, title=f"isolated {i}", report_id=f"R-0c0c0c0{i}", op_id=f"op-carry-{i}")
        assert r.status_code == 201
        events.append(r.json()["event"])
    assert all(C.get(REGION_URL["pun"] + f"/v1/reports/{e['report_id']}", headers=bearer(owner)).status_code == 404 for e in events)

    cluster.kill("mum")
    try:
        # a DIFFERENT member's session carries them: the origin's signature is what protects them
        res = C.post(REGION_URL["pun"] + "/v1/carry", json={"events": events}, headers=bearer(carrier))
        assert res.status_code == 200
        assert [x["result"] for x in res.json()["results"]] == ["new", "new", "new"]
        # the carried carts belong to their original owner, not to the carrier
        r = C.get(REGION_URL["pun"] + f"/v1/reports/{events[0]['report_id']}", headers=bearer(owner))
        assert r.status_code == 200
        assert C.get(REGION_URL["pun"] + f"/v1/reports/{events[0]['report_id']}", headers=bearer(carrier)).status_code == 404
        # a tampered auth provenance field breaks the hash/signature and is rejected, state unchanged
        forged = dict(events[0], role="admin", event_id="mum:999", seq=999)
        bad = C.post(REGION_URL["pun"] + "/v1/carry", json={"events": [dict(events[1], role="admin")]}, headers=bearer(carrier))
        assert bad.json()["results"][0]["result"] == "rejected"
        assert bad.json()["results"][0]["reason"] == "bad_hash"
    finally:
        cluster.restart("mum")
        for a, b in (("mum", "pun"), ("pun", "mum")):
            C.post(REGION_URL[a] + f"/admin/links/{b}", json={"mode": "up"}, headers=bearer(admin))


def test_history_of_a_revoked_member_still_replicates(cluster, stack, make_wallet):
    w, m = member(stack, make_wallet, "Historic Officer")
    token = login(w)
    r = cart(token, title="before revoke", report_id="R-0d0d0d0d")
    assert r.status_code == 201
    stack.admin("POST", f"/v1/syncshield/members/{m['actor_id']}/revoke", {"reason": "x"})
    until(lambda: cart(token).status_code == 401, timeout=10)
    # their earlier event is still valid history and still reaches the other region
    until(lambda: has_event("pun", r.json()["event_id"]), timeout=10)


def test_losing_a_sidecar_keeps_local_session_checks_and_other_regions_login_works(cluster, stack, make_wallet):
    w, _ = member(stack, make_wallet, "Sidecar Officer")
    token = login(w, "mum")
    stack.stop("auth-pun")
    try:
        # a session minted at mum is checked at pun by pinned keys and cached status only
        assert cart(token, "pun").status_code == 201
        # new login at pun cannot work (honest failure, no silent fallback to anything weaker)...
        down = httpx.post(REGION_URL["pun"] + "/v1/auth/challenge", json={}, timeout=5)
        assert down.status_code == 503 and down.json()["error"]["code"] == "auth_unavailable"
        # ...but the survivor can log the user in, and that session works at pun
        token2 = login(w, "mum")
        assert cart(token2, "pun").status_code == 201
    finally:
        stack.start_sidecar("pun")


def test_nothing_secret_leaks_into_events_or_responses(cluster, stack, make_wallet):
    w, m = member(stack, make_wallet, "Privacy Officer")
    out = w.login(REGION_URL["mum"])
    assert out["ok"]
    token = out["token"]
    r = cart(token, title="plain title", report_id="R-0e0e0e0e")
    assert r.status_code == 201
    history = C.get(REGION_URL["mum"] + "/v1/reports/R-0e0e0e0e/history", headers=bearer(token)).json()
    blob = json.dumps(history) + json.dumps(r.json())
    for secret in (token, "eyJ", "Privacy Officer", "synthetic demo member", "SS-act_", "presentation", "privateJwk"):
        assert secret not in blob, secret
    # the session response carries exactly the contract fields
    assert set(out) <= {"ok", "status", "token", "expires_at", "actor_id", "role", "credential_id", "auth_mode",
                        "status_ver", "status_age_s", "region", "challenge_id"}
