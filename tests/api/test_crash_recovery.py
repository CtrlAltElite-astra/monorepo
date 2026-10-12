"""kill -9 safety (owner: Akshit). PDF 2 section 6.7 and test T9.

A REAL region process is hard-killed while four clients are writing. Every write that was
acknowledged (201) must be in the database afterwards, the sequence must be gap-free and must
continue (never reuse a number), views must match events, and the clock must not go backwards.
Popen.kill() is TerminateProcess on Windows and SIGKILL elsewhere: no shutdown code runs.
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
KEY = {"X-API-Key": "dev-officer"}


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start(db, port) -> subprocess.Popen:
    env = dict(os.environ, REGION="mum", PORT=str(port), DB_PATH=str(db), PEER_TOKEN="t", ENV_FILE="none",
               API_KEYS="dev-officer:officer-mum-1:officer", PYTHONPATH=str(ROOT))
    env.pop("PEERS", None)  # a single region: nothing to sync with
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "region.main:app", "--host", "127.0.0.1", "--port", str(port),
         "--workers", "1", "--log-level", "error"], cwd=ROOT, env=env)


def wait_up(url, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(url + "/v1/health", timeout=0.5).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.2)
    raise TimeoutError(url)


def read_db(db):
    """What a restarted process would find. sqlite replays the WAL on this first open."""
    for attempt in range(20):
        try:
            con = sqlite3.connect(db)
            events = con.execute("SELECT event_id, seq, type, hlc_l, hlc_c FROM events ORDER BY seq").fetchall()
            views = con.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
            next_seq = int(con.execute("SELECT v FROM node_meta WHERE k='next_seq'").fetchone()[0])
            con.close()
            return events, views, next_seq
        except sqlite3.OperationalError:
            time.sleep(0.05)
    raise RuntimeError("Failed to read db after kill")



def test_hard_kill_during_writes_loses_no_acknowledged_event_and_the_sequence_continues(tmp_path):
    db = tmp_path / "mum.db"
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    proc = start(db, port)
    acked, lock, stop = [], threading.Lock(), threading.Event()

    def writer(n):
        with httpx.Client(timeout=3) as c:
            i = 0
            while not stop.is_set():
                try:
                    r = c.post(url + "/v1/reports", headers=KEY,
                               json={"title": f"w{n}-{i}", "category": "flood", "severity": 2})
                except httpx.HTTPError:
                    return  # the server was killed under this request: it was never acknowledged
                if r.status_code == 201:
                    with lock:
                        acked.append(r.json()["event_id"])
                i += 1

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    try:
        wait_up(url)
        for t in threads:
            t.start()
        end = time.time() + 20
        while len(acked) < 200 and time.time() < end:
            time.sleep(0.02)
        assert len(acked) >= 200, "the writers never got going"
        proc.kill()  # hard kill while the writers are still mid-request
        proc.wait()
    finally:
        stop.set()
        proc.kill()
        for t in threads:
            t.join(10)

    events, views, next_seq = read_db(db)
    stored = {e[0] for e in events}
    assert set(acked) <= stored, "an acknowledged write was lost"
    seqs = [e[1] for e in events]
    assert seqs == list(range(1, len(seqs) + 1)), "the sequence has a hole after the crash"
    assert next_seq == len(seqs) + 1, "next_seq must follow the last committed event"
    assert views == sum(1 for e in events if e[2] == "CREATE"), "a view is missing or extra: event and view must commit together"
    last_clock = max((e[3], e[4]) for e in events)

    # restart on the same file: the sequence continues and the clock moves forward
    port2 = free_port()
    url2 = f"http://127.0.0.1:{port2}"
    proc2 = start(db, port2)
    try:
        wait_up(url2)
        r = httpx.post(url2 + "/v1/reports", headers=KEY, timeout=3,
                       json={"title": "after the crash", "category": "fire", "severity": 3})
        assert r.status_code == 201
        assert r.json()["event_id"] == f"mum:{len(seqs) + 1}"  # never reuses a number
        assert httpx.get(url2 + "/v1/status", timeout=3).json()["heads"] == {"mum": len(seqs) + 1}
        new_event = httpx.get(f"{url2}/v1/reports/{r.json()['report']['report_id']}/history", timeout=3).json()[0]
        assert tuple(new_event["hlc"]) > last_clock, "the clock went backwards across the crash"
        # a sample of acknowledged reports is readable through the API after the restart
        for event_id in acked[-20:]:
            rid = f"R-mum-{event_id.split(':')[1]}"
            assert httpx.get(f"{url2}/v1/reports/{rid}", timeout=3).status_code == 200
    finally:
        proc2.kill()
        proc2.wait()
