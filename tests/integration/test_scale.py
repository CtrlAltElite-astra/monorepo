"""Scaling controls on real processes (owner: Simrit): power, the region limit, Bengaluru, the status blocks.

Regions are started through tests.integration.scale_app, which is region.main:app plus the one hook
(`scale.install(app)`) that the joining phase adds to region/main.py. Ports 19101-19104, their own databases and
their own signing keys, so nothing here touches a running demo.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from loadgen import SHOPPERS, run_benchmark, shopper_key  # noqa: E402

PORTS = {"mum": 19101, "pun": 19102, "del": 19103, "blr": 19104}
URL = {r: f"http://127.0.0.1:{p}" for r, p in PORTS.items()}
KEY = {"X-API-Key": "dev-officer"}
SHOPPER_KEYS = ",".join(f"dev-{s.lower()}:{s.lower()}:officer" for s in SHOPPERS)


class Mesh:
    def __init__(self, tmp: Path):
        self.tmp, self.procs = tmp, {}
        (tmp / "keys").mkdir(exist_ok=True)

    def start(self, region: str, peers: list) -> None:
        env = dict(os.environ)
        env.update(
            REGION=region, PORT=str(PORTS[region]), DB_PATH=str(self.tmp / f"{region}.db"),
            PEERS=",".join(f"{p}={URL[p]}" for p in peers), PEER_TOKEN="scale-test", SYNC_INTERVAL_MS="200",
            API_KEYS=f"dev-officer:officer-{region}-1:officer,{SHOPPER_KEYS}",
            PYTHONPATH=str(ROOT), ENV_FILE="none",
            SIGNING_KEYS_DIR=str(self.tmp / "keys"), PUBLIC_KEYS_FILE=str(self.tmp / "public_keys.json"),
        )
        self.procs[region] = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "tests.integration.scale_app:app", "--host", "127.0.0.1",
             "--port", str(PORTS[region]), "--workers", "1", "--log-level", "error"], cwd=ROOT, env=env)
        self.wait_up(region)

    def wait_up(self, region: str, timeout: float = 20) -> None:
        end = time.time() + timeout
        while time.time() < end:
            try:
                if httpx.get(f"{URL[region]}/v1/health", timeout=0.5).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise TimeoutError(f"{region} did not start")

    def kill(self, region: str) -> None:
        p = self.procs.pop(region, None)
        if p and p.poll() is None:
            p.kill()
            p.wait(timeout=10)

    def stop(self) -> None:
        for r in list(self.procs):
            self.kill(r)

    def status(self, region: str) -> dict:
        return httpx.get(f"{URL[region]}/v1/status", timeout=3).json()

    def config(self, region: str, body: dict):
        return httpx.post(f"{URL[region]}/admin/config", json=body, headers=KEY, timeout=3)

    def converged(self, regions: list) -> bool:
        try:
            s = [self.status(r) for r in regions]
        except httpx.HTTPError:
            return False
        return len({x["event_digest"] for x in s}) == 1 and len({x["state_hash"] for x in s}) == 1

    def wait_converged(self, regions: list, timeout: float = 25) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.converged(regions):
                return
            time.sleep(0.3)
        raise AssertionError("regions did not converge: " + str({r: self.status(r)["heads"] for r in regions}))


@pytest.fixture
def make_mesh(tmp_path):
    meshes = []

    def make(regions=("mum", "pun", "del")) -> Mesh:
        m = Mesh(tmp_path)
        meshes.append(m)
        for r in regions:
            m.start(r, [p for p in regions if p != r])
        return m
    yield make
    for m in meshes:
        m.stop()


_client = httpx.Client(timeout=10)   # one client: building a new one per request costs ~300 ms and would hide the limit


def post(region, path, body, key="dev-officer"):
    return _client.post(f"{URL[region]}{path}", json=body, headers={"X-API-Key": key})


def cart(region, op_id, key="dev-officer"):
    return post(region, "/v1/reports", {"title": "Alice", "category": "cart", "severity": 1, "op_id": op_id}, key)


# ---- status blocks and /admin/config -------------------------------------------------------------------
def test_status_carries_load_and_rate_and_admin_config_round_trips(make_mesh):
    m = make_mesh(("mum",))
    s = m.status("mum")
    assert set(s["load"]) == {"writes_per_s", "power", "batch", "queue_depth"}
    assert s["load"]["power"] == "medium" and s["load"]["batch"] == 8
    assert set(s["rate"]) == {"actor_burst", "actor_refill_per_s", "region_limit_per_s", "refused_actor", "refused_region"}
    assert s["rate"]["region_limit_per_s"] == 0 and s["rate"]["refused_region"] == 0

    r = m.config("mum", {"power": "high", "region_write_limit_per_s": 50, "rate_actor_burst": 10,
                         "rate_actor_refill_per_s": 2})
    assert r.status_code == 200
    got = r.json()
    assert got["power"] == "high" and got["batch"] == 32 and got["region_write_limit_per_s"] == 50
    assert got["rate_actor_burst"] == 10 and got["rate_actor_refill_per_s"] == 2.0
    s = m.status("mum")                                            # and the status answer follows
    assert s["load"]["power"] == "high" and s["load"]["batch"] == 32
    assert s["rate"]["region_limit_per_s"] == 50 and s["rate"]["actor_burst"] == 10
    assert m.config("mum", {}).json()["power"] == "high"           # an empty body just reads the current set


@pytest.mark.parametrize("body", [{"power": "turbo"}, {"colour": "red"}, {"region_write_limit_per_s": -1},
                                  {"rate_actor_burst": "ten"}, {"rate_actor_burst": True},
                                  {"power": "low", "colour": "red"}])
def test_bad_config_is_422_and_nothing_is_applied(make_mesh, body):
    m = make_mesh(("mum",))
    assert m.config("mum", body).status_code == 422
    assert m.config("mum", {}).json()["power"] == "medium"        # not even the valid half of a mixed body


# ---- power ---------------------------------------------------------------------------------------------
def test_power_300_writes_at_each_setting_all_present_gap_free_and_identical(make_mesh):
    regions = ["mum", "pun", "del"]
    m = make_mesh(tuple(regions))
    accepted = 0
    seen = {}
    for power in ("low", "medium", "high"):
        for r in regions:
            assert m.config(r, {"power": power}).json()["batch"] == {"low": 1, "medium": 8, "high": 32}[power]
        before = {r: m.config(r, {}).json()["group_commit"] for r in regions}
        out = run_benchmark({r: URL[r] for r in regions}, 300, 24, SHOPPERS, lambda _s: "dev-officer")
        assert out["accepted"] == 300 and out["refused"] == 0 and out["errors"] == 0
        accepted += out["accepted"]
        after = {r: m.config(r, {}).json()["group_commit"] for r in regions}
        seen[power] = (sum(after[r]["writes"] - before[r]["writes"] for r in regions),
                       sum(after[r]["commits"] - before[r]["commits"] for r in regions))
        m.wait_converged(regions)
        stats = [m.status(r) for r in regions]
        for s in stats:
            assert s["event_count"] == accepted                     # all of them are on every region
            assert sum(s["heads"].values()) == accepted             # contiguous heads add up: no gap anywhere
            assert s["quarantined"] == 0
        assert len({s["state_hash"] for s in stats}) == 1           # identical state on every region
    # the dial changes the work: low commits every write by itself, high shares transactions
    assert seen["low"][1] == seen["low"][0]
    assert seen["high"][1] < seen["high"][0]


# ---- region limit --------------------------------------------------------------------------------------
def test_region_limit_refuses_with_503_replays_pass_reads_and_replication_are_untouched(make_mesh):
    m = make_mesh(("mum", "pun"))
    assert cart("mum", "op-before-the-limit").status_code == 201
    assert m.config("mum", {"region_write_limit_per_s": 5}).json()["region_write_limit_per_s"] == 5

    t0 = time.time()
    codes, first = [], None
    for i in range(40):
        r = cart("mum", f"op-spam-{i}")
        codes.append(r.status_code)
        if r.status_code == 503 and first is None:
            first = r
    elapsed = time.time() - t0
    assert codes.count(503) > 0
    assert codes.count(201) <= 5 * (int(elapsed) + 1)               # at most 5 per second got through
    assert first.json() == {"error": "overloaded", "scope": "region"}
    assert first.headers["retry-after"]

    # over the limit right now: a replay of a stored op_id is still answered, and not counted
    replay = cart("mum", "op-before-the-limit")
    assert replay.status_code == 200 and replay.headers.get("idempotent-replay") == "true"
    # reads are never limited
    assert all(_client.get(f"{URL['mum']}/v1/reports").status_code == 200 for _ in range(30))
    assert m.status("mum")["rate"]["refused_region"] == codes.count(503)

    # replication into the limited region keeps working: a write on pun reaches mum although mum is limited
    time.sleep(1.1)
    ok = cart("pun", "op-from-pun")
    assert ok.status_code == 201
    rid = ok.json()["report"]["report_id"]
    end = time.time() + 10
    while time.time() < end and httpx.get(f"{URL['mum']}/v1/reports/{rid}", timeout=3).status_code != 200:
        time.sleep(0.2)
    assert httpx.get(f"{URL['mum']}/v1/reports/{rid}", timeout=3).status_code == 200

    # limit 0 = off: no refusals, no behaviour change
    m.config("mum", {"region_write_limit_per_s": 0})
    assert all(cart("mum", f"op-free-{i}").status_code == 201 for i in range(40))


def test_a_limited_region_still_receives_everything_its_peers_write(make_mesh):
    m = make_mesh(("mum", "pun"))
    m.config("mum", {"region_write_limit_per_s": 1})
    for i in range(15):
        assert cart("pun", f"op-pun-{i}").status_code == 201
    end = time.time() + 12
    while time.time() < end and m.status("mum")["event_count"] < 15:
        time.sleep(0.2)
    assert m.status("mum")["event_count"] == 15                     # nothing the limit does touches /internal
    assert m.status("mum")["rate"]["refused_region"] == 0


# ---- Bengaluru -----------------------------------------------------------------------------------------
def test_bengaluru_joins_a_running_mesh_empty_converges_and_loses_nothing(make_mesh):
    base = ["mum", "pun", "del"]
    m = make_mesh(tuple(base))
    run_benchmark({r: URL[r] for r in base}, 60, 6, SHOPPERS, lambda _s: "dev-officer")
    m.wait_converged(base)
    expected = m.status("mum")["event_count"]
    assert expected == 60

    # blr starts EMPTY under its own id; the running regions learn about it without a restart
    m.start("blr", base)
    for r in base:
        add = httpx.post(f"{URL[r]}/admin/config", json={"add_peer": {"name": "blr", "url": URL["blr"]}},
                         headers=KEY, timeout=3)
        assert add.status_code == 200
    m.wait_converged(base + ["blr"])
    blr = m.status("blr")
    nonzero = lambda h: {o: n for o, n in h.items() if n}   # noqa: E731  (blr lists its own empty head)
    assert blr["event_count"] == expected and nonzero(blr["heads"]) == nonzero(m.status("mum")["heads"])

    # it also writes, and everybody receives that (the others pull from it because it was added at runtime)
    mine = [cart("blr", f"op-blr-{i}") for i in range(10)]
    assert all(r.status_code == 201 for r in mine)
    m.wait_converged(base + ["blr"])
    assert m.status("mum")["heads"].get("blr") == 10

    # killing it afterwards loses nothing: everything it acknowledged is on the others
    m.kill("blr")
    ids = sorted(r.json()["event"]["event_id"] for r in mine)
    survivors = httpx.post(f"{URL['mum']}/v1/events/has", json={"ids": ids}, headers=KEY, timeout=3).json()
    assert survivors["missing"] == []
    assert m.status("mum")["event_count"] == expected + 10


def test_loadgen_spread_and_actor_work_from_the_command_line(make_mesh):
    m = make_mesh(("mum", "pun", "del"))
    cmd = [sys.executable, str(ROOT / "scripts" / "loadgen.py"), "--spread", "mum,pun,del", "-n", "30", "-c", "6"]
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    # --spread targets the demo ports (8001-8003), so point the generator at the test regions instead
    out = subprocess.run([sys.executable, "-c",
                          "import sys; sys.path.insert(0, 'scripts'); import loadgen; "
                          f"loadgen.PORTS.update({PORTS!r}); sys.argv = {cmd[1:]!r}; loadgen.main()"],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert "By region:" in out.stdout
    m.wait_converged(["mum", "pun", "del"])
    assert m.status("mum")["event_count"] == 30
    # --actor sends one shopper's own key
    cmd = [sys.executable, str(ROOT / "scripts" / "loadgen.py"), "--url", URL["mum"], "--actor", "Bob", "-n", "5"]
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and "Accepted:    5" in out.stdout
    assert shopper_key("Bob") == "dev-bob"
