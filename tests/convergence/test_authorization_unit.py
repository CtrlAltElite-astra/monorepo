"""Session verification, status policy and authorization, with an injected clock (no sleeping, no Node).

Tokens and snapshots are minted here with PyJWT and throw-away keys, in the exact formats the real sidecar
and issuer produce (the real ones are exercised in test_real_flow.py / test_revocation_failover.py).
"""
import dataclasses
import json
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from jwt.algorithms import ECAlgorithm

from region import config, store
from region.engine import canonical
from region.main import app
from region.trust import adapter as adapter_mod
from region.trust import true_status
from region.trust.true_adapter import TrueSessionAdapter
from region.trust.true_session import AuthError, SessionKeyRegistry, SessionVerifier
from region.trust.true_status import StatusStore, evaluate

ISSUER_ID = "issuer:astra-institute"
ACTOR_A, ACTOR_B, ACTOR_S = ("act_" + c * 16 for c in "abc")
CRED_A, CRED_B, CRED_S = ("cred_" + c * 24 for c in "abc")
CASES = json.loads((Path(__file__).resolve().parents[2] / "collaboration" / "true" / "tests"
                    / "status-policy-cases.json").read_text())["cases"]


# ---- helpers -----------------------------------------------------------------------------------------
def new_key():
    priv = ec.generate_private_key(ec.SECP256R1())
    return priv, json.loads(ECAlgorithm.to_jwk(priv.public_key()))


class Clock:
    def __init__(self, t=1_800_000_000):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def world(tmp_path):
    """Pinned files + keys + clock. `mint` makes session tokens, `snapshot` writes a signed status file."""
    clock = Clock()
    issuer_priv, issuer_pub = new_key()
    region_priv, region_pub = new_key()
    keydir = tmp_path / "session-keys"
    keydir.mkdir()
    (keydir / "mum.json").write_text(json.dumps({"kid": "mum-test", "region": "mum", "jwk": region_pub}))
    status_file = tmp_path / "status.jws"

    def mint(sub=ACTOR_A, role="officer", cid=CRED_A, ttl=300, cexp_in=86_400, key=None, kid="mum-test", **over):
        now = clock()
        claims = {"iss": "syncshield-region:mum", "sub": sub, "aud": "syncshield", "iat": now, "nbf": now,
                  "exp": now + ttl, "jti": "j" + str(now), "cid": cid, "role": role, "sver": 1, "sat": now,
                  "cexp": now + cexp_in}
        claims.update(over)
        headers = {"kid": kid, "typ": "syncshield-session+jwt"}
        return jwt.encode({k: v for k, v in claims.items() if v is not None}, key or region_priv, algorithm="ES256", headers=headers)

    def snapshot(statuses=None, ver=1, issued_in=0, key=None, typ="syncshield-status+jwt", iss=ISSUER_ID):
        iat = clock() + issued_in
        payload = {"iss": iss, "iat": iat, "ver": ver, "next_update": iat + 600,
                   "statuses": statuses if statuses is not None else {CRED_A: "a", CRED_B: "a", CRED_S: "a"}}
        status_file.write_text(jwt.encode(payload, key or issuer_priv, algorithm="ES256", headers={"typ": typ}))
        return iat

    verifier = SessionVerifier(SessionKeyRegistry(str(keydir)), clock=clock)
    store_ = StatusStore(str(status_file), ISSUER_ID, issuer_pub)
    adapter = TrueSessionAdapter(verifier, store_, clock=clock)
    return dict(clock=clock, mint=mint, snapshot=snapshot, verifier=verifier, status=store_, adapter=adapter,
                issuer_priv=issuer_priv, issuer_pub=issuer_pub, region_priv=region_priv, status_file=status_file,
                keydir=keydir, status_file_path=str(status_file))


