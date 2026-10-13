"""The joining steps of the scaling plan (J4, J5, J6), through the REAL region app with an injected clock.

Pure Python (the `world` and `api` fixtures of test_authorization_unit.py): throw-away keys, no Node, no sleeping.
The same behaviour with the real partner crypto and real processes is exercised by the browser test.

    J4  an item whose note says {"requires": "age_over_18"} is stored only for a session that proved it (403 claim_required)
    J5  the per-shopper rate limit follows the verified person (credential id), not the device
    J6  /v1/carry refuses a validly signed event whose credential has since been revoked (revoked_credential)
"""
import json

import pytest

from region import store
from region.api import ratelimit
from region.engine import signing
from tests.convergence.test_authorization_unit import ACTOR_B, CRED_A, CRED_B, api, hdr, world  # noqa: F401

CART = {"title": "Alice", "category": "cart", "severity": 1}


def note(text: str) -> dict:
    return {"text": text}


def beer() -> str:
    return json.dumps({"kind": "ADD", "sku": "BEER-01", "name": "Craft beer sampler", "qty": 1, "price": 1299,
                       "user": "Alice", "requires": "age_over_18"})


def chai() -> str:
    return json.dumps({"kind": "ADD", "sku": "CHAI-01", "name": "Masala chai box", "qty": 1, "price": 349, "user": "Alice"})


def events() -> int:
    with store.read() as conn:
        return store.event_count(conn)


@pytest.fixture
def signed(api, monkeypatch, tmp_path):  # noqa: F811
    """The auth-mode app, with this region's event-signing key (carry refuses unsigned events)."""
    monkeypatch.setenv("SIGNING_KEYS_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("PUBLIC_KEYS_FILE", str(tmp_path / "pub.json"))
    signing.ensure_keys(["mum"])
    ratelimit.reset()
    yield api
    ratelimit.reset()


# ---- J4: the claim check on the write path --------------------------------------------------------------
def test_restricted_item_needs_the_proven_claim_and_nothing_is_stored_without_it(signed, world):  # noqa: F811
    rid = signed.post("/v1/reports", json=CART, headers=hdr(world)).json()["report"]["report_id"]
    before = events()

    plain = signed.post(f"/v1/reports/{rid}/notes", json=note(beer()), headers=hdr(world))                      # signed in, nothing proven
    assert plain.status_code == 403 and plain.json() == {"error": "claim_required", "claim": "age_over_18"}
    other = signed.post(f"/v1/reports/{rid}/notes", json=note(beer()), headers=hdr(world, clm=["member"], dsc=["member"]))
    assert other.status_code == 403 and other.json()["claim"] == "age_over_18"                                  # another claim is not this one
    assert events() == before                                                                                   # nothing stored

    proven = signed.post(f"/v1/reports/{rid}/notes", json=note(beer()),
                         headers=hdr(world, clm=["age_over_18"], dsc=["age_over_18"]))
    assert proven.status_code == 201 and events() == before + 1


def test_an_ordinary_item_needs_no_claim_and_an_unknown_claim_is_a_422(signed, world):  # noqa: F811
    rid = signed.post("/v1/reports", json=CART, headers=hdr(world)).json()["report"]["report_id"]
    assert signed.post(f"/v1/reports/{rid}/notes", json=note(chai()), headers=hdr(world)).status_code == 201
    before = events()
    odd = json.dumps({"kind": "ADD", "sku": "X", "requires": "shoe_size"})
    assert signed.post(f"/v1/reports/{rid}/notes", json=note(odd), headers=hdr(world)).status_code == 422
    assert events() == before


def test_the_claim_must_survive_a_retry_after_failover_the_same_way(signed, world):  # noqa: F811
    """A retry with the same op_id and the same proven session is a replay (200), not a second item."""
    rid = signed.post("/v1/reports", json=CART, headers=hdr(world)).json()["report"]["report_id"]
    h = hdr(world, clm=["age_over_18"], dsc=["age_over_18"])
    first = signed.post(f"/v1/reports/{rid}/notes", json={**note(beer()), "op_id": "op-beer"}, headers=h)
    again = signed.post(f"/v1/reports/{rid}/notes", json={**note(beer()), "op_id": "op-beer"}, headers=h)
    assert first.status_code == 201 and again.status_code == 200 and again.headers["idempotent-replay"] == "true"


# ---- J5: the limit follows the person -------------------------------------------------------------------
def test_the_rate_limit_follows_the_credential_not_the_device(signed, world):  # noqa: F811
    ratelimit.configure(burst=3, refill_per_s=0)                       # one burst, nothing back
    device_1 = hdr(world, ttl=300)                                      # the same person, two sessions (two browser windows)
    device_2 = hdr(world, ttl=301)
    assert device_1["Authorization"] != device_2["Authorization"]
    rid = signed.post("/v1/reports", json=CART, headers=device_1).json()["report"]["report_id"]          # 1 of 3
    assert signed.post(f"/v1/reports/{rid}/notes", json=note(chai()), headers=device_2).status_code == 201   # 2 of 3 (other window)
    assert signed.post(f"/v1/reports/{rid}/notes", json=note(chai()), headers=device_1).status_code == 201   # 3 of 3
    refused = signed.post(f"/v1/reports/{rid}/notes", json=note(chai()), headers=device_2)
    assert refused.status_code == 429 and refused.json()["scope"] == "actor"                              # same person: still refused
    again = signed.post(f"/v1/reports/{rid}/notes", json=note(chai()), headers=device_1)
    assert again.status_code == 429
    # a different credential is unaffected
    other = hdr(world, sub=ACTOR_B, cid=CRED_B)
    assert signed.post("/v1/reports", json=CART, headers=other).status_code == 201


# ---- J6: the revoked-credential event ---------------------------------------------------------------------
def test_carry_refuses_a_real_signed_event_whose_credential_was_revoked(signed, world):  # noqa: F811
    made = signed.post("/v1/reports", json=CART, headers=hdr(world))
    ev = made.json()["event"]
    assert ev["credential_id"] == CRED_A and ev["sig"]                  # a real event, signed by this region, for CRED_A
    carrier = hdr(world, sub=ACTOR_B, cid=CRED_B)                       # another, still active person hands it over

    ok = signed.post("/v1/carry", json={"events": [ev]}, headers=carrier).json()["results"][0]
    assert ok["result"] == "dup"                                       # before the revocation: just a duplicate

    world["snapshot"](statuses={CRED_A: "r", CRED_B: "a"}, ver=2)        # the issuer revokes CRED_A; this region hears of it
    with store.read() as conn:
        quarantined = store.quarantine_count(conn)
    state_before = signed.get("/v1/status").json()["state_hash"]
    count_before = events()

    res = signed.post("/v1/carry", json={"events": [ev]}, headers=carrier).json()["results"][0]
    assert res == {"event_id": ev["event_id"], "result": "rejected", "reason": "revoked_credential"}
    with store.read() as conn:
        assert store.quarantine_count(conn) == quarantined + 1         # kept as evidence, never applied
    assert signed.get("/v1/status").json()["state_hash"] == state_before and events() == count_before


def test_an_event_from_an_active_credential_or_with_none_is_unaffected(signed, world):  # noqa: F811
    mine = signed.post("/v1/reports", json=CART, headers=hdr(world, sub=ACTOR_B, cid=CRED_B)).json()["event"]
    world["snapshot"](statuses={CRED_A: "r", CRED_B: "a"}, ver=2)       # somebody else is revoked, not CRED_B
    carrier = hdr(world, sub=ACTOR_B, cid=CRED_B)
    assert signed.post("/v1/carry", json={"events": [mine]}, headers=carrier).json()["results"][0]["result"] == "dup"
    no_status = {k: v for k, v in mine.items() if k != "credential_id"}
    # an event without a credential_id (sealed before sign-in existed) can only fail the hash check, never "revoked"
    assert signed.post("/v1/carry", json={"events": [no_status]}, headers=carrier).json()["results"][0]["reason"] == "bad_hash"


# ---- the sidecar client must not be rebuilt per call ------------------------------------------------------------------
def test_the_sidecar_http_client_is_built_once_per_event_loop_not_per_call(monkeypatch):
    """Building an AsyncClient costs 300-500 ms of CPU on Windows and froze the region's event loop at every sign-in."""
    import asyncio
    import dataclasses

    import httpx

    from region import config
    from region.api import routes_auth

    built = []
    real = httpx.AsyncClient

    class Counting(real):
        def __init__(self, *a, **kw):
            built.append(1)
            super().__init__(*a, **kw)

    monkeypatch.setattr(routes_auth.httpx, "AsyncClient", Counting)
    monkeypatch.setattr(routes_auth, "_client", None)
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, auth_sidecar_url="http://127.0.0.1:1"))

    async def many():
        return [await routes_auth._sidecar("GET", "/internal/v1/health") for _ in range(5)]

    outs = asyncio.run(many())
    assert all(status == 503 for status, _ in outs)        # nothing listens there: an honest "unavailable", never a raise
    assert len(built) == 1


