"""Chaos agent (owner: Miti). Kills and starts region processes from the UI.

    python scripts/chaos_agent.py            # listens on 127.0.0.1:9000

    POST /crash/{region}   hard-kill the region process (SIGKILL; TerminateProcess on Windows)
    POST /start/{region}   start it again with scripts/run_one.py settings
    GET  /state            {regions: {mum: {up, pid, port, managed}, ...}}
    POST /tamper/{region}  {"mode": "edit" | "forge" | "reuse"}  inject a bad event through the peer path
                           and report whether it was quarantined and whether state changed

Every call needs the header  X-Admin-Key: <ADMIN_KEY>  (default "dev-admin").
Demo and test tool only. NEVER expose this on a public deployment.

Env: CHAOS_PORT (9000), ADMIN_KEY, CORS_ORIGINS (comma list, default the Vite dev
server), REGIONS (comma list, default "mum,pun").
Pid files live in run/<region>.pid. If a region was started by something else
(run_regions.py), the agent finds the pid by the port it listens on.
"""
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_one import PORTS, ROOT, spawn  # noqa: E402
import join_region  # noqa: E402

OPTIONAL_REGIONS = {"blr"}   # joins a running mesh when someone adds it; until then it is not anybody's peer

RUN_DIR = ROOT / "run"
ADMIN_KEY = os.environ.get("ADMIN_KEY", "dev-admin")
REGIONS = [r.strip() for r in os.environ.get("REGIONS", "mum,pun").split(",") if r.strip()]
ORIGINS = [o.strip() for o in os.environ.get(
    "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()]

_children: dict = {}  # region -> Popen started by this agent (so we can reap it)


def port_open(port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def pid_from_port(port: int):
    """Find the process listening on a TCP port, or None."""
    try:
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True).stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
                    return int(parts[4])
        else:
            out = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True).stdout
            if out.split():
                return int(out.split()[0])
    except (OSError, ValueError):
        pass
    return None


def read_pidfile(region: str):
    try:
        return int((RUN_DIR / f"{region}.pid").read_text().strip())
    except (OSError, ValueError):
        return None


def region_pid(region: str):
    pid = read_pidfile(region)
    if pid and pid_alive(pid) and port_open(PORTS[region]):
        return pid
    return pid_from_port(PORTS[region])


def hard_kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    else:
        os.kill(pid, signal.SIGKILL)


def mesh_for(region: str) -> list:
    """The regions that are part of the mesh when `region` starts: every configured one, except an optional region
    (Bengaluru) that is not running. An optional region that has not joined must not show up as an unreachable peer."""
    return [r for r in REGIONS if r not in OPTIONAL_REGIONS or r == region or port_open(PORTS[r])]


def _announce_when_up(region: str, mesh: list, proc, patience_s: float = 120.0) -> None:
    """Tell the running regions about `region` as soon as it answers (same call as scripts/join_region.py)."""
    end = time.time() + patience_s
    while time.time() < end and proc.poll() is None:
        if port_open(PORTS[region]):
            join_region.notify_existing_nodes(region, PORTS[region], [r for r in mesh if r != region and port_open(PORTS[r])])
            return
        time.sleep(0.25)


def check_region(region: str) -> None:
    if region not in PORTS or region not in REGIONS:
        raise HTTPException(404, f"unknown region '{region}' (known: {', '.join(REGIONS)})")


def require_admin(key) -> None:
    if key != ADMIN_KEY:
        raise HTTPException(401, "missing or wrong X-Admin-Key")


app = FastAPI(title="SyncShield chaos agent", version="1")
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["*"], allow_headers=["*"])


def region_state(region: str) -> dict:
    port = PORTS[region]
    up = port_open(port)
    return {"region": region, "port": port, "up": up, "pid": region_pid(region) if up else None,
            "managed": region in _children and _children[region].poll() is None}


@app.get("/state")
def state(x_admin_key: str = Header(None)):
    require_admin(x_admin_key)
    return {"regions": {r: region_state(r) for r in REGIONS}, "ts": int(time.time() * 1000)}


@app.post("/crash/{region}")
def crash(region: str, x_admin_key: str = Header(None)):
    require_admin(x_admin_key)
    check_region(region)
    pid = region_pid(region)
    if pid is None:
        return {"region": region, "was_running": False, "killed_pid": None}
    hard_kill(pid)
    child = _children.pop(region, None)
    if child is not None:
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
    # wait briefly so callers see a consistent state
    end = time.time() + 3
    while time.time() < end and port_open(PORTS[region]):
        time.sleep(0.05)
    (RUN_DIR / f"{region}.pid").unlink(missing_ok=True)
    return {"region": region, "was_running": True, "killed_pid": pid, "up": port_open(PORTS[region])}