# ---- 1. the freshness policy matches the TypeScript sidecar's table -----------------------------------
@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_status_policy_parity_with_the_sidecar(case):
    statuses = {"cred_x": case["status"]} if case["status"] else {}
    d = evaluate(statuses, 1_000_000, "cred_x", 1_000_000 + case["age"], case["purpose"])
    assert d.allow == case["allow"]
    if d.allow:
        assert d.mode == case["mode"]
    else:
        assert d.reason == case["reason"]


def test_no_snapshot_is_never_an_approval():
    assert evaluate(None, None, "cred_x", 5, "write") == true_status.Decision(False, reason="no_status")


# ---- 2. session token verification ---------------------------------------------------------------------
def test_a_good_session_verifies(world):
    s = world["verifier"].verify(world["mint"]())
    assert (s.actor_id, s.role, s.credential_id, s.region) == (ACTOR_A, "officer", CRED_A, "mum")


def expect(verifier, token, code):
    with pytest.raises(AuthError) as e:
        verifier.verify(token)
    assert e.value.code == code


def test_signature_by_the_wrong_key_is_rejected_even_with_a_known_kid(world):
    other, _ = new_key()
    expect(world["verifier"], world["mint"](key=other), "bad_signature")


def test_unknown_key_id_is_rejected(world):
    expect(world["verifier"], world["mint"](kid="mum-nope"), "unknown_key")


def test_algorithm_confusion_is_rejected(world):
    now = world["clock"]()
    hs = jwt.encode({"iss": "syncshield-region:mum", "sub": ACTOR_A, "aud": "syncshield", "iat": now, "nbf": now, "exp": now + 60},
                    "a-shared-secret-of-sufficient-length-32b", algorithm="HS256",
                    headers={"kid": "mum-test", "typ": "syncshield-session+jwt"})
    expect(world["verifier"], hs, "bad_algorithm")
    none = jwt.encode({"sub": ACTOR_A}, None, algorithm="none", headers={"kid": "mum-test", "typ": "syncshield-session+jwt"})
    expect(world["verifier"], none, "bad_algorithm")


def test_wrong_token_type_audience_and_issuer(world):
    mint = world["mint"]
    expect(world["verifier"], jwt.encode({"x": 1}, world["region_priv"], algorithm="ES256",
                                         headers={"kid": "mum-test", "typ": "JWT"}), "wrong_token_type")
    expect(world["verifier"], mint(aud="someone-else"), "wrong_audience")
    expect(world["verifier"], mint(iss="syncshield-region:pun"), "wrong_issuer")   # signed by mum's key, claims pun


def test_expiry_not_before_and_absurd_lifetimes(world):
    c, mint, v = world["clock"], world["mint"], world["verifier"]
    t = mint(ttl=300)
    c.t += 299
    assert v.verify(t).actor_id == ACTOR_A
    c.t += 1
    expect(v, t, "session_expired")
    c.t = 1_800_000_000
    expect(v, mint(nbf=c.t + 100), "session_not_yet_valid")
    expect(v, mint(ttl=7_200), "malformed_session")                 # 2 h: refused even though correctly signed
    expect(v, mint(ttl=300, cexp_in=100), "malformed_session")      # outlives its credential


def test_malformed_claims_are_rejected(world):
    mint, v = world["mint"], world["verifier"]
    expect(v, mint(role="superuser"), "malformed_session")
    expect(v, mint(sub="alice"), "malformed_session")
    expect(v, mint(cid="whatever"), "malformed_session")
    expect(v, mint(jti=""), "malformed_session")
    expect(v, mint(exp="tomorrow"), "malformed_session")
    expect(v, "", "missing_session")
    expect(v, "a.b.c", "malformed_session")


# ---- 3. status snapshot: signature, tamper, rollback ------------------------------------------------------
def test_status_store_accepts_only_issuer_signed_snapshots(world):
    st = world["status"]
    assert st.current() is None
    world["snapshot"](ver=3)
    assert st.current().ver == 3
    stranger, _ = new_key()
    world["snapshot"](ver=9, key=stranger)                  # signed by someone else
    assert st.current().ver == 3 and "rejected" in st.last_error
    world["snapshot"](ver=4, iss="issuer:evil")             # right key, wrong issuer
    assert st.current().ver == 3
    world["snapshot"](ver=4, typ="JWT")
    assert st.current().ver == 3


