"""Convergence (TRUE x SyncShield) test harness: REAL partner crypto, REAL processes.

Everything runs on an isolated port range and temp directories, so it never touches a running demo:
    issuer 14001, auth sidecars 14101.., regions 18101.. (tests/integration uses 18001..)

Needs Node 24+ and `npm install` in collaboration/true. The tests skip (with a reason) when they are missing.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from true_stack import Stack  # noqa: E402  (scripts/true_stack.py)
from region.engine import signing  # noqa: E402

TRUE_DIR = ROOT / "collaboration" / "true"
WALLET_CLI = TRUE_DIR / "scripts" / "wallet-cli.mjs"
REGIONS = ["mum", "pun"]
REGION_PORTS = {"mum": 18101, "pun": 18102}
REGION_URL = {r: f"http://127.0.0.1:{p}" for r, p in REGION_PORTS.items()}
PEER_TOKEN = "convergence-peer"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None or not (TRUE_DIR / "node_modules").exists(),
    reason="Node 24+ and `npm install` in collaboration/true are required for the real-crypto convergence tests",
)


def pytest_collection_modifyitems(items):
    """Only tests that start the real Node stack need Node; the pure-Python ones always run."""
    for item in items:
        if {"stack", "cluster", "make_wallet", "members"} & set(item.fixturenames):
            item.add_marker(needs_node)


class Wallet:
    """A headless user: holds one holder key and the credential(s) the issuer gave it."""

    def __init__(self, path: Path, issuer_url: str):
        self.path, self.issuer_url = path, issuer_url

    def _run(self, *args):
        r = subprocess.run(["node", str(WALLET_CLI), *args, "--wallet", str(self.path)],
                           cwd=TRUE_DIR, capture_output=True, text=True, timeout=60)
        out = r.stdout.strip().splitlines()
        return json.loads(out[-1]) if out else {"ok": False, "error": r.stderr[-400:]}

    def claim(self, code):
        return self._run("claim", "--issuer", self.issuer_url, "--code", code)

    def login(self, region_url, **flags):
        extra = []
        for k, v in flags.items():
            extra += [f"--{k}", str(v)]
        return self._run("login", "--region", region_url, *extra)

    def present(self, nonce, audience, **flags):
        extra = []
        for k, v in flags.items():
            extra += [f"--{k}", str(v)]
        return self._run("present", "--nonce", nonce, "--audience", audience, *extra)


@pytest.fixture(scope="session")
def stack(tmp_path_factory):
    d = tmp_path_factory.mktemp("true-stack")
    os.environ.setdefault("STATUS_REFRESH_MS", "700")
    st = Stack(d, regions=REGIONS, issuer_port=14001, sidecar_base_port=14101)
    st.up()
    yield st
    st.down()


@pytest.fixture(scope="session")
def members(stack):
    return {
        "alice": stack.admin("POST", "/v1/syncshield/members", {"name": "Alice", "role": "officer"})[1],
        "bob": stack.admin("POST", "/v1/syncshield/members", {"name": "Bob", "role": "officer"})[1],
        "sup": stack.admin("POST", "/v1/syncshield/members", {"name": "Sunita", "role": "supervisor"})[1],
        "admin": stack.admin("POST", "/v1/syncshield/members", {"name": "Admin", "role": "admin"})[1],
    }


@pytest.fixture(scope="session")
def make_wallet(stack, tmp_path_factory):
    d = tmp_path_factory.mktemp("wallets")

    def make(member, name):
        code = stack.admin("POST", "/v1/syncshield/claim-codes", {"actorId": member["actor_id"], "ttlMinutes": 30})[1]["code"]
        w = Wallet(d / f"{name}.json", stack.issuer_url)
        res = w.claim(code)
        assert res.get("ok"), res
        w.credential_id = res["credentialId"]
        w.actor_id = member["actor_id"]
        return w
    return make


@pytest.fixture(scope="session")
def keys(tmp_path_factory):
    """Origin signing keys (Ed25519) shared by both regions so carry works."""
    d = tmp_path_factory.mktemp("sigkeys")
    os.environ["SIGNING_KEYS_DIR"] = str(d / "keys")
    os.environ["PUBLIC_KEYS_FILE"] = str(d / "pub.json")
    signing.ensure_keys(REGIONS)
    return d


def _start_region(region, stack, keys_dir, tmp, other_up=True, auth=True):
    other = [r for r in REGIONS if r != region]
    env = dict(os.environ)
    env.update(
        REGION=region, PORT=str(REGION_PORTS[region]), DB_PATH=str(tmp / f"{region}.db"),
        PEERS=",".join(f"{o}={REGION_URL[o]}" for o in other), PEER_TOKEN=PEER_TOKEN, SYNC_INTERVAL_MS="300",
        API_KEYS=f"dev-officer:officer-{region}-1:officer", ENV_FILE="none", PYTHONPATH=str(ROOT),
        SIGNING_KEYS_DIR=str(keys_dir / "keys"), PUBLIC_KEYS_FILE=str(keys_dir / "pub.json"),
    )
    if auth:
        env.update(stack.region_env(region))
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "region.main:app", "--host", "127.0.0.1", "--port", str(REGION_PORTS[region]),
         "--workers", "1", "--log-level", "error"], cwd=ROOT, env=env)


def wait_up(region, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(REGION_URL[region] + "/healthz", timeout=0.5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.15)
    raise TimeoutError(region)


class Cluster:
    def __init__(self, procs, tmp, stack, keys_dir):
        self.procs, self.tmp, self.stack, self.keys_dir = procs, tmp, stack, keys_dir

    def kill(self, region):
        self.procs[region].kill()
        self.procs[region].wait()

    def restart(self, region):
        self.procs[region] = _start_region(region, self.stack, self.keys_dir, self.tmp)
        wait_up(region)

    def status(self, region):
        return httpx.get(REGION_URL[region] + "/v1/status", timeout=3).json()


@pytest.fixture(scope="module")
def cluster(stack, keys, members, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("regions")
    procs = {r: _start_region(r, stack, keys, tmp) for r in REGIONS}
    for r in REGIONS:
        wait_up(r)
    c = Cluster(procs, tmp, stack, keys)
    yield c
    for p in c.procs.values():
        p.kill()
        p.wait()


def until(fn, timeout=8.0, step=0.15):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("condition not reached in time")


def bearer(token):
    return {"Authorization": f"Bearer {token}"}
