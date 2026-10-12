"""Delhi / New Node Join Script (owner: Simrit).

Usage:
    python scripts/join_region.py [del]

1. Hot-reloads PEERS on existing running nodes (mum :8001, pun :8002) to include the joining region.
2. Starts the new empty region node in the background or foreground.
3. Node begins empty and pulls all origin histories from reachable peers until CONVERGED.
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.run_one import PORTS, spawn  # noqa: E402


def notify_existing_nodes(region: str, port: int) -> None:
    """Notify already running nodes via /admin/config to add this peer (hot reload)."""
    node_url = f"http://127.0.0.1:{port}"
    for peer, p_port in PORTS.items():
        if peer == region:
            continue
        try:
            r = httpx.post(
                f"http://127.0.0.1:{p_port}/admin/config",
                headers={"X-API-Key": "dev-officer"},
                json={"add_peer": {"name": region, "url": node_url}},
                timeout=1.0,
            )
            if r.status_code == 200:
                print(f"Notified {peer} (: {p_port}) to add peer {region} ({node_url})")
        except Exception:
            pass  # Peer might not be running yet


def join(region: str = "del") -> None:
    port = PORTS.get(region, 8003)
    existing_regions = [r for r in ("mum", "pun", "del") if r != region]

    print(f"Joining region '{region}' on port {port}...")
    notify_existing_nodes(region, port)

    # Spawn the new region process
    all_regions = ["mum", "pun", "del"]
    p = spawn(region, all_regions)
    print(f"Region {region} started (PID {p.pid}). Pulling all origin events from peers...")

    try:
        sys.exit(p.wait())
    except KeyboardInterrupt:
        p.terminate()


if __name__ == "__main__":
    reg = sys.argv[1] if len(sys.argv) > 1 else "del"
    join(reg)
