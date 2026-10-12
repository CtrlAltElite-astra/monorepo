"""Signing tests (owner: Rahil).

A valid signature verifies. A tampered event, another node's key, an unknown origin and garbage do
not. Covers the node API the regions use (sign(region, hash), verify, has_key, ensure_keys) and the
pure helpers the in-memory nodes use (generate, sign_with, verify_with, public_of).
"""
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
def node(provision):
    """This process is node mum: its own private key, and both public keys in the registry."""
    provision({"mum": MUM}, {"mum": MUM_PUB, "pun": PUN_PUB})


# ---- valid ---------------------------------------------------------------
def test_valid_signature_verifies():
    sig = signing.sign("mum", EVENT["hash"])
    assert signing.verify("mum", EVENT["hash"], sig)
    assert len(sig) == 88 and sig == signing.sign("mum", EVENT["hash"]) == signing.sign_with(MUM, EVENT["hash"])


def test_signed_event_survives_transport():
    ev = dict(EVENT, sig=signing.sign("mum", EVENT["hash"]))
    got = json.loads(json.dumps(ev, indent=2))  # the sig is not part of the hash, so adding it changes nothing
    assert canonical.event_hash(got) == got["hash"] and signing.verify(got["origin"], got["hash"], got["sig"])


# ---- tampered ------------------------------------------------------------
def test_tampered_event_does_not_verify():
    sig = signing.sign("mum", EVENT["hash"])
    edited = dict(EVENT, value="closed")
    assert canonical.event_hash(edited) != EVENT["hash"]  # the receiver's hash check fails first
    assert not signing.verify("mum", canonical.event_hash(edited), sig)  # and the old signature does not fit
    flipped = ("B" if sig[0] == "A" else "A") + sig[1:]
    assert not signing.verify("mum", EVENT["hash"], flipped)


# ---- wrong key -----------------------------------------------------------
def test_another_nodes_key_cannot_sign_for_mum():
    forged = signing.sign_with(PUN, EVENT["hash"])  # pun signs an event that claims origin mum
    assert not signing.verify("mum", EVENT["hash"], forged)
    assert signing.verify("pun", EVENT["hash"], forged)  # it is a real signature, just not mum's
    assert signing.sign("pun", EVENT["hash"]) is None  # this node holds no private key for pun


# ---- unknown origin ------------------------------------------------------
def test_unknown_origin_never_verifies():
    sig = signing.sign("mum", EVENT["hash"])
    assert signing.has_key("mum") and not signing.has_key("del")
    assert not signing.verify("del", EVENT["hash"], sig)


@pytest.mark.parametrize("sig", [None, "", "not base64!", "AAAA", 123, [], {}])
def test_garbage_signature_is_false_and_never_raises(sig):
    assert signing.verify("mum", EVENT["hash"], sig) is False
    assert signing.verify_with(MUM_PUB, EVENT["hash"], sig) is False
    assert signing.verify_with("not a key", EVENT["hash"], signing.sign_with(MUM, EVENT["hash"])) is False


# ---- pure helpers --------------------------------------------------------
def test_pure_helpers_agree_with_the_node_api():
    assert signing.public_of(MUM) == MUM_PUB and signing.public_of(PUN) == PUN_PUB
    sig = signing.sign_with(MUM, EVENT["hash"])
    assert signing.verify_with(MUM_PUB, EVENT["hash"], sig) and not signing.verify_with(PUN_PUB, EVENT["hash"], sig)
    assert signing.verify("mum", EVENT["hash"], sig)


# ---- a node without a key ------------------------------------------------
def test_node_without_a_key_writes_unsigned_events(provision):
    provision({}, {"mum": MUM_PUB})
    (Path(signing.keys_dir()) / "mum.key").unlink()
    assert signing.sign("mum", EVENT["hash"]) is None and not signing.can_sign("mum")
    assert not signing.verify("mum", EVENT["hash"], None)


def test_missing_files_mean_unsigned_and_nothing_verifies(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNING_KEYS_DIR", str(tmp_path / "nokeys"))
    monkeypatch.setenv("PUBLIC_KEYS_FILE", str(tmp_path / "none.json"))
    signing._priv_cache.clear()
    assert signing.sign("mum", EVENT["hash"]) is None and not signing.has_key("mum")


# ---- key files -----------------------------------------------------------
def test_ensure_keys_creates_once_and_never_changes_an_existing_key(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNING_KEYS_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("PUBLIC_KEYS_FILE", str(tmp_path / "trust" / "public_keys.json"))
    signing._priv_cache.clear()
    first = signing.ensure_keys(["mum", "pun"])
    assert set(first) == {"mum", "pun"} and first["mum"] != first["pun"]
    again = signing.ensure_keys(["mum", "pun", "del"])
    assert {r: again[r] for r in first} == first and "del" in again
    sig = signing.sign("mum", EVENT["hash"])
    assert signing.verify("mum", EVENT["hash"], sig) and signing.public_of((tmp_path / "keys" / "mum.key")
                                                                           .read_text().strip()) == first["mum"]


def test_gen_keys_command_line_never_prints_a_private_key(tmp_path):
    keys, registry = tmp_path / "keys", tmp_path / "public_keys.json"
    env = {**__import__("os").environ, "SIGNING_KEYS_DIR": str(keys), "PUBLIC_KEYS_FILE": str(registry)}
    run = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_keys.py"), "mum"],
                         capture_output=True, text=True, cwd=ROOT, env=env)
    assert run.returncode == 0
    private = (keys / "mum.key").read_text(encoding="ascii").strip()
    assert private not in run.stdout + run.stderr and signing.public_of(private) in registry.read_text(encoding="utf-8")
