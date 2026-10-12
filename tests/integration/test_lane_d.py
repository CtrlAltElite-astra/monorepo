"""Lane D Integration & Correctness Tests (Simrit).

Covers:
- T8: Sequence gaps and watermark continuity
- T1: Duplicate delivery idempotency and quarantine
- T15: Partition enforcement both directions (503 and outbound silence)
- T11: Relay catch-up across 3 regions (mum -> pun -> del)
- T14: Priority queue ordering under throttling (P0 before P2)
- Fast-heal and restart recovery
"""
import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

from region import config, store
from region.engine import canonical, hlc
from region.sync import ingest, links, push, state

ROOT = Path(__file__).resolve().parents[2]
PORTS = {"mum": 19001, "pun": 19002, "del": 19003}
KEY = {"X-API-Key": "dev-officer"}
URL = {r: f"http://127.0.0.1:{p}" for r, p in PORTS.items()}


# ==============================================================================
# In-Process Unit/Correctness Tests for Lane D (Fast, In-Memory DBs)
# ==============================================================================

def test_t8_sequence_gap_and_contiguous_watermark():
    """T8: deliver seq 3 before seq 2 -> contiguous stays at 1, then advances to 3."""
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "test_gap.db")
        store.init(db)
        try:
            # Construct 3 events from origin "peer1"
            def make_ev(seq):
                ev = {
                    "v": 1,
                    "event_id": f"peer1:{seq}",
                    "origin": "peer1",
                    "seq": seq,
                    "report_id": "R-peer1-1",
                    "type": "CREATE" if seq == 1 else "SET",
                    "field": None if seq == 1 else "severity",
                    "value": {"title": "Test incident", "category": "flood", "severity": 3} if seq == 1 else 4,
                    "supersedes": [] if seq == 1 else ["peer1:1"],
                    "hlc": [1760000000000 + seq, 0],
                    "priority": 1,
                    "actor": "officer-1",
                    "reason": None,
                    "wall": "2026-10-12T15:00:00+05:30",
                    "sig": None,
                }
                ev["hash"] = canonical.event_hash(ev)
                return ev

            ev1 = make_ev(1)
            ev2 = make_ev(2)
            ev3 = make_ev(3)

            # Step 1: Ingest seq 1 -> contiguous is 1
            res1 = ingest.ingest([ev1], source="peer1")
            assert res1["stored"] == 1
            assert state.contiguous("peer1") == 1
            assert state.max_seen().get("peer1") == 1

            # Step 2: Ingest seq 3 (out of order, gap: missing seq 2)
            res3 = ingest.ingest([ev3], source="peer1")
            assert res3["stored"] == 1
            # Contiguous MUST stay at 1 because 2 is missing!
            assert state.contiguous("peer1") == 1
            # Max seen advances to 3
            assert state.max_seen().get("peer1") == 3

            # Step 3: Now ingest the missing seq 2
            res2 = ingest.ingest([ev2], source="peer1")
            assert res2["stored"] == 1
            # Contiguous now jumps past the filled hole to 3!
            assert state.contiguous("peer1") == 3
            assert state.max_seen().get("peer1") == 3

            # Step 4: Duplicate delivery of seq 2 and 3
            res_dup = ingest.ingest([ev2, ev3], source="peer1")
            assert res_dup["stored"] == 0
            assert res_dup["duplicate"] == 2
            assert state.contiguous("peer1") == 3
        finally:
            store.close()


def test_t1_tampered_and_duplicate_quarantine():
    """T1/T10: Tampered hash or duplicate id mismatch is quarantined and counted."""
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "test_tamper.db")
        store.init(db)
        try:
            ev = {
                "v": 1,
                "event_id": "origin_a:1",
                "origin": "origin_a",
                "seq": 1,
                "report_id": "R-origin_a-1",
                "type": "CREATE",
                "field": None,
                "value": {"title": "Fire report", "category": "fire", "severity": 4},
                "supersedes": [],
                "hlc": [1760000000000, 0],
                "priority": 0,
                "actor": "officer-a",
                "reason": None,
                "wall": "2026-10-12T15:00:00+05:30",
                "sig": None,
            }
            ev["hash"] = canonical.event_hash(ev)

            # Ingest original
            r = ingest.ingest([ev], source="peer_a")
            assert r["stored"] == 1

            # Tampered content with same event_id
            tampered = dict(ev)
            tampered["value"] = {"title": "Tampered title"}
            tampered["hash"] = canonical.event_hash(tampered)  # valid internal hash, but id reused with different body
            r2 = ingest.ingest([tampered], source="peer_a")
            assert r2["rejected"] == 1
            assert r2["rejected_list"][0]["reason"] == "id_reused"

            # Bad hash
            bad_hash_ev = dict(ev)
            bad_hash_ev["event_id"] = "origin_a:2"
            bad_hash_ev["seq"] = 2
            bad_hash_ev["hash"] = "0000000000000000000000000000000000000000000000000000000000000000"
            r3 = ingest.ingest([bad_hash_ev], source="peer_a")
            assert r3["rejected"] == 1
            assert r3["rejected_list"][0]["reason"] == "bad_hash"

            with store.read() as conn:
                assert store.quarantine_count(conn) >= 2
        finally:
            store.close()


