"""Sign and verify events (owner: Akshit; thin seam over region/trust/keys.py).

The signature is Ed25519 over the ASCII bytes of the event's hex hash, base64 encoded. Because the
hash covers every field except `hash` and `sig`, a valid signature binds the whole event to its origin.

`sign` only ever uses THIS region's key: a node never signs another node's events.
`verify(origin, hash, sig)` is the function the ingest hook and POST /v1/carry call; it is False
for an unknown origin, a malformed signature or a wrong key, and never raises.
"""
import base64
import binascii

from cryptography.exceptions import InvalidSignature

from region import config
from region.trust import keys


def sign(hash_hex: str) -> str:
    return base64.b64encode(keys.private_key(config.settings.region).sign(hash_hex.encode("ascii"))).decode("ascii")


def is_known_origin(origin) -> bool:
    return isinstance(origin, str) and keys.public_key(origin) is not None


def verify(origin, hash_hex, sig) -> bool:
    if not (isinstance(origin, str) and isinstance(hash_hex, str) and isinstance(sig, str)):
        return False
    public = keys.public_key(origin)
    if public is None:
        return False
    try:
        public.verify(base64.b64decode(sig, validate=True), hash_hex.encode("ascii"))
        return True
    except (InvalidSignature, binascii.Error, ValueError, UnicodeEncodeError):
        return False
