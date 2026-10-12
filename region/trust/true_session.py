"""Verification of a SyncShield regional session token (SyncShield convergence).

A region never trusts a decoded payload: the signature is checked against PINNED regional session public keys
(one file per region in a directory the operator controls), the header is checked against an allowlist, and
every claim is validated against the contract the sidecar issues. All time checks use an injectable clock.

This is ordinary JWT verification: the TRUE credential was verified once, at login, by the partner's code in
the Node sidecar. Cheap per-request authorization uses only local data (these keys and the status snapshot).
"""
import json
import os
import re
from dataclasses import dataclass
from typing import Callable, Optional

SESSION_AUDIENCE = "syncshield"
SESSION_TOKEN_TYPE = "syncshield-session+jwt"
SESSION_ISSUER_PREFIX = "syncshield-region:"
ROLES = ("officer", "supervisor", "admin")
ATTRIBUTES = ("age_over_18", "member")   # disclosable attributes; mirrors SYNCSHIELD_ATTRIBUTES in syncshield.ts
MAX_SESSION_SECONDS = 600      # refuse absurdly long tokens even if correctly signed

_ACTOR = re.compile(r"^act_[0-9a-f]{16}$")
_CRED = re.compile(r"^cred_[0-9a-f]{24}$")


class AuthError(Exception):
    """A session was rejected. `code` is machine readable; `status` is the HTTP status to answer with."""

    def __init__(self, code: str, message: str = "", status: int = 401):
        super().__init__(code)
        self.code, self.message, self.status = code, message or code, status


@dataclass(frozen=True)
class Session:
    actor_id: str
    role: str
    credential_id: str
    kid: str
    region: str
    jti: str
    iat: int
    exp: int
    status_ver: int
    status_at: int
    credential_exp: int
    # Contract v1.2 (C1). `disclosed`: attribute names the shop received at sign-in; `claims`: those proven true.
    # The session holds these names and nothing else from the credential (no name, no date of birth).
    claims: tuple = ()
    disclosed: tuple = ()


def _attribute_list(value) -> tuple:
    """`clm` / `dsc`: absent means none; otherwise a list of distinct known attribute names."""
    if value is None:
        return ()
    if not isinstance(value, list) or len(set(value)) != len(value) or any(v not in ATTRIBUTES for v in value):
        raise AuthError("malformed_session", "Bad claim list")
    return tuple(sorted(value))


class SessionKeyRegistry:
    """Pinned public session keys: {kid: (region, jwk)} loaded from <dir>/<region>.json files."""

    def __init__(self, directory: str):
        self.directory = directory
        self._sig = None
        self._keys: dict = {}

    def keys(self) -> dict:
        try:
            names = sorted(n for n in os.listdir(self.directory) if n.endswith(".json"))
            sig = tuple((n, os.stat(os.path.join(self.directory, n)).st_mtime_ns) for n in names)
        except OSError:
            return {}
        if sig != self._sig:
            keys = {}
            for n in names:
                try:
                    with open(os.path.join(self.directory, n), "r", encoding="utf-8") as fh:
                        d = json.load(fh)
                    keys[d["kid"]] = (d["region"], {k: v for k, v in d["jwk"].items() if k != "d"})
                except (OSError, ValueError, KeyError):
                    continue
            self._keys, self._sig = keys, sig
        return self._keys


class SessionVerifier:
    def __init__(self, registry: SessionKeyRegistry, clock: Optional[Callable[[], float]] = None):
        self.registry = registry
        self.clock = clock or __import__("time").time

    def verify(self, token: Optional[str]) -> Session:
        import jwt  # lazy: opt-in dependency
        from jwt.algorithms import ECAlgorithm

        if not token or not isinstance(token, str):
            raise AuthError("missing_session", "No session token supplied")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise AuthError("malformed_session", "Not a readable token")
        # explicit allowlists: never "whatever the token says"
        if header.get("alg") != "ES256":
            raise AuthError("bad_algorithm", "Only ES256 sessions are accepted")
        if header.get("typ") != SESSION_TOKEN_TYPE:
            raise AuthError("wrong_token_type", "This is not a SyncShield session token")
        known = self.registry.keys().get(header.get("kid"))
        if known is None:
            raise AuthError("unknown_key", "The signing key is not pinned in this region")
        region, jwk = known
        try:
            key = ECAlgorithm.from_jwk(json.dumps(jwk))
            claims = jwt.decode(
                token, key, algorithms=["ES256"],
                options={"verify_exp": False, "verify_nbf": False, "verify_iat": False, "verify_aud": False,
                         "verify_iss": False, "require": []},
            )
        except jwt.PyJWTError:
            raise AuthError("bad_signature", "Session signature is not valid")

        now = int(self.clock())
        if claims.get("iss") != SESSION_ISSUER_PREFIX + region:
            raise AuthError("wrong_issuer", "Token issuer does not match its signing key")
        aud = claims.get("aud")
        if aud != SESSION_AUDIENCE and not (isinstance(aud, list) and SESSION_AUDIENCE in aud):
            raise AuthError("wrong_audience", "Token is not for SyncShield")
        iat, nbf, exp = claims.get("iat"), claims.get("nbf"), claims.get("exp")
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (iat, nbf, exp)):
            raise AuthError("malformed_session", "Missing or non-integer time claims")
        if now < nbf:
            raise AuthError("session_not_yet_valid", "Session is not valid yet")
        if now >= exp:
            raise AuthError("session_expired", "Session has expired")
        if exp - iat > MAX_SESSION_SECONDS or exp <= iat:
            raise AuthError("malformed_session", "Unreasonable session lifetime")
        sub, role, cid = claims.get("sub"), claims.get("role"), claims.get("cid")
        if not isinstance(sub, str) or not _ACTOR.match(sub):
            raise AuthError("malformed_session", "Bad subject")
        if role not in ROLES:
            raise AuthError("malformed_session", "Unknown role")
        if not isinstance(cid, str) or not _CRED.match(cid):
            raise AuthError("malformed_session", "Bad credential reference")
        jti = claims.get("jti")
        if not isinstance(jti, str) or not jti:
            raise AuthError("malformed_session", "Missing jti")
        sver, sat, cexp = claims.get("sver"), claims.get("sat"), claims.get("cexp")
        if not all(isinstance(v, int) for v in (sver, sat, cexp)):
            raise AuthError("malformed_session", "Missing status or credential bounds")
        if exp > cexp:
            raise AuthError("malformed_session", "Session outlives its credential")
        proven, disclosed = _attribute_list(claims.get("clm")), _attribute_list(claims.get("dsc"))
        if not set(proven) <= set(disclosed):
            raise AuthError("malformed_session", "A claim was proven without being disclosed")
        return Session(sub, role, cid, header["kid"], region, jti, iat, exp, sver, sat, cexp, proven, disclosed)
