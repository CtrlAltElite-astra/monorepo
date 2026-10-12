"""Contract v1.2 identity (lane C): claims.py, proven claims in the session, require_claim, is_revoked.

Pure Python with an injected clock and throw-away keys (the `world` fixture of test_authorization_unit.py);
the same flows with the real partner crypto and real processes are in test_claims_real.py.
"""
import dataclasses

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from region import config
from region.auth import Auth, require_key
from region.trust import adapter as adapter_mod
from region.trust import claims, true_hook
from tests.convergence.test_authorization_unit import ACTOR_A, CRED_A, CRED_B, expect, world  # noqa: F401


@pytest.fixture
def auth_mode(monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, auth_enabled=True))


@pytest.fixture
def key_mode(monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, auth_enabled=False))


@pytest.fixture
def installed(world):
    previous = adapter_mod.get_adapter()
    adapter_mod.set_adapter(world["adapter"])
    yield world
    adapter_mod.set_adapter(previous)


# ---- subject_key: the rate limiter follows the verified person ----------------------------------------------
def test_subject_key_is_the_credential_when_signed_in(auth_mode):
    assert claims.subject_key(Auth(ACTOR_A, "officer", "online", CRED_A)) == CRED_A


def test_subject_key_is_the_api_key_actor_otherwise(key_mode):
    # LocalKeyAdapter fills credential_id with the actor too; API-key mode must still key on the actor
    assert claims.subject_key(Auth("officer-mum-1", "officer", "online", "officer-mum-1")) == "officer-mum-1"
    assert claims.subject_key(Auth("officer-mum-1", "officer")) == "officer-mum-1"


def test_two_devices_of_one_person_share_one_key_and_two_people_do_not(auth_mode):
    phone, laptop = Auth(ACTOR_A, "officer", "online", CRED_A), Auth(ACTOR_A, "officer", "cached", CRED_A)
    assert claims.subject_key(phone) == claims.subject_key(laptop)
    assert claims.subject_key(phone) != claims.subject_key(Auth(ACTOR_A, "officer", "online", CRED_B))


# ---- has_claim / require_claim -------------------------------------------------------------------------------
def test_has_claim_reads_only_proven_claims(auth_mode):
    a = Auth(ACTOR_A, "officer", "online", CRED_A, ("age_over_18",))
    assert claims.has_claim(a, "age_over_18") is True
    assert claims.has_claim(a, "member") is False
    assert claims.has_claim(a, "admin") is False                 # not an attribute at all
    assert claims.has_claim(None, "age_over_18") is False


def test_has_claim_is_always_false_in_api_key_mode(key_mode):
    assert claims.has_claim(Auth("officer-mum-1", "officer", "online", None, ("age_over_18",)), "age_over_18") is False


def test_require_claim_passes_with_the_claim_and_raises_the_contract_403_without(auth_mode):
    claims.require_claim(Auth(ACTOR_A, "officer", "online", CRED_A, ("age_over_18",)), "age_over_18")
    with pytest.raises(claims.ClaimRequired) as e:
        claims.require_claim(Auth(ACTOR_A, "officer", "online", CRED_A, ("member",)), "age_over_18")
    assert e.value.status_code == 403
    assert e.value.detail == {"error": "claim_required", "claim": "age_over_18"}
    with pytest.raises(ValueError):
        claims.require_claim(Auth(ACTOR_A, "officer"), "not_a_claim")


def _restricted_app(via_install=False):
    app = FastAPI()
    if via_install:
        true_hook.install(app)      # API-key mode: registers only the 403 renderer
    else:
        app.add_exception_handler(claims.ClaimRequired, claims.claim_required_handler)

    @app.post("/buy")
    def buy(auth: Auth = Depends(require_key)):
        claims.require_claim(auth, "age_over_18")
        return {"ok": True}
    return app


def test_through_http_a_session_with_age_over_18_buys_and_one_without_gets_exactly_the_contract_body(installed, auth_mode):
    world = installed
    world["snapshot"]()
    client = TestClient(_restricted_app())
    ok = client.post("/buy", headers={"Authorization": "Bearer " + world["mint"](clm=["age_over_18"], dsc=["age_over_18"])})
    assert ok.status_code == 200
    no = client.post("/buy", headers={"Authorization": "Bearer " + world["mint"](clm=[], dsc=["age_over_18"])})
    assert no.status_code == 403 and no.json() == {"error": "claim_required", "claim": "age_over_18"}
    other = client.post("/buy", headers={"Authorization": "Bearer " + world["mint"](clm=["member"], dsc=["member"])})
    assert other.status_code == 403 and other.json()["claim"] == "age_over_18"
    plain = client.post("/buy", headers={"Authorization": "Bearer " + world["mint"]()})   # signed in, nothing proven
    assert plain.status_code == 403


