"""Ed25519 event signatures (owner: Rahil).

The node that writes an event signs its hash: sig = base64(Ed25519 over the hex
hash). Anyone holding the event (a peer relaying it, a browser carrying it after
a crash) can pass it on but cannot change it or forge another one, because only
the origin has its private key. A node never signs another node's events.

Keys live outside the code: keys/<region>.key (private, git-ignored) and
region/trust/public_keys.json ({region: public key}). Both are made by
python scripts/gen_keys.py. This is the one engine module that reads files,
and only in load().
"""
import base64
import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ROOT = Path(__file__).resolve().parents[2]
KEYS_DIR = ROOT / "keys"
REGISTRY = ROOT / "region" / "trust" / "public_keys.json"

_private = None  # this node's private key (base64); None = the node writes unsigned events
_public = {}     # {region: public key (base64)}
_loaded = False


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


# ---- pure: the caller supplies the key ------------------------------------
def generate() -> tuple:
    """A new key pair: (private, public), each the base64 of 32 raw bytes."""
    key = Ed25519PrivateKey.generate()
    return _b64(key.private_bytes_raw()), _b64(key.public_key().public_bytes_raw())


def public_of(private: str) -> str:
    """The public key that belongs to a private key."""
    return _b64(Ed25519PrivateKey.from_private_bytes(base64.b64decode(private)).public_key().public_bytes_raw())


def sign_with(private: str, event_hash: str) -> str:
    """Signature over one event hash. Ed25519 is deterministic: same key and hash, same signature."""
    return _b64(Ed25519PrivateKey.from_private_bytes(base64.b64decode(private)).sign(event_hash.encode("ascii")))


def verify_with(public: str, event_hash, sig) -> bool:
    """True only when sig was made over event_hash by the owner of public. Never raises."""
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public, validate=True))
        key.verify(base64.b64decode(sig, validate=True), event_hash.encode("ascii"))
        return True
    except (InvalidSignature, ValueError, TypeError, AttributeError):
        return False


# ---- this node's keys -------------------------------------------------------
def configure(private=None, public=None) -> None:
    """Set this node's keys directly (tests, or a caller that read them itself)."""
    global _private, _public, _loaded
    _private, _public, _loaded = private, dict(public or {}), True


def load(region=None, keys_dir=None, registry=None) -> None:
    """Read this node's private key and the registry of public keys.

    Defaults come from the environment (REGION, KEYS_DIR, PUBLIC_KEYS_FILE), then keys/ and
    region/trust/public_keys.json. A missing file is not an error: without a private key the
    node writes unsigned events, and with an empty registry nothing verifies.
    """
    region = region or os.environ.get("REGION")
    key_file = Path(keys_dir or os.environ.get("KEYS_DIR") or KEYS_DIR) / f"{region}.key"
    reg_file = Path(registry or os.environ.get("PUBLIC_KEYS_FILE") or REGISTRY)
    private = key_file.read_text(encoding="ascii").strip() if region and key_file.is_file() else None
    public = json.loads(reg_file.read_text(encoding="utf-8")) if reg_file.is_file() else {}
    if private and region in public and public_of(private) != public[region]:
        raise RuntimeError(f"{key_file} does not match '{region}' in {reg_file}: run scripts/gen_keys.py")
    configure(private, public)


def _ready() -> None:
    if not _loaded:
        load()


def sign(event_hash: str):
    """This node's signature over an event hash; None when the node has no key."""
    _ready()
    return sign_with(_private, event_hash) if _private else None


def known(origin) -> bool:
    """True when the registry holds a public key for that region."""
    _ready()
    return isinstance(origin, str) and origin in _public


def verify(origin, event_hash, sig) -> bool:
    """True only when sig is origin's own signature over event_hash.

    False for an unknown origin, a missing or malformed signature, a signature by another
    node's key, or a hash that changed after signing. Never raises.
    """
    return known(origin) and verify_with(_public[origin], event_hash, sig)


def status(origin, event_hash, sig) -> str:
    """'verified' | 'bad' | 'unsigned': the label the history view shows for an event."""
    if sig is None:
        return "unsigned"
    return "verified" if verify(origin, event_hash, sig) else "bad"