@app.post("/start/{region}")
def start(region: str, x_admin_key: str = Header(None)):
    require_admin(x_admin_key)
    check_region(region)
    if port_open(PORTS[region]):
        return {"region": region, "already_running": True, "pid": region_pid(region)}
    child = _children.get(region)
    if child is not None and child.poll() is None:   # already being started (a second press, or an automatic start): do not spawn twice
        return {"region": region, "already_running": False, "already_starting": True, "pid": child.pid, "up": False}
    mesh = mesh_for(region)
    try:
        proc = spawn(region, mesh)
    except Exception as exc:  # noqa: BLE001 - say why (for example: no sign-in sidecar for this region), never a bare 500
        raise HTTPException(500, f"could not start {region}: {type(exc).__name__}: {exc}")
    _children[region] = proc
    RUN_DIR.mkdir(exist_ok=True)
    (RUN_DIR / f"{region}.pid").write_text(str(proc.pid))
    end = time.time() + 8
    while time.time() < end and not port_open(PORTS[region]):
        if proc.poll() is not None:
            raise HTTPException(500, f"region process exited with code {proc.returncode}")
        time.sleep(0.1)
    up = port_open(PORTS[region])
    if region in OPTIONAL_REGIONS:
        # A region joining a running mesh: the others learn about it without a restart. This waits for the region to answer in
        # the background (in sign-in mode it can take longer than the 8 s above), so the request does not have to.
        threading.Thread(target=_announce_when_up, args=(region, mesh, proc), daemon=True).start()
    return {"region": region, "already_running": False, "pid": proc.pid, "up": up}


@app.post("/tamper/{region}")
def tamper(region: str, body: dict = Body(default={}), x_admin_key: str = Header(None)):
    """Dev-only. Sends ONE bad event to `region` as if a peer had pushed it, then reports what happened.

    edit   a real event with its content changed          -> bad_hash
    forge  a well-formed event signed with the wrong key  -> bad_sig
    reuse  a different event under an existing event id   -> id_reused
    The point of the demo: the event is quarantined and the node's state does not change.
    """
    require_admin(x_admin_key)
    check_region(region)
    mode = (body or {}).get("mode", "edit")
    if mode not in ("edit", "forge", "reuse"):
        raise HTTPException(422, "mode must be edit, forge or reuse")
    peers = [r for r in REGIONS if r != region]
    if not peers:
        raise HTTPException(409, "needs at least two regions")
    peer, base = peers[0], f"http://127.0.0.1:{PORTS[region]}"
    hdr = {"X-Region": peer, "X-Peer-Token": os.environ.get("PEER_TOKEN", "change-me")}
    sys.path.insert(0, str(ROOT))
    from region.engine import canonical, signing
    try:
        with httpx.Client(timeout=3) as c:
            before = c.get(base + "/v1/status").json()
            got = c.get(base + "/internal/v1/sync/events", params={"origin": region, "after": 0, "limit": 1}, headers=hdr)
            if got.status_code != 200:
                raise HTTPException(409, f"peer path refused ({got.status_code}); is the {peer}-{region} link up?")
            events = got.json()["events"]
            if not events:
                raise HTTPException(409, f"{region} has no events yet: add something first")
            ev = dict(events[0])
            if mode == "edit":
                bad = dict(ev, value=({**ev["value"], "title": "TAMPERED"} if isinstance(ev["value"], dict) else "TAMPERED"))
            elif mode == "reuse":
                bad = dict(ev, value="a different event under the same id", wall="tamper")
                bad["hash"] = canonical.event_hash(bad)
                bad["sig"] = None
            else:  # forge: valid hash, signed with THIS node's key but claiming to be the peer's event
                seq = int(before["heads"].get(peer, 0)) + 1
                bad = dict(ev, event_id=f"{peer}:{seq}", origin=peer, seq=seq, report_id="R-forged", type="NOTE",
                           field=None, value={"text": "forged"}, supersedes=[], op_id=None, sig=None)
                bad["hash"] = canonical.event_hash(bad)
                bad["sig"] = signing.sign(region, bad["hash"]) or "AAAA"
            pushed = c.post(base + "/internal/v1/sync/push", json={"from": peer, "events": [bad]}, headers=hdr)
            after = c.get(base + "/v1/status").json()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"could not reach {region}: {e}")
    rejected = (pushed.json() or {}).get("rejected", []) if pushed.status_code == 200 else []
    return {
        "region": region, "mode": mode, "event_id": bad["event_id"], "push_status": pushed.status_code,
        "rejected": rejected,
        "quarantined_before": before["quarantined"], "quarantined_after": after["quarantined"],
        "state_hash_before": before["state_hash"], "state_hash_after": after["state_hash"],
        "event_count_before": before["event_count"], "event_count_after": after["event_count"],
        "state_unchanged": before["state_hash"] == after["state_hash"] and before["event_count"] == after["event_count"],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("CHAOS_PORT", "9000")), log_level="warning")
