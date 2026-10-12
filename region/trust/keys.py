"""Per-region Ed25519 keys and the public-key registry (owner: Akshit).

Private key of region R: keys/R.key (base64 of the 32-byte seed; git-ignored, never leaves R).
Public keys, all merged, later sources win:
  1. region/trust/public_keys.json   committed registry  {"mum": "<base64 raw public key>", ...}
  2. keys/public_keys.json           output of scripts/gen_keys.py, if present
  3. keys/<region>.pub               written by ensure_region_key(); one writer per file, so three
                                     regions starting at once cannot corrupt a shared registry
Environment: KEYS_DIR (default <repo>/keys), PUBLIC_KEYS_PATH (replace sources 1-2 with one file).
"""
import base64
import json
import os
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ROOT = Path(__file__).resolve().parents[2]
COMMITTED_REGISTRY = Path(__file__).with_name("public_keys.json")

_private_cache: dict = {}


def keys_dir() -> Path:
    return Path(os.environ.get("KEYS_DIR") or ROOT / "keys")


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _public_b64(key: Ed25519PrivateKey) -> str:
    return _b64(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def _seed_b64(key: Ed25519PrivateKey) -> str:
    return _b64(key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()))


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def registry() -> dict:
    """{region id: base64 public key}. Read from disk on every call so a freshly joined node is seen."""
    override = os.environ.get("PUBLIC_KEYS_PATH")
    merged = _read_json(Path(override)) if override else {**_read_json(COMMITTED_REGISTRY), **_read_json(keys_dir() / "public_keys.json")}
    folder = keys_dir()
    if folder.is_dir():
        for pub in sorted(folder.glob("*.pub")):
            try:
                merged[pub.stem] = pub.read_text(encoding="utf-8").strip()
            except OSError:
                pass
    return merged


def public_key(origin: str) -> Optional[Ed25519PublicKey]:
    """The registered public key of `origin`, or None for an unknown origin or an unusable entry."""
    encoded = registry().get(origin)
    if not encoded:
        return None
    try:
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(encoded, validate=True))
    except ValueError:
        return None


def private_key(region: str) -> Ed25519PrivateKey:
    """This region's private key. Raises FileNotFoundError if it was never generated."""
    path = keys_dir() / f"{region}.key"
    cached = _private_cache.get(str(path))
    if cached is not None:
        return cached
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(path.read_text(encoding="utf-8").strip(), validate=True))
    _private_cache[str(path)] = key
    return key


def ensure_region_key(region: str) -> str:
    """Create keys/<region>.key and keys/<region>.pub if missing. Returns the base64 public key.

    Refuses to continue if a different public key is already registered for this region
    (a wiped key under a known region id would let it forge or be unable to verify its own history)."""
    folder = keys_dir()
    folder.mkdir(parents=True, exist_ok=True)
    key_path = folder / f"{region}.key"
    if key_path.exists():
        key = private_key(region)
    else:
        key = Ed25519PrivateKey.generate()
        key_path.write_text(_seed_b64(key), encoding="utf-8")
        _private_cache[str(key_path)] = key
    mine = _public_b64(key)
    known = registry().get(region)
    if known is not None and known != mine and not (folder / f"{region}.pub").exists():
        raise RuntimeError(f"region '{region}' is already registered with a different public key; "
                           f"restore keys/{region}.key or choose a new region id")
    (folder / f"{region}.pub").write_text(mine, encoding="utf-8")
    return mine


def reset_cache() -> None:
    _private_cache.clear()