def test_status_store_refuses_rollback_and_survives_garbage(world):
    st = world["status"]
    world["snapshot"](ver=5, statuses={CRED_A: "r"})
    assert st.current().statuses[CRED_A] == "r"
    time.sleep(0.01)
    world["snapshot"](ver=4, statuses={CRED_A: "a"})        # an older snapshot that would hide the revocation
    assert st.current().ver == 5 and st.last_error == "rollback"
    world["status_file"].write_text("not a jws")
    assert st.current().ver == 5                             # last good kept


# ---- 4. the adapter's decisions, from CURRENT evidence ----------------------------------------------------
def test_mode_is_computed_from_current_status_not_from_the_token(world):
    c, a, mint = world["clock"], world["adapter"], world["mint"]
    world["snapshot"]()
    tok = mint(ttl=600)
    assert a.verify_actor(tok, "set").mode == "online"
    c.t += 100
    assert a.verify_actor(tok, "set").mode == "cached"
    c.t += 520                                               # snapshot now 620 s old, token (600 s) expired
    expect_expired = a.verify_actor(tok, "set")
    assert expect_expired.allowed is False and expect_expired.reason == "session_expired"
    fresh = mint(ttl=300)
    d = a.verify_actor(fresh, "set")
    assert d.allowed and d.mode == "stale"                   # ordinary write, known active credential
    assert a.verify_actor(fresh, "resolve").allowed is False  # privileged: needs <= 10 min


def test_revoked_unknown_and_missing_status_are_denied(world):
    a, mint = world["adapter"], world["mint"]
    assert a.verify_actor(mint(), "set").reason == "no_status"
    world["snapshot"](statuses={CRED_A: "r", CRED_B: "a"})
    assert a.verify_actor(mint(cid=CRED_A), "set").reason == "revoked"
    assert a.verify_actor(mint(cid=CRED_B), "set").allowed
    assert a.verify_actor(mint(cid=CRED_S), "set").reason == "unknown_credential"


def test_roles_are_enforced_from_the_session_only(world):
    a, mint = world["adapter"], world["mint"]
    world["snapshot"]()
    assert a.verify_actor(mint(role="officer"), "resolve").reason == "forbidden"
    assert a.verify_actor(mint(sub=ACTOR_S, role="supervisor", cid=CRED_S), "resolve").allowed
    assert a.verify_actor(mint(sub=ACTOR_S, role="supervisor", cid=CRED_S), "admin").reason == "forbidden"
    assert a.verify_actor(mint(role="admin"), "admin").allowed
    assert a.verify_actor(mint(role="admin"), "create").reason == "forbidden"   # admin is not a cart writer


# ---- 5. through the real API: no mutation on denial, ownership, review ----------------------------------------
@pytest.fixture
def api(tmp_path, monkeypatch, world):
    s = dataclasses.replace(
        config.settings, region="mum", db_path=str(tmp_path / "mum.db"), peers={}, peer_token="t",
        api_keys={"dev-officer": ("officer-mum-1", "officer")}, auth_enabled=True,
        auth_issuer_file="x", auth_session_key_dir=str(world["keydir"]), auth_status_file=world["status_file_path"])
    monkeypatch.setattr(config, "settings", s)
    monkeypatch.setenv("SIGNING_KEYS_DIR", str(tmp_path / "nokeys"))
    monkeypatch.setenv("PUBLIC_KEYS_FILE", str(tmp_path / "nokeys.json"))
    store.init()
    previous = adapter_mod.get_adapter()
    adapter_mod.set_adapter(world["adapter"])
    world["snapshot"]()
    client = TestClient(app)
    yield client
    adapter_mod.set_adapter(previous)
    store.close()


