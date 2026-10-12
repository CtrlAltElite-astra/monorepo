"""T17 zero-loss failover against REAL region processes (owner: Rahil; D and C own the routes).

Two regions with their own SQLite files and Ed25519 keys. The shopper adds items, the
link is cut so Mumbai's last adds exist only on Mumbai's disk and in the outbox, Mumbai is
killed (TerminateProcess on Windows, SIGKILL elsewhere), the shopper keeps adding and fails
over to Pune carrying its outbox. Every acknowledged event must be on Pune
(POST /v1/events/has -> missing = []) with 0 shopper-visible errors. Then Mumbai restarts
on the same file and both converge with no duplicate items.

Until the pieces it needs are merged (write path v2 from A; /healthz, /v1/carry and
/v1/events/has from D) the test SKIPS and names what is missing; it never passes on a mock.
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from failover_harness import Shopper, http_send
from region.engine import signing

ROOT = Path(__file__).resolve().parents[2]
KEY = {"X-API-Key": "dev-officer"}
REGIONS = ("mum", "pun")
CART = "R-0000b0b0"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Regions:
    def __init__(self, tmp: Path):
        self.tmp, self.ports, self.procs = tmp, {r: free_port() for r in REGIONS}, {}
        self.urls = {r: f"http://127.0.0.1:{p}" for r, p in self.ports.items()}
        keys, public = tmp / "keys", {}
        keys.mkdir()
        for r in REGIONS:
            private, public[r] = signing.generate()
            (keys / f"{r}.key").write_text(private, encoding="ascii")
        (tmp / "public_keys.json").write_text(json.dumps(public), encoding="utf-8")

    def start(self, region):
        peers = ",".join(f"{p}={self.urls[p]}" for p in REGIONS if p != region)
        env = dict(os.environ, REGION=region, PORT=str(self.ports[region]), DB_PATH=str(self.tmp / f"{region}.db"),
                   PEERS=peers, PEER_TOKEN="t", ENV_FILE="none", ENABLE_ADMIN="1", SYNC_INTERVAL_MS="300",
                   API_KEYS="dev-officer:officer-1:officer", KEYS_DIR=str(self.tmp / "keys"),
                   PUBLIC_KEYS_FILE=str(self.tmp / "public_keys.json"), PYTHONPATH=str(ROOT))
        self.procs[region] = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "region.main:app", "--host", "127.0.0.1",
             "--port", str(self.ports[region]), "--workers", "1", "--log-level", "error"], cwd=ROOT, env=env)
        until(lambda: httpx.get(self.urls[region] + "/v1/health", timeout=0.5).status_code == 200, 20)

    def kill(self, region):
        self.procs[region].kill()
        self.procs[region].wait()

    def stop(self):
        for p in self.procs.values():
            p.kill()
            p.wait()

    def link(self, region, peer, mode):
        r = httpx.post(f"{self.urls[region]}/admin/links/{peer}", headers=KEY, json={"mode": mode}, timeout=3)
        assert r.status_code == 200, r.text

    def has(self, region, ids):
        r = httpx.post(self.urls[region] + "/v1/events/has", headers=KEY, json={"ids": list(ids)}, timeout=3)
        return r.json()["missing"]


def until(check, seconds):
    end = time.time() + seconds
    while time.time() < end:
        try:
            if check():
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise TimeoutError("condition not met in time")


def missing_pieces(url) -> list:
    """What T17 needs that this build does not answer yet (empty = run the test)."""
    need = []
    if httpx.get(url + "/healthz", timeout=2).status_code != 200:
        need.append("GET /healthz (D)")
    r = httpx.post(url + "/v1/reports", headers=KEY, timeout=3, json={
        "report_id": CART, "op_id": "t17-probe", "title": "Bob", "description": "", "category": "cart", "severity": 1})
    body = r.json() if r.status_code == 201 else {}
    if r.status_code != 201:
        need.append(f"POST /v1/reports with client report_id and category 'cart' (A; got {r.status_code})")
    elif not isinstance(body.get("event"), dict) or not body["event"].get("sig"):
        need.append("signed event in 2xx bodies (A: write path v2 calls signing.sign)")
    elif body["event"].get("op_id") != "t17-probe" or body["event"].get("report_id") != CART:
        need.append("op_id and client report_id stored on the event (A)")
    if httpx.post(url + "/v1/carry", headers=KEY, json={"events": []}, timeout=2).status_code != 200:
        need.append("POST /v1/carry (D)")
    if httpx.post(url + "/v1/events/has", headers=KEY, json={"ids": []}, timeout=2).status_code != 200:
        need.append("POST /v1/events/has (D)")
    return need


def add(shop, sku):
    text = json.dumps({"kind": "ADD", "sku": sku, "qty": 1, "user": "Bob"}, separators=(",", ":"))
    return shop.call("POST", f"/v1/reports/{CART}/notes", {"text": text})


def items(url):
    view = httpx.get(f"{url}/v1/reports/{CART}", headers=KEY, timeout=3).json()
    notes = (view.get("state") or view.get("report", {}).get("state") or {}).get("notes", [])
    return sorted(json.loads(n["text"])["sku"] for n in notes)


@pytest.fixture
def regions(tmp_path):
    rs = Regions(tmp_path)
    try:
        for r in REGIONS:
            rs.start(r)
        yield rs
    finally:
        rs.stop()


def test_t17_kill_9_with_unreplicated_writes_loses_nothing_acknowledged(regions):
    need = missing_pieces(regions.urls["mum"])
    if need:
        pytest.skip("T17 live waits for: " + "; ".join(need))

    send = http_send(regions.urls, KEY, timeout_s=0.6)
    shop = Shopper(send, REGIONS)
    try:
        for sku in ("FCOFF-500", "GIFT-500"):
            assert add(shop, sku)[0] == 201
        until(lambda: regions.has("pun", shop.acknowledged) == [], 10)  # replicated normally

        regions.link("mum", "pun", "down")  # Mumbai's next adds reach no peer
        regions.link("pun", "mum", "down")
        for sku in ("MAT-01", "CHAI-01", "BAG-01"):
            assert add(shop, sku)[0] == 201
        at_risk = [i for i in shop.acknowledged if i.startswith("mum:")][-3:]
        assert regions.has("pun", at_risk) == at_risk  # only Mumbai's disk and the outbox hold them

        regions.kill("mum")  # kill -9
        killed_at = time.time()
        assert add(shop, "LAMP-01")[0] == 201
        failover_s = time.time() - killed_at
        assert add(shop, "KURTA-01")[0] == 201

        assert shop.errors == 0
        assert regions.has("pun", shop.acknowledged) == []  # acknowledged = present on survivors
        assert sorted(r["event_id"] for r in shop.carried if r["result"] == "new") == sorted(at_risk)
        print(f"\nT17 live: kill to first success on Pune {failover_s * 1000:.0f} ms")

        regions.start("mum")  # restart on the same file
        regions.link("mum", "pun", "up")
        regions.link("pun", "mum", "up")
        status = lambda r: httpx.get(regions.urls[r] + "/v1/status", timeout=2).json()  # noqa: E731
        until(lambda: status("mum")["event_digest"] == status("pun")["event_digest"], 15)
        assert status("mum")["state_hash"] == status("pun")["state_hash"]  # CONVERGED
        want = sorted(["FCOFF-500", "GIFT-500", "MAT-01", "CHAI-01", "BAG-01", "LAMP-01", "KURTA-01"])
        assert items(regions.urls["mum"]) == items(regions.urls["pun"]) == want  # no duplicate items
    finally:
        send.close()
