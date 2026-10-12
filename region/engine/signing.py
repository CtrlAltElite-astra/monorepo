"""Ed25519 event signatures (owner: Rahil; implemented on request by Miti).

An event's `sig` is the base64 Ed25519 signature, by the ORIGIN node's private key, over the ASCII
bytes of the event's hex `hash`. The hash already covers every other field, so a signed hash
binds the whole envelope. A node never signs another node's events; a browser that carries an
event cannot change it without breaking the hash, and cannot forge one without the origin's key.

Files
    keys/<region>.key              private key, base64 of 32 raw bytes   (git-ignored)
    region/trust/public_keys.json  {region: base64 public key}           (generated locally, git-ignored)

Env: SIGNING_KEYS_DIR, PUBLIC_KEYS_FILE override the locations (tests use this).

Behaviour when nothing is provisioned: `sign()` returns None (events stay unsigned) and
`verify()`/`has_key()` are False, so carry stays disabled instead of trusting an unsigned event.

generate / public_of / sign_with / verify_with are the same operations with the key passed in, for
callers that hold keys themselves: the in-memory nodes of the engine model, the F1 fuzz test and the
T17 harness.
"""
import base64
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ROOT = Path(__file__).resolve().parents[2]
_priv_cache: dict = {}
_pub_cache: dict = {"mtime": None, "path": None, "keys": {}}


def keys_dir() -> Path:
    return Path(os.environ.get("SIGNING_KEYS_DIR", ROOT / "keys"))


def public_keys_file() -> Path:
    return Path(os.environ.get("PUBLIC_KEYS_FILE", ROOT / "region" / "trust" / "public_keys.json"))


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _raw_public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def public_keys() -> dict:
    """Registry {region: base64 public key}, reloaded when the file changes."""
    path = public_keys_file()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {}
    if _pub_cache["path"] != str(path) or _pub_cache["mtime"] != mtime:
        try:
            _pub_cache.update(path=str(path), mtime=mtime, keys=json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return {}
    return _pub_cache["keys"]


def has_key(origin: str) -> bool:
    return origin in public_keys()


def _private(region: str) -> Optional[Ed25519PrivateKey]:
    path = keys_dir() / f"{region}.key"
    ck = str(path)
    if ck in _priv_cache:
        return _priv_cache[ck]
    try:
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(path.read_text().strip()))
    except (OSError, ValueError):
        return None
    _priv_cache[ck] = key
    return key


def can_sign(region: str) -> bool:
    return _private(region) is not None


def sign(region: str, hash_hex: str) -> Optional[str]:
    """Signature for an event this node originates, or None when no key is provisioned."""
    key = _private(region)
    if key is None:
        return None
    return _b64(key.sign(hash_hex.encode("ascii")))


def verify(origin: str, hash_hex: str, sig_b64: Optional[str]) -> bool:
    """True only for a valid signature by `origin`'s registered key. Fails closed."""
    pub = public_keys().get(origin)
    if not pub or not sig_b64:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pub)).verify(
            base64.b64decode(sig_b64), hash_hex.encode("ascii"))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False



# ---- pure: the caller supplies the key --------------------------------------
def generate() -> tuple:
    """A new key pair: (private, public), each the base64 of 32 raw bytes."""
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    return _b64(raw), _b64(_raw_public(key))


def public_of(private_b64: str) -> str:
    """The public key that belongs to a private key."""
    return _b64(_raw_public(Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))))


def sign_with(private_b64: str, hash_hex: str) -> str:
    """Signature over one event hash. Ed25519 is deterministic: same key and hash, same signature."""
    return _b64(Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_b64)).sign(hash_hex.encode("ascii")))


def verify_with(public_b64, hash_hex, sig_b64) -> bool:
    """True only when sig was made over hash_hex by the owner of public_b64. Never raises."""
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64, validate=True)).verify(
            base64.b64decode(sig_b64, validate=True), hash_hex.encode("ascii"))
        return True
    except (InvalidSignature, ValueError, TypeError, AttributeError):
        return False

@contextmanager
def _registry_lock(path: Path, timeout_s: float = 8.0):
    """Cross-process lock around the registry read-modify-write. Three regions that start together must not
    erase each other's entry (a lost update would make a node's signatures unverifiable until it restarts)."""
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout_s
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except (FileExistsError, PermissionError):  # Windows: PermissionError while another holder deletes it
            try:
                if time.time() - lock.stat().st_mtime > 10:  # left behind by a crashed holder
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.time() > deadline:
                raise TimeoutError(f"could not lock {lock}")
            time.sleep(0.02)
    try:
        yield
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def _write_registry_atomically(path: Path, registry: dict) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for attempt in range(20):  # Windows can refuse the replace for a moment while another process reads the file
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.02 * (attempt + 1))
    os.replace(tmp, path)


def ensure_keys(regions) -> dict:
    """Create any missing private key and add each region's public key to the registry. Dev convenience.

    Safe to call from several processes at once: each region's private key is only written by its own caller,
    and the shared registry is updated under a lock, re-read inside it, and replaced atomically."""
    kd = keys_dir()
    kd.mkdir(parents=True, exist_ok=True)
    mine = {}
    for r in regions:
        path = kd / f"{r}.key"
        if path.exists():
            key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(path.read_text().strip()))
        else:
            key = Ed25519PrivateKey.generate()
            path.write_text(_b64(key.private_bytes(
                serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())))
        mine[r] = _b64(_raw_public(key))
    pf = public_keys_file()
    pf.parent.mkdir(parents=True, exist_ok=True)
    with _registry_lock(pf):
        try:
            registry = json.loads(pf.read_text(encoding="utf-8"))  # fresh from disk, not the mtime cache
        except (OSError, ValueError):
            registry = {}
        registry.update(mine)
        _write_registry_atomically(pf, registry)
    _priv_cache.clear()
    return registry