def hdr(world, **kw):
    return {"Authorization": "Bearer " + world["mint"](**kw)}


def fingerprint():
    with store.read() as conn:
        return (store.event_count(conn), store.own_head(conn), store.load_hlc(conn),
                conn.execute("SELECT COUNT(*) FROM idempotency").fetchone()[0])


def test_denied_writes_do_not_touch_seq_hlc_events_or_the_op_cache(api, world):
    first = api.post("/v1/reports", json={"title": "t", "category": "cart", "severity": 1}, headers=hdr(world))
    assert first.status_code == 201
    before = fingerprint()
    other, _ = new_key()
    attempts = [
        {},
        {"Authorization": "Bearer junk"},
        {"X-API-Key": "dev-officer"},
        {"Authorization": "Bearer " + world["mint"](key=other)},                 # forged
        {"Authorization": "Bearer " + world["mint"](aud="nope")},
        {"Authorization": "Bearer " + world["mint"](ttl=1)},                     # becomes expired below
        hdr(world, role="admin"),                                                 # wrong role for a write
    ]
    world["clock"].t += 5
    for h in attempts:
        r = api.post("/v1/reports", json={"title": "x", "category": "cart", "severity": 1, "op_id": "op-denied"}, headers=h)
        assert r.status_code in (401, 403), (h, r.text)
    assert fingerprint() == before


def test_revoked_credential_cannot_write_after_a_new_snapshot(api, world):
    h = hdr(world, ttl=600)
    assert api.post("/v1/reports", json={"title": "t", "category": "cart", "severity": 1}, headers=h).status_code == 201
    world["snapshot"](statuses={CRED_A: "r"}, ver=2)
    r = api.post("/v1/reports", json={"title": "t2", "category": "cart", "severity": 1}, headers=h)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "credential_revoked"


def test_stale_status_degrades_writes_but_blocks_privileged_actions(api, world):
    world["snapshot"]()
    sup = {"sub": ACTOR_S, "role": "supervisor", "cid": CRED_S, "ttl": 600}
    world["clock"].t += 700                                   # snapshot 700 s old
    sup_h = hdr(world, **{**sup, "ttl": 300})
    r = api.post("/v1/reports", json={"title": "t", "category": "cart", "severity": 1}, headers=sup_h)
    assert r.status_code == 201 and r.json()["event"]["auth_mode"] == "stale"      # honest label on the event
    res = api.post("/v1/reports/R-mum-1/resolve", json={"field": "status", "value": "closed", "candidates": [], "reason": "x"},
                   headers=sup_h)
    assert res.status_code == 503 and res.json()["detail"]["code"] == "status_stale_for_purpose"
    world["clock"].t += 300                                   # 1000 s: beyond the hard bound
    r2 = api.post("/v1/reports", json={"title": "t", "category": "cart", "severity": 1}, headers=hdr(world, **sup))
    assert r2.status_code == 503


def test_ownership_privacy_and_op_id_binding(api, world):
    a, b, s = hdr(world), hdr(world, sub=ACTOR_B, cid=CRED_B), hdr(world, sub=ACTOR_S, role="supervisor", cid=CRED_S)
    made = api.post("/v1/reports", json={"title": "mine", "category": "cart", "severity": 1, "op_id": "op-own"}, headers=a)
    rid = made.json()["report"]["report_id"]
    assert made.json()["event"]["owner"] == ACTOR_A
    assert [r["report_id"] for r in api.get("/v1/reports", headers=b).json()] == []
    assert api.get(f"/v1/reports/{rid}", headers=b).status_code == 404
    assert api.patch(f"/v1/reports/{rid}", json={"field": "severity", "value": 3}, headers=b).status_code == 404
    assert [r["report_id"] for r in api.get("/v1/reports", headers=s).json()] == [rid]            # review visibility
    assert api.patch(f"/v1/reports/{rid}", json={"field": "severity", "value": 3}, headers=s).status_code == 403
    # another actor cannot obtain, or deduplicate against, someone else's operation
    clash = api.post("/v1/reports", json={"title": "mine", "category": "cart", "severity": 1, "op_id": "op-own"}, headers=b)
    assert clash.status_code == 409
    replay = api.post("/v1/reports", json={"title": "mine", "category": "cart", "severity": 1, "op_id": "op-own"}, headers=a)
    assert replay.status_code == 200 and replay.json()["event_id"] == made.json()["event_id"]
    # reads without a session are refused in auth mode (they are public in base mode)
    assert api.get("/v1/reports").status_code == 401
    assert api.get("/v1/review").status_code == 401
    assert api.get("/healthz").status_code == 200
    assert api.get("/v1/status").status_code == 200


