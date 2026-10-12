"""Contract v1.2 identity with REAL partner crypto and REAL processes (lane C).

C1  prove one attribute at sign-in; the region records only that claim (no name, no date of birth)
C3  sign in at Mumbai, crash Mumbai, keep writing at Pune with the same session: no new sign-in and no issuer
    (the issuer AND Pune's auth sidecar are down during the write, so neither can have been asked)
    is_revoked turns true within the documented window, for exactly the id that events carry as credential_id
"""
import json
import os
import time

import httpx

from region.trust import adapter as adapter_mod
from region.trust import claims
from region.trust.true_adapter import TrueSessionAdapter
from region.trust.true_session import SessionKeyRegistry, SessionVerifier
from region.trust.true_status import StatusStore
from tests.convergence.conftest import REGION_URL, bearer, until

C = httpx.Client(timeout=10)


def member(stack, make_wallet, name, **attributes):
    body = {"name": name, "role": "officer", **({"attributes": attributes} if attributes else {})}
    status, m = stack.admin("POST", "/v1/syncshield/members", body)
    assert status == 201, m
    return make_wallet(m, name.lower().replace(" ", "-")), m


def login(wallet, region="mum", **flags):
    until(lambda: httpx.get(REGION_URL[region] + "/v1/auth/status", timeout=2).json().get("status"), timeout=15)
    out = wallet.login(REGION_URL[region], **flags)
    assert out["ok"], out
    return out


def me(token, region="mum"):
    r = C.get(REGION_URL[region] + "/v1/auth/me", headers=bearer(token))
    assert r.status_code == 200, r.text
    return r.json()


def cart(token, region="mum", **kw):
    return C.post(REGION_URL[region] + "/v1/reports", json={"title": "c", "category": "cart", "severity": 1, **kw},
                  headers=bearer(token))


def has_event(region, event_id):
    origin, seq = event_id.split(":")
    return httpx.get(REGION_URL[region] + "/v1/status", timeout=3).json()["heads"].get(origin, 0) >= int(seq)


# ---- C1 ---------------------------------------------------------------------------------------------------------
def test_proving_over_18_discloses_only_that_and_the_region_records_only_that(cluster, stack, make_wallet):
    w, m = member(stack, make_wallet, "Adult Shopper")
    held = w.holds()
    assert held["ok"] and held["claims"]["age_over_18"] is True and held["claims"]["member"] is True   # holds both

    out = login(w, claim="age_over_18")
    assert (out["claims"], out["disclosed"]) == (["age_over_18"], ["age_over_18"])
    who = me(out["token"])
    assert who["claims"] == ["age_over_18"] and who["disclosed"] == ["age_over_18"]
    assert "member" not in who["disclosed"]                                    # held, but never sent
    assert who["actor_id"] == m["actor_id"] and who["credential_id"] == w.credential_id
    assert not {"name", "dob", "date_of_birth", "address"} & set(who)          # nothing personal in the session

    plain = me(login(w)["token"])                                              # an ordinary sign-in proves nothing
    assert plain["claims"] == [] and plain["disclosed"] == []


def test_a_member_under_18_signs_in_but_proves_nothing(cluster, stack, make_wallet):
    w, _ = member(stack, make_wallet, "Young Shopper", age_over_18=False)
    out = login(w, claim="age_over_18")
    assert (out["claims"], out["disclosed"]) == ([], ["age_over_18"])
    assert me(out["token"])["claims"] == []                                    # require_claim would answer 403


def test_disclosing_an_attribute_that_was_not_asked_for_is_refused(cluster, stack, make_wallet):
    w, _ = member(stack, make_wallet, "Oversharer")
    out = w.login(REGION_URL["mum"], claims="actor_id,syncshield_member,syncshield_role,credential_id,member")
    assert out["ok"] is False and out["status"] == 401 and out["error"]["code"] == "wrong_claims"


def test_a_flipped_attribute_is_caught_by_the_real_verifier(cluster, stack, make_wallet):
    w, _ = member(stack, make_wallet, "Would Be Adult", age_over_18=False)
    out = w.login(REGION_URL["mum"], claim="age_over_18", tamper="age_over_18")   # value changed after signing
    assert out["ok"] is False and out["status"] == 401


# ---- C3 ---------------------------------------------------------------------------------------------------------
def test_signed_in_session_and_its_claim_survive_a_region_crash_without_issuer_or_new_sign_in(cluster, stack, make_wallet):
    w, m = member(stack, make_wallet, "Crash Shopper")
    token = login(w, "mum", claim="age_over_18")["token"]
    first = cart(token, title="before crash", op_id="op-c3-1", report_id="R-c3c3c3c3")
    assert first.status_code == 201, first.text
    assert first.json()["event"]["credential_id"] == w.credential_id
    until(lambda: has_event("pun", first.json()["event_id"]), timeout=10)

    cluster.kill("mum")
    stack.stop("issuer")
    stack.stop("auth-pun")
    try:
        note = C.post(REGION_URL["pun"] + "/v1/reports/R-c3c3c3c3/notes", json={"text": "after crash", "op_id": "op-c3-2"},
                      headers=bearer(token))
        assert note.status_code == 201, note.text
        ev = note.json()["event"]
        assert ev["actor"] == m["actor_id"] and ev["credential_id"] == w.credential_id
        who = me(token, "pun")
        assert who["claims"] == ["age_over_18"] and who["region"] == "pun"     # the proven claim came along
        assert w.login(REGION_URL["pun"])["ok"] is False                        # proof: pun could not sign anyone in now
    finally:
        stack.start_issuer()
        stack.start_sidecar("pun")
        cluster.restart("mum")
    until(lambda: cluster.status("mum")["event_digest"] == cluster.status("pun")["event_digest"], timeout=15)


# ---- revocation --------------------------------------------------------------------------------------------------
def test_is_revoked_turns_true_within_the_window_for_the_id_events_carry(cluster, stack, make_wallet):
    w, m = member(stack, make_wallet, "Revoked Shopper")
    token = login(w)["token"]
    ev = cart(token, title="before revoke").json()["event"]
    assert ev["credential_id"] == w.credential_id                              # what lane B's rule will look up

    pinned = json.loads(stack.pinned_issuer.read_text())
    status = StatusStore(str(stack.data / "status" / "mum.jws"), pinned["issuerId"], pinned["publicJwk"])
    previous = adapter_mod.get_adapter()
    adapter_mod.set_adapter(TrueSessionAdapter(SessionVerifier(SessionKeyRegistry(str(stack.session_keys))), status))
    try:
        assert claims.is_revoked(ev["credential_id"]) is False
        code, body = stack.admin("POST", f"/v1/syncshield/members/{m['actor_id']}/revoke", {"reason": "test"})
        assert code == 200 and body["revokedCredentials"] == 1
        t0 = time.time()
        refresh_s = int(os.environ.get("STATUS_REFRESH_MS", "5000")) / 1000
        window = refresh_s + 2.0                                                # one refresh + fetch/verify slack
        until(lambda: claims.is_revoked(ev["credential_id"]), timeout=window + 3)
        assert time.time() - t0 <= window, f"seen after {time.time() - t0:.1f} s, window {window:.1f} s"
    finally:
        adapter_mod.set_adapter(previous)
    until(lambda: cart(token).status_code == 401, timeout=10)                  # and the region itself agrees