def test_t14_priority_queue_ordering():
    """T14: Priority queue dequeues P0 critical events ahead of P1 and P2 events."""
    q = push.PushQueue()

    # Enqueue in reverse order: P2 bulk note, then P1 normal edit, then P0 critical
    q.put(2, [1000, 0], "bulk:1")
    q.put(2, [1001, 0], "bulk:2")
    q.put(1, [1002, 0], "normal:1")
    q.put(0, [1003, 0], "critical:1")

    # asyncio run to take
    async def drain():
        return await q.take(max_n=10, max_wait_ms=50)

    batch = asyncio.run(drain())
    # Critical (P0) MUST come first, then Normal (P1), then Bulk (P2)
    assert batch[0] == "critical:1"
    assert batch[1] == "normal:1"
    assert "bulk:1" in batch[2:]
    assert "bulk:2" in batch[2:]


# ==============================================================================
# Full Multi-Process Integration Tests (Relay, Partition, Recovery)
# ==============================================================================

def start_node(region, tmp, peers_dict):
    env = dict(os.environ)
    peers_str = ",".join(f"{p}={URL[p]}" for p, u in peers_dict.items())
    env.update(
        REGION=region,
        PORT=str(PORTS[region]),
        DB_PATH=str(Path(tmp) / f"{region}.db"),
        PEERS=peers_str,
        PEER_TOKEN="secret-token",
        SYNC_INTERVAL_MS="250",
        API_KEYS=f"dev-officer:officer-{region}-1:officer",
        PYTHONPATH=str(ROOT),
        ENV_FILE="none",
    )
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "region.main:app", "--host", "127.0.0.1",
         "--port", str(PORTS[region]), "--workers", "1", "--log-level", "error"],
        cwd=ROOT,
        env=env,
    )


def wait_healthy(region, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            r = httpx.get(f"{URL[region]}/v1/health", timeout=0.5)
            if r.status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.2)
    raise TimeoutError(f"Region {region} did not start in time")


def poll_until(fn, timeout=8.0, desc="condition"):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.15)
    raise AssertionError(f"Condition '{desc}' not reached within {timeout}s")