def conflicting_events(rid):
    """Two concurrent assigned_team writes from different nodes (what a partition produces), hashed for real."""
    def ev(origin, seq, team, t):
        e = dict(v=1, event_id=f"{origin}:{seq}", origin=origin, seq=seq, report_id=rid, type="SET", field="assigned_team",
                 value=team, supersedes=["mum:1"], hlc=[2_000_000_000_000 + t, 0], priority=1, actor=ACTOR_A,
                 reason=None, wall="x", sig=None, op_id=None, observed=None)
        e["hash"] = canonical.event_hash(e)
        return e
    return [ev("pun", 1, "Bhiwandi", 1), ev("del", 1, "Chakan", 2)]   # two PEER origins, never this region's


def test_review_queue_and_resolve_with_the_role_gate(api, world):
    a = hdr(world)
    s = hdr(world, sub=ACTOR_S, role="supervisor", cid=CRED_S)
    rid = api.post("/v1/reports", json={"title": "c", "category": "cart", "severity": 1}, headers=a).json()["report"]["report_id"]
    from region.sync import ingest
    ingest.ingest(conflicting_events(rid), source="test")

    q = api.get("/v1/review", headers=s).json()
    assert [(i["report_id"], i["field"], i["receipt"]["flags"]) for i in q] == [(rid, "assigned_team", ["DOUBLE_DISPATCH"])]
    assert [i["report_id"] for i in api.get("/v1/review", headers=a).json()] == [rid]   # owner sees own cart's item

    body = {"field": "assigned_team", "value": "Gurgaon", "candidates": ["pun:1", "del:1"], "reason": "closest centre"}
    assert api.post(f"/v1/reports/{rid}/resolve", json=body, headers=a).status_code == 403          # officer
    stale = api.post(f"/v1/reports/{rid}/resolve", json={**body, "candidates": ["pun:1"]}, headers=s)
    assert stale.status_code == 409 and stale.json()["error"] == "stale_resolve" and "live" in stale.json()
    ok = api.post(f"/v1/reports/{rid}/resolve", json=body, headers=s)
    assert ok.status_code == 201, ok.text
    ev = ok.json()["event"]
    assert ev["type"] == "RESOLVE" and ev["reason"] == "closest centre" and ev["actor"] == ACTOR_S and ev["role"] == "supervisor"
    assert api.get(f"/v1/reports/{rid}", headers=s).json()["state"]["assigned_team"] == "Gurgaon"
    assert api.get("/v1/review", headers=s).json() == []
    again = api.post(f"/v1/reports/{rid}/resolve", json=body, headers=s)
    assert again.status_code == 409 and again.json()["error"] == "no_conflict"


def test_event_provenance_is_inside_the_signed_hash(api, world):
    r = api.post("/v1/reports", json={"title": "t", "category": "cart", "severity": 1}, headers=hdr(world))
    ev = r.json()["event"]
    assert canonical.event_hash(ev) == ev["hash"]
    assert {"role", "auth_mode", "credential_id", "owner"} <= set(ev)
    tampered = dict(ev, role="admin")
    assert canonical.event_hash(tampered) != ev["hash"]
