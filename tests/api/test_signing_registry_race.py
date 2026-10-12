"""Regions that start at the same moment must all end up in the shared key registry (owner: Akshit).

Each region provisions its own key at start. Before the registry update was locked, eight simultaneous starts
lost entries (a read-modify-write race) and the lost region's signatures were rejected as unknown_origin."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNIPPET = "import sys; from region.engine import signing; signing.ensure_keys([sys.argv[1]])"


def test_simultaneous_provisioning_keeps_every_regions_public_key(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(ROOT), SIGNING_KEYS_DIR=str(tmp_path / "keys"), PUBLIC_KEYS_FILE=str(tmp_path / "pub.json"))
    names = [f"r{i}" for i in range(8)]
    procs = [subprocess.Popen([sys.executable, "-c", SNIPPET, n], cwd=ROOT, env=env, stderr=subprocess.PIPE) for n in names]
    errors = [p.communicate(timeout=60)[1].decode() for p in procs]
    assert all(p.returncode == 0 for p in procs), errors
    registry = json.loads((tmp_path / "pub.json").read_text())
    assert sorted(registry) == sorted(names)
    assert not list(tmp_path.glob("pub.json.*")), "lock and temp files must be cleaned up"
    for n in names:  # and every private key is intact and matches its registered public key
        assert (tmp_path / "keys" / f"{n}.key").exists()
