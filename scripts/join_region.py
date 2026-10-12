"""Delhi / New Node Join Script (owner: Simrit).

Usage:
    python scripts/join_region.py [del]

1. Hot-reloads PEERS on the regions that are running (any of mum, pun, del, blr) to include the joining region.
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


def is_up(port: int) -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{port}/v1/health", timeout=0.5).status_code == 200
    except httpx.HTTPError:
        return False


def running_regions(exclude: str) -> list:
    """Regions of the port table that answer right now, in table order."""
    return [r for r, port in PORTS.items() if r != exclude and is_up(port)]


def notify_existing_nodes(region: str, port: int, peers: list) -> None:
    """Tell the running nodes (via /admin/config) to add the joining region as a peer, without a restart."""
    node_url = f"http://127.0.0.1:{port}"
    for peer in peers:
        try:
            r = httpx.post(
                f"http://127.0.0.1:{PORTS[peer]}/admin/config",
                headers={"X-API-Key": "dev-officer"},
                json={"add_peer": {"name": region, "url": node_url}},
                timeout=1.0,
            )
            if r.status_code == 200:
                print(f"Notified {peer} (:{PORTS[peer]}) to add peer {region} ({node_url})")
        except Exception:
            pass


def join(region: str = "del") -> None:
    if region not in PORTS:
        sys.exit(f"unknown region '{region}' (known: {', '.join(PORTS)})")
    port = PORTS[region]
    peers = running_regions(exclude=region)
    if not peers:
        print("No other region is running; starting alone (it will sync as soon as peers appear).")

    print(f"Joining region '{region}' on port {port} with peers {peers or '-'}...")
    notify_existing_nodes(region, port, peers)

    # The new node starts empty and pulls every origin from the regions that are up.
    p = spawn(region, peers + [region])
    print(f"Region {region} started (PID {p.pid}). Pulling all origin events from peers...")

    try:
        sys.exit(p.wait())
    except KeyboardInterrupt:
        p.terminate()


if __name__ == "__main__":
    reg = sys.argv[1] if len(sys.argv) > 1 else "del"
    join(reg)