def test_api_key_mode_can_never_buy_a_restricted_item(key_mode, monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(
        config.settings, api_keys={"dev-officer": ("officer-mum-1", "officer")}))
    r = TestClient(_restricted_app(via_install=True)).post("/buy", headers={"X-API-Key": "dev-officer"})
    assert r.status_code == 403 and r.json() == {"error": "claim_required", "claim": "age_over_18"}


# ---- the session records proven claims and nothing else ------------------------------------------------------
def test_session_carries_proven_and_disclosed_claims(world):
    s = world["verifier"].verify(world["mint"](clm=["age_over_18"], dsc=["age_over_18"]))
    assert (s.claims, s.disclosed) == (("age_over_18",), ("age_over_18",))
    s = world["verifier"].verify(world["mint"](clm=[], dsc=["age_over_18"]))            # disclosed, but false
    assert (s.claims, s.disclosed) == ((), ("age_over_18",))
    s = world["verifier"].verify(world["mint"]())                                        # older tokens: none
    assert (s.claims, s.disclosed) == ((), ())


@pytest.mark.parametrize("over", [
    {"clm": ["age_over_18"], "dsc": []},              # proven without being disclosed
    {"clm": ["date_of_birth"], "dsc": ["date_of_birth"]},  # not a disclosable attribute
    {"clm": "age_over_18", "dsc": "age_over_18"},     # not a list
    {"clm": [], "dsc": ["member", "member"]},          # duplicates
])
def test_malformed_claim_lists_are_rejected(world, over):
    expect(world["verifier"], world["mint"](**over), "malformed_session")


def test_claims_flow_from_the_session_into_the_decision(installed):
    world = installed
    world["snapshot"]()
    d = world["adapter"].verify_actor(world["mint"](clm=["member"], dsc=["member"]), "set")
    assert d.allowed and d.claims == ("member",)


def test_auth_me_reports_claims_and_disclosed(installed, auth_mode):
    from region.api import routes_auth
    world = installed
    world["snapshot"]()
    app = FastAPI()
    app.include_router(routes_auth.router)
    me = TestClient(app).get("/v1/auth/me", headers={"Authorization": "Bearer " + world["mint"](clm=["age_over_18"],
                                                                                                    dsc=["age_over_18"])})
    assert me.status_code == 200
    body = me.json()
    assert body["claims"] == ["age_over_18"] and body["disclosed"] == ["age_over_18"]
    assert "member" not in body["disclosed"]


# ---- is_revoked ------------------------------------------------------------------------------------------------
def test_is_revoked_is_false_without_status_data(key_mode):
    assert claims.is_revoked(CRED_A) is False         # LocalKeyAdapter holds no status list
    assert claims.is_revoked("") is False


def test_is_revoked_follows_the_signed_snapshot_on_the_next_read(installed):
    world = installed
    assert claims.is_revoked(CRED_A) is False         # no snapshot yet: not revoked (and not an approval)
    world["snapshot"](statuses={CRED_A: "a", CRED_B: "a"}, ver=1)
    assert claims.is_revoked(CRED_A) is False
    world["snapshot"](statuses={CRED_A: "r", CRED_B: "a"}, ver=2)
    assert claims.is_revoked(CRED_A) is True          # no caching window beyond the file the sidecar writes
    assert claims.is_revoked(CRED_B) is False
    assert claims.is_revoked("cred_" + "f" * 24) is False   # unknown id: no status, not "revoked"


def test_is_revoked_ignores_a_forged_or_rolled_back_snapshot(installed):
    from tests.convergence.test_authorization_unit import new_key
    world = installed
    world["snapshot"](statuses={CRED_A: "r"}, ver=5)
    assert claims.is_revoked(CRED_A) is True
    world["snapshot"](statuses={CRED_A: "a"}, ver=4)  # rollback that would hide the revocation
    assert claims.is_revoked(CRED_A) is True
    stranger, _ = new_key()
    world["snapshot"](statuses={CRED_A: "a"}, ver=9, key=stranger)
    assert claims.is_revoked(CRED_A) is True