# ---- onboarding without claim codes: the demo relay issues one when a member first signs in (real issuer) ----------------
def test_the_demo_relay_hands_out_a_one_time_code_that_a_real_wallet_redeems(stack, tmp_path):
    import socket
    import threading

    import httpx

    from scripts.true_stack import serve_relay
    from tests.convergence.conftest import Wallet

    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    threading.Thread(target=serve_relay, args=(stack, port), daemon=True).start()
    url = f"http://127.0.0.1:{port}/claim-code"
    for _ in range(50):
        try:
            httpx.post(url, timeout=0.3)
            break
        except httpx.HTTPError:
            import time
            time.sleep(0.1)
    key = {"X-Admin-Key": "dev-admin"}

    assert httpx.post(url, json={"member": "Alice"}).status_code == 401                 # the admin key is required
    assert httpx.post(url, json={"member": "Nobody"}, headers=key).status_code == 404    # only a known demo member
    got = httpx.post(url, json={"member": "Alice"}, headers=key)
    assert got.status_code == 201 and got.json()["code"]
    claimed = Wallet(tmp_path / "alice.json", stack.issuer_url).claim(got.json()["code"])   # holder proof + issuer signature
    assert claimed.get("ok"), claimed
    again = Wallet(tmp_path / "mallory.json", stack.issuer_url).claim(got.json()["code"])
    assert not again.get("ok")                                                              # a code works once

    # several members onboarding at the same moment (what "Sign in everyone" does) all get a code
    import concurrent.futures
    names = ["Bob", "Chitra", "Dev", "Esha", "Sunita"]
    with concurrent.futures.ThreadPoolExecutor(len(names)) as pool:
        codes = list(pool.map(lambda n: httpx.post(url, json={"member": n}, headers=key, timeout=10), names))
    assert [r.status_code for r in codes] == [201] * len(names)
