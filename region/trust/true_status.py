"""TRUE credential status: the issuer-signed snapshot and the freshness policy (SyncShield convergence).

Pure and clock-injectable. The snapshot file is written by this region's auth sidecar after it verified the
issuer signature; this module verifies it AGAIN with the pinned issuer key (a file on disk is not trusted
just because of where it sits) and refuses rollbacks.

The policy below is the Python mirror of collaboration/true/services/syncshield-auth/src/status.ts. Both are
tested against the same table: collaboration/true/tests/status-policy-cases.json.
"""
import json
from dataclasses import dataclass
from typing import Optional

# seconds; mirrors STATUS_POLICY in packages/shared/src/syncshield.ts
ONLINE_S = 60      # snapshot obtained this recently: "online"
CACHED_S = 600     # up to 10 minutes: "cached", the limit for logins and privileged actions
HARD_S = 900       # absolute bound for ordinary writes with a known, previously active credential: "stale"

STATUS_TOKEN_TYPE = "syncshield-status+jwt"

PURPOSES = ("login", "write", "privileged")


@dataclass(frozen=True)
class Decision:
    allow: bool
    mode: Optional[str] = None      # online | cached | stale   (when allowed)
    reason: str = ""                # when denied
    age: int = 0


def evaluate(statuses: Optional[dict], issued_at: Optional[int], credential_id: str, now: int, purpose: str) -> Decision:
    """`statuses` is None when there is no valid snapshot at all."""
    if statuses is None or issued_at is None:
        return Decision(False, reason="no_status")
    state = statuses.get(credential_id)
    age = max(0, int(now) - int(issued_at))
    if state is None:
        return Decision(False, reason="unknown_credential", age=age)
    if state == "r":
        return Decision(False, reason="revoked", age=age)
    if age > HARD_S:
        return Decision(False, reason="status_too_old", age=age)
    if age <= ONLINE_S:
        return Decision(True, mode="online", age=age)
    if age <= CACHED_S:
        return Decision(True, mode="cached", age=age)
    if purpose == "write":
        return Decision(True, mode="stale", age=age)
    return Decision(False, reason="status_stale_for_purpose", age=age)


@dataclass(frozen=True)
class Snapshot:
    ver: int
    issued_at: int
    statuses: dict


class StatusStore:
    """Reads and verifies the sidecar-mirrored snapshot file; keeps the last good one.

    `issuer_jwk` / `issuer_id` come from the PINNED issuer file. Verification failure, a missing file or a
    lower version than one already seen never replaces the last good snapshot.
    """

    def __init__(self, path: str, issuer_id: str, issuer_jwk: dict):
        self.path = path
        self.issuer_id = issuer_id
        self._jwk = {k: v for k, v in issuer_jwk.items() if k != "d"}   # public half only
        self._sig = None
        self._good: Optional[Snapshot] = None
        self._max_ver = -1
        self.last_error: Optional[str] = None

    def _verify(self, jws: str) -> Snapshot:
        import jwt  # lazy: PyJWT is an opt-in dependency (requirements-auth.txt)
        from jwt.algorithms import ECAlgorithm

        header = jwt.get_unverified_header(jws)
        if header.get("alg") != "ES256" or header.get("typ") != STATUS_TOKEN_TYPE:
            raise ValueError("wrong snapshot header")
        key = ECAlgorithm.from_jwk(json.dumps(self._jwk))
        payload = jwt.decode(
            jws, key, algorithms=["ES256"],
            options={"verify_exp": False, "verify_nbf": False, "verify_iat": False, "verify_aud": False,
                     "verify_iss": False, "require": []},
        )
        if payload.get("iss") != self.issuer_id:
            raise ValueError("wrong issuer")
        ver, iat, statuses = payload.get("ver"), payload.get("iat"), payload.get("statuses")
        if not isinstance(ver, int) or not isinstance(iat, int) or not isinstance(statuses, dict):
            raise ValueError("malformed snapshot")
        if any(v not in ("a", "r") for v in statuses.values()):
            raise ValueError("malformed status value")
        return Snapshot(ver=ver, issued_at=iat, statuses=statuses)

    def current(self) -> Optional[Snapshot]:
        # Compare content, not (mtime, size): two snapshots of equal size written within one timestamp tick
        # would look identical by stat, and a newer one (for example a revocation) would be missed.
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
        except OSError:
            self.last_error = "no_file"
            return self._good
        if raw == self._sig:
            return self._good
        self._sig = raw
        try:
            snap = self._verify(raw)
        except Exception as exc:  # noqa: BLE001 - any failure means "keep the last good one"
            self.last_error = f"rejected: {exc}"
            return self._good
        if snap.ver < self._max_ver:
            self.last_error = "rollback"
            return self._good
        self._max_ver = snap.ver
        self._good = snap
        self.last_error = None
        return snap
