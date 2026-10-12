"""Signing tests (owner: Rahil).

A valid signature verifies. A tampered event, another node's key, an unknown
origin and garbage do not. Key files come from scripts/gen_keys.py.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from region.engine import canonical, signing

ROOT = Path(__file__).resolve().parents[2]
MUM, MUM_PUB = signing.generate()
PUN, PUN_PUB = signing.generate()

EVENT = dict(v=1, event_id="mum:17", origin="mum", seq=17, report_id="R-3f9a2c10", type="SET", field="status",
             value="assigned", supersedes=["mum:12"], hlc=[1760277060123, 0], priority=1, op_id="7f2a9c4e",
             actor="agent-mum-1", reason=None, wall="2026-10-12T19:21:00+05:30", sig=None)
EVENT["hash"] = canonical.event_hash(EVENT)


@pytest.fixture(autouse=True)
def node(monkeypatch):
    """Every test starts as node mum knowing both public keys. Module state is restored afterwards."""
    for name in ("_private", "_public", "_loaded"):
        monkeypatch.setattr(signing, name, getattr(signing, name))
    for var in ("REGION", "KEYS_DIR", "PUBLIC_KEYS_FILE"):
        monkeypatch.delenv(var, raising=False)
    signing.configure(MUM, {"mum": MUM_PUB, "pun": PUN_PUB})


def gen_keys():
    spec = importlib.util.spec_from_file_location("gen_keys", ROOT / "scripts" / "gen_keys.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---- valid ---------------------------------------------------------------
def test_valid_signature_verifies():
    sig = signing.sign(EVENT["hash"])
    assert signing.verify("mum", EVENT["hash"], sig) and signing.status("mum", EVENT["hash"], sig) == "verified"
    assert len(sig) == 88 and sig == signing.sign(EVENT["hash"])  # 64 bytes in base64; same input, same signature


def test_signed_event_survives_transport():
    ev = dict(EVENT, sig=signing.sign(EVENT["hash"]))
    got = json.loads(json.dumps(ev, indent=2))  # the sig is not part of the hash, so adding it changes nothing
    assert canonical.event_hash(got) == got["hash"] and signing.verify(got["origin"], got["hash"], got["sig"])


# ---- tampered ------------------------------------------------------------
def test_tampered_event_does_not_verify():
    sig = signing.sign(EVENT["hash"])
    edited = dict(EVENT, value="closed")
    assert canonical.event_hash(edited) != EVENT["hash"]  # the receiver's hash check fails first
    assert not signing.verify("mum", canonical.event_hash(edited), sig)  # and the old signature does not fit
    assert signing.status("mum", canonical.event_hash(edited), sig) == "bad"
    flipped = ("B" if sig[0] == "A" else "A") + sig[1:]
    assert not signing.verify("mum", EVENT["hash"], flipped)


# ---- wrong key -----------------------------------------------------------
def test_another_nodes_key_cannot_sign_for_mum():
    forged = signing.sign_with(PUN, EVENT["hash"])  # pun signs an event that claims origin mum
    assert not signing.verify("mum", EVENT["hash"], forged)
    assert signing.verify("pun", EVENT["hash"], forged)  # it is a real signature, just not mum's


# ---- unknown origin ------------------------------------------------------
def test_unknown_origin_never_verifies():
    sig = signing.sign(EVENT["hash"])
    assert signing.known("mum") and not signing.known("del")
    assert not signing.verify("del", EVENT["hash"], sig) and signing.status("del", EVENT["hash"], sig) == "bad"
    assert not signing.known(None) and not signing.known(["mum"])


@pytest.mark.parametrize("sig", [None, "", "not base64!", "AAAA", 123, [], {}])
def test_garbage_signature_is_false_and_never_raises(sig):
    assert signing.verify("mum", EVENT["hash"], sig) is False
    assert signing.verify("mum", None, sig) is False and signing.verify({}, EVENT["hash"], sig) is False


# ---- a node without a key ------------------------------------------------
def test_node_without_a_key_writes_unsigned_events():
    signing.configure(None, {"mum": MUM_PUB})
    assert signing.sign(EVENT["hash"]) is None
    assert signing.status("mum", EVENT["hash"], None) == "unsigned" and not signing.verify("mum", EVENT["hash"], None)


# ---- key files -----------------------------------------------------------
def test_gen_keys_then_load_signs_and_verifies(tmp_path):
    keys, registry = tmp_path / "keys", tmp_path / "trust" / "public_keys.json"
    make_keys = gen_keys().make_keys
    assert make_keys(["mum", "pun"], keys, registry) == {"mum": "created", "pun": "created"}
    before = json.loads(registry.read_text(encoding="utf-8"))
    assert set(before) == {"mum", "pun"} and before["mum"] != before["pun"]
    assert make_keys(["mum", "pun", "del"], keys, registry) == {"mum": "kept", "pun": "kept", "del": "created"}
    after = json.loads(registry.read_text(encoding="utf-8"))
    assert {r: after[r] for r in before} == before  # running again never changes an existing key

    signing.load("mum", keys, registry)
    sig = signing.sign(EVENT["hash"])
    signing.load("pun", keys, registry)  # another node, with only its own private key
    assert signing.verify("mum", EVENT["hash"], sig) and signing.sign(EVENT["hash"]) != sig

    assert make_keys(["mum"], keys, registry, force=True) == {"mum": "created"}  # rotation
    signing.load("pun", keys, registry)
    assert not signing.verify("mum", EVENT["hash"], sig)  # signed with the old key


def test_load_refuses_a_private_key_that_does_not_match_the_registry(tmp_path):
    keys, registry = tmp_path / "keys", tmp_path / "public_keys.json"
    gen_keys().make_keys(["mum"], keys, registry)
    registry.write_text(json.dumps({"mum": PUN_PUB}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not match"):
        signing.load("mum", keys, registry)


def test_missing_files_mean_unsigned_and_nothing_verifies(tmp_path):
    signing.load("mum", tmp_path / "nokeys", tmp_path / "none.json")
    assert signing.sign(EVENT["hash"]) is None and not signing.known("mum")


def test_first_use_loads_keys_named_by_the_environment(tmp_path, monkeypatch):
    keys, registry = tmp_path / "keys", tmp_path / "public_keys.json"
    gen_keys().make_keys(["mum"], keys, registry)
    monkeypatch.setattr(signing, "_loaded", False)
    monkeypatch.setenv("REGION", "mum")
    monkeypatch.setenv("KEYS_DIR", str(keys))
    monkeypatch.setenv("PUBLIC_KEYS_FILE", str(registry))
    assert signing.verify("mum", EVENT["hash"], signing.sign(EVENT["hash"]))


def test_gen_keys_command_line_never_prints_a_private_key(tmp_path):
    keys, registry = tmp_path / "keys", tmp_path / "public_keys.json"
    run = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_keys.py"), "mum", "--keys-dir", str(keys),
                          "--registry", str(registry)], capture_output=True, text=True, cwd=ROOT)
    assert run.returncode == 0 and "mum: created" in run.stdout
    private = (keys / "mum.key").read_text(encoding="ascii").strip()
    assert private not in run.stdout + run.stderr and signing.public_of(private) in registry.read_text(encoding="utf-8")
