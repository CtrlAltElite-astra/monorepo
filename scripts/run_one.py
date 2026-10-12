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
        API_KEYS=",".join((f"dev-officer:officer-{region}-1:officer", f"dev-supervisor:supervisor-{region}-1:supervisor",
                           f"dev-admin:admin-{region}-1:admin")),  # the web console's live mode uses all three
        PYTHONPATH=str(ROOT),
    )
    # Opt-in TRUE credential sessions: AUTH_ENABLED=true python scripts/run_one.py mum
    # (needs `python scripts/true_stack.py up --provision` first; the ports are the fixed demo ones).
    if os.environ.get("AUTH_ENABLED", "").lower() in ("1", "true", "yes"):
        sys.path.insert(0, str(ROOT / "scripts"))
        from true_stack import Stack
        env.update(Stack().region_env(region))
    return env


def check_empty_db_guard(region: str) -> None:
    """Refuse to start an empty DB under a known region id (Section 6.10).
    A wiped DB starting fresh would reuse sequence numbers, corrupting watermarks.
    """
    if os.environ.get("ALLOW_EMPTY_DB", "0") == "1":
        return
    db_file = ROOT / "data" / f"{region}.db"
    import sqlite3
    own_has_events = False
    if db_file.is_file() and db_file.stat().st_size > 0:
        try:
            con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
            cnt = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            con.close()
            if cnt > 0:
                own_has_events = True
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            pass

    if own_has_events:
        return  # Own database has events, safe to start and resume sequence

    # If own DB has NO events, check if any peer database already has recorded events for this origin
    data_dir = ROOT / "data"
    if data_dir.exists():
        for other_db in data_dir.glob("*.db"):
            if other_db.name == f"{region}.db":
                continue
            try:
                con = sqlite3.connect(f"file:{other_db}?mode=ro", uri=True)
                row = con.execute("SELECT contiguous, max_seen FROM origin_progress WHERE origin=?", (region,)).fetchone()
                if row and (row[0] > 0 or row[1] > 0):
                    con.close()
                    raise RuntimeError(
                        f"Refusing to start empty DB for known region '{region}'. "
                        f"Peer DB '{other_db.name}' recorded progress for origin '{region}' (contiguous={row[0]}, max_seen={row[1]}). "
                        f"Sequence numbers would be reused! (PDF 2 Section 6.10)"
                    )
                ev_row = con.execute("SELECT 1 FROM events WHERE origin=? LIMIT 1", (region,)).fetchone()
                con.close()
                if ev_row:
                    raise RuntimeError(
                        f"Refusing to start empty DB for known region '{region}'. "
                        f"Peer DB '{other_db.name}' holds events from origin '{region}'. "
                        f"Sequence numbers would be reused! (PDF 2 Section 6.10)"
                    )
            except sqlite3.OperationalError:
                pass


def spawn(region: str, regions: list) -> subprocess.Popen:
    check_empty_db_guard(region)
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "run").mkdir(exist_ok=True)
    # every node signs the events it originates; make sure keys and the public registry exist (dev only)
    sys.path.insert(0, str(ROOT))
    from region.engine import signing
    signing.ensure_keys(list(PORTS))
    cmd =[sys.executable, "-m", "uvicorn", "region.main:app", "--host", "127.0.0.1",
           "--port", str(PORTS[region]), "--workers", "1", "--log-level", "warning"]
    p = subprocess.Popen(cmd, cwd=ROOT, env=region_env(region, regions))
    pid_file = ROOT / "run" / f"{region}.pid"
    pid_file.write_text(str(p.pid), encoding="utf-8")
    return p


if __name__ == "__main__":
    region = sys.argv[1]
    regions = os.environ.get("REGIONS", "mum,pun").split(",")
    p = spawn(region, regions)
    try:
        sys.exit(p.wait())
    except KeyboardInterrupt:
        p.terminate()