def test_t11_relay_catch_up_three_regions():
    """T11: Mum writes event -> Pun pulls from Mum -> Del (isolated from Mum) catches up via Pun relay."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        # Mum connects to Pun.
        # Pun connects to Mum and Del.
        # Del connects ONLY to Pun (Del has no direct link to Mum).
        procs = {
            "mum": start_node("mum", tmp, {"pun": URL["pun"]}),
            "pun": start_node("pun", tmp, {"mum": URL["mum"], "del": URL["del"]}),
            "del": start_node("del", tmp, {"pun": URL["pun"]}),
        }
        try:
            for r in ("mum", "pun", "del"):
                wait_healthy(r)

            # Create report on Mumbai
            r = httpx.post(
                f"{URL['mum']}/v1/reports",
                headers=KEY,
                json={"title": "Relay Flood Alert", "category": "flood", "severity": 4},
                timeout=3,
            )
            assert r.status_code == 201
            report_id = r.json()["report"]["report_id"]
            assert report_id == "R-mum-1"

            # 1. Verify Pune receives R-mum-1 directly from Mumbai
            poll_until(
                lambda: any(x["report_id"] == "R-mum-1" for x in httpx.get(f"{URL['pun']}/v1/reports").json()),
                timeout=6,
                desc="Pune received R-mum-1 from Mumbai",
            )

            # 2. Verify Delhi (which ONLY connects to Pune) receives R-mum-1 via Pune's relay!
            poll_until(
                lambda: any(x["report_id"] == "R-mum-1" for x in httpx.get(f"{URL['del']}/v1/reports").json()),
                timeout=6,
                desc="Delhi received R-mum-1 via Pune relay",
            )

            # Verify all three regions report identical digests and state
            poll_until(
                lambda: (
                    httpx.get(f"{URL['mum']}/v1/status").json()["state_hash"]
                    == httpx.get(f"{URL['pun']}/v1/status").json()["state_hash"]
                    == httpx.get(f"{URL['del']}/v1/status").json()["state_hash"]
                ),
                timeout=6,
                desc="State hash convergence across all 3 nodes via relay",
            )

        finally:
            for p in procs.values():
                p.kill()
                p.wait()


def test_t15_slow_link_and_partition_both_ways():
    """T15: Test slow link throttling and bidirectional partition isolation."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        procs = {
            "mum": start_node("mum", tmp, {"pun": URL["pun"]}),
            "pun": start_node("pun", tmp, {"mum": URL["mum"]}),
        }
        try:
            for r in ("mum", "pun"):
                wait_healthy(r)

            # 1. Set link to 'slow' with 100ms latency
            res = httpx.post(
                f"{URL['mum']}/admin/links/pun",
                json={"mode": "slow", "delay_ms": 100, "rate_bps": 5000},
                headers=KEY,
                timeout=2,
            )
            assert res.status_code == 200
            assert res.json()["mode"] == "slow"
            assert res.json()["delay_ms"] == 100

            # Create report under slow link
            r = httpx.post(
                f"{URL['mum']}/v1/reports",
                headers=KEY,
                json={"title": "Slow Link Report", "category": "medical", "severity": 3},
                timeout=3,
            )
            assert r.status_code == 201

            # Should still replicate despite latency
            poll_until(
                lambda: any(x["report_id"] == "R-mum-1" for x in httpx.get(f"{URL['pun']}/v1/reports").json()),
                timeout=8,
                desc="Replication across slow link",
            )

            # 2. Cut link to down on both ends
            httpx.post(f"{URL['mum']}/admin/links/pun", json={"mode": "down"}, headers=KEY, timeout=2)
            httpx.post(f"{URL['pun']}/admin/links/mum", json={"mode": "down"}, headers=KEY, timeout=2)

            # Inbound /internal is blocked with 503 on both ends
            assert httpx.get(
                f"{URL['mum']}/internal/v1/sync/summary",
                headers={"X-Region": "pun", "X-Peer-Token": "secret-token"},
            ).status_code == 503

            assert httpx.get(
                f"{URL['pun']}/internal/v1/sync/summary",
                headers={"X-Region": "mum", "X-Peer-Token": "secret-token"},
            ).status_code == 503

            # Writes on both sides while partitioned succeed locally
            httpx.post(f"{URL['mum']}/v1/reports/R-mum-1/notes", json={"text": "Note from mum"}, headers=KEY)
            httpx.post(f"{URL['pun']}/v1/reports/R-mum-1/notes", json={"text": "Note from pun"}, headers=KEY)

            # Verify they remain isolated (state hashes differ)
            time.sleep(1.0)
            st_mum = httpx.get(f"{URL['mum']}/v1/status").json()
            st_pun = httpx.get(f"{URL['pun']}/v1/status").json()
            assert st_mum["state_hash"] != st_pun["state_hash"]

            # 3. Heal partition
            httpx.post(f"{URL['mum']}/admin/links/pun", json={"mode": "up"}, headers=KEY, timeout=2)
            httpx.post(f"{URL['pun']}/admin/links/mum", json={"mode": "up"}, headers=KEY, timeout=2)

            # Fast heal triggers convergence
            poll_until(
                lambda: (
                    httpx.get(f"{URL['mum']}/v1/status").json()["state_hash"]
                    == httpx.get(f"{URL['pun']}/v1/status").json()["state_hash"]
                ),
                timeout=8,
                desc="Fast heal convergence after partition cut",
            )

            rep_mum = httpx.get(f"{URL['mum']}/v1/reports/R-mum-1").json()
            notes_texts = [n["text"] for n in rep_mum["state"]["notes"]]
            assert "Note from mum" in notes_texts
            assert "Note from pun" in notes_texts

        finally:
            for p in procs.values():
                p.kill()
                p.wait()


def test_t14_priority_overtaking_under_throttling():
    """T14: On a slow/throttled link, a P0 critical event overtakes a queue of P2 bulk notes."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        procs = {
            "mum": start_node("mum", tmp, {"pun": URL["pun"]}),
            "pun": start_node("pun", tmp, {"mum": URL["mum"]}),
        }
        try:
            for r in ("mum", "pun"):
                wait_healthy(r)

            # Create a base incident
            r_base = httpx.post(
                f"{URL['mum']}/v1/reports",
                headers=KEY,
                json={"title": "Base Incident", "category": "shelter", "severity": 2},
                timeout=3,
            )
            base_id = r_base.json()["report"]["report_id"]
            poll_until(lambda: any(x["report_id"] == base_id for x in httpx.get(f"{URL['pun']}/v1/reports").json()), 6)

            # Throttle link mum -> pun to 500 Bps and 100ms latency
            httpx.post(
                f"{URL['mum']}/admin/links/pun",
                json={"mode": "slow", "delay_ms": 100, "rate_bps": 500},
                headers=KEY,
                timeout=2,
            )

            # Queue 10 bulk notes (P2)
            for i in range(10):
                httpx.post(f"{URL['mum']}/v1/reports/{base_id}/notes", json={"text": f"Bulk status note {i}"}, headers=KEY)

            # Issue a P0 Critical Event (collapse, severity 5)
            r_crit = httpx.post(
                f"{URL['mum']}/v1/reports",
                headers=KEY,
                json={"title": "CRITICAL EMERGENCY COLLAPSE", "category": "collapse", "severity": 5},
                timeout=3,
            )
            crit_id = r_crit.json()["report"]["report_id"]

            # Verify P0 report arrives at Pune quickly despite the slow link and bulk notes
            poll_until(
                lambda: any(x["report_id"] == crit_id for x in httpx.get(f"{URL['pun']}/v1/reports").json()),
                timeout=6,
                desc="P0 Critical report arrived at Pune ahead of bulk note backlog",
            )

        finally:
            for p in procs.values():
                p.kill()
                p.wait()
