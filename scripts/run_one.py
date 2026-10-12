"""Run ONE region in the foreground.   python scripts/run_one.py pun

Region table: mum :8001, pun :8002, del :8003. Peers = the other regions in
REGIONS (env, default "mum,pun"; set REGIONS=mum,pun,del in Phase 3).
Always one uvicorn worker (sequence numbers are guarded by an in-process lock).
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORTS = {"mum": 8001, "pun": 8002, "del": 8003}


def region_env(region: str, regions: list) -> dict:
    env = dict(os.environ)
    env.update(
        REGION=region,
        PORT=str(PORTS[region]),
        DB_PATH=str(ROOT / "data" / f"{region}.db"),
        PEERS=",".join(f"{r}=http://127.0.0.1:{PORTS[r]}" for r in regions if r != region),
        PEER_TOKEN=os.environ.get("PEER_TOKEN", "change-me"),
        API_KEYS=f"dev-officer:officer-{region}-1:officer",
        PYTHONPATH=str(ROOT),
    )
    return env


def spawn(region: str, regions: list) -> subprocess.Popen:
    (ROOT / "data").mkdir(exist_ok=True)
    # every node signs the events it originates; make sure keys and the public registry exist (dev only)
    sys.path.insert(0, str(ROOT))
    from region.engine import signing
    signing.ensure_keys(list(PORTS))
    cmd = [sys.executable, "-m", "uvicorn", "region.main:app", "--host", "127.0.0.1",
           "--port", str(PORTS[region]), "--workers", "1", "--log-level", "warning"]
    return subprocess.Popen(cmd, cwd=ROOT, env=region_env(region, regions))


if __name__ == "__main__":
    region = sys.argv[1]
    regions = os.environ.get("REGIONS", "mum,pun").split(",")
    p = spawn(region, regions)
    try:
        sys.exit(p.wait())
    except KeyboardInterrupt:
        p.terminate()
