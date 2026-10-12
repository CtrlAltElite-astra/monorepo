"""Three regions (mum, pun, del) in auth mode: one login, accepted everywhere, surviving a region crash.

Real partner crypto, real issuer, one real sidecar and one real FastAPI process per region, on their own
ports so it never touches the two-region fixtures or a running demo:
    issuer 14201, sidecars 14301..14303, regions 18201..18203
"""
import os
import subprocess
import sys

import httpx
import pytest

from tests.convergence.conftest import ROOT, Wallet, bearer, needs_node, until
from true_stack import Stack

pytestmark = needs_node

REGIONS = ["mum", "pun", "del"]
PORTS = {"mum": 18201, "pun": 18202, "del": 18203}
URL = {r: f"http://127.0.0.1:{p}" for r, p in PORTS.items()}
PEER_TOKEN = "three-region-peer"
C = httpx.Client(timeout=10)


@pytest.fixture(scope="module")
def three(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("three")
    os.environ.setdefault("STATUS_REFRESH_MS", "700")
    stack = Stack(tmp / "true", regions=REGIONS, issuer_port=14201, sidecar_base_port=14301)
    stack.up()
    keys = tmp / "sig"
    env_keys = dict(os.environ, SIGNING_KEYS_DIR=str(keys / "keys"), PUBLIC_KEYS_FILE=str(keys / "pub.json"),
                    PYTHONPATH=str(ROOT))
    subprocess.run([sys.executable, "-c",
                    "from region.engine import signing; signing.ensure_keys(['mum','pun','del'])"],
                   cwd=ROOT, env=env_keys, check=True)

    def start(region):
        env = dict(env_keys)
        env.update(
            REGION=region, PORT=str(PORTS[region]), DB_PATH=str(tmp / f"{region}.db"),
            PEERS=",".join(f"{o}={URL[o]}" for o in REGIONS if o != region), PEER_TOKEN=PEER_TOKEN,
            SYNC_INTERVAL_MS="300", API_KEYS=f"dev-officer:officer-{region}-1:officer", ENV_FILE="none",
            CORS_ORIGINS="http://localhost:5173",
        )
        env.update(stack.region_env(region))
        return subprocess.Popen([sys.executable, "-m", "uvicorn", "region.main:app", "--host", "127.0.0.1",
                                 "--port", str(PORTS[region]), "--workers", "1", "--log-level", "error"],
                                cwd=ROOT, env=env)

    procs = {r: start(r) for r in REGIONS}
    for r in REGIONS:
        until(lambda r=r: _up(r), timeout=25)
    member = stack.admin("POST", "/v1/syncshield/members", {"name": "Alice", "role": "officer"})[1]
    code = stack.admin("POST", "/v1/syncshield/claim-codes", {"actorId": member["actor_id"], "ttlMinutes": 30})[1]["code"]
    wallet = Wallet(tmp / "alice.json", stack.issuer_url)
    claimed = wallet.claim(code)
    assert claimed.get("ok"), claimed
    wallet.actor_id, wallet.credential_id = member["actor_id"], claimed["credentialId"]
    yield {"stack": stack, "procs": procs, "wallet": wallet, "member": member}
    for p in procs.values():
        p.kill()
        p.wait()
    stack.down()


def _up(region):
    try:
        return httpx.get(URL[region] + "/healthz", timeout=0.5).status_code == 200
    except httpx.HTTPError:
        return False


def _login(wallet, region):
    until(lambda: httpx.get(URL[region] + "/v1/auth/status", timeout=2).json().get("status"), timeout=15)
    out = wallet.login(URL[region])
    assert out["ok"], out
    return out


def _cart(token, region, title):
    return C.post(URL[region] + "/v1/reports", headers=bearer(token),
                  json={"title": title, "category": "cart", "severity": 1})


def test_one_login_is_accepted_by_all_three_regions_and_replicates(three):
    out = _login(three["wallet"], "del")                       # Delhi's own sidecar issued this session
    assert out["role"] == "officer" and out["actor_id"] == three["wallet"].actor_id
    made = {r: _cart(out["token"], r, f"cart via {r}") for r in REGIONS}
    assert all(r.status_code == 201 for r in made.values()), {k: v.text for k, v in made.items()}
    ids = [r.json()["report"]["report_id"] for r in made.values()]
    # every region ends up holding all three carts, written through three different regions
    for region in REGIONS:
        until(lambda region=region: {x["report_id"] for x in C.get(URL[region] + "/v1/reports", headers=bearer(out["token"])).json()}
              >= set(ids), timeout=20)


def test_session_survives_a_region_crash_and_revocation_reaches_all_three(three):
    out = _login(three["wallet"], "pun")
    three["procs"]["mum"].kill()
    three["procs"]["mum"].wait()
    # no new login: the same token still writes at the two survivors
    for region in ("pun", "del"):
        r = _cart(out["token"], region, f"after crash at {region}")
        assert r.status_code == 201, (region, r.text)

    status, body = three["stack"].admin(
        "POST", f"/v1/syncshield/members/{three['member']['actor_id']}/revoke", {"reason": "three-region test"})
    assert status == 200 and body["revokedCredentials"] >= 1, body

    def refused(region):
        r = _cart(out["token"], region, "should be refused")
        return r.status_code == 401 and r.json()["detail"]["code"] == "credential_revoked"

    until(lambda: refused("pun") and refused("del"), timeout=20)
