"""Identity questions the rest of the region asks (contract v1.2, section 3.5). Owner: Miti (lane C).

    subject_key(auth)        the rate limiter's key: the credential id when signed in, else the API-key actor
    has_claim(auth, claim)   was `claim` proven in this verified session? Always False in API-key mode
    is_revoked(cid)          does the issuer-signed status list this region holds mark `cid` revoked?
    require_claim(auth, c)   raises the 403 of 3.3 when `has_claim` is False (lane A's write path calls it)

Everything is read from data the region already verified: the session token (claims proven at sign-in, see
true_session.py) and the status snapshot (true_status.py). Nothing here calls the issuer or the sidecar, so a
dead issuer, sidecar or neighbouring region changes none of these answers.
"""
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from region import config
from region.trust import adapter

# The disclosable attributes a restricted product may ask for. Mirrors SYNCSHIELD_ATTRIBUTES in
# collaboration/true/packages/shared/src/syncshield.ts.
CLAIMS = ("age_over_18", "member")


def subject_key(auth) -> str:
    """Who a rate limit follows: the verified person (credential id) in auth mode, the API-key actor otherwise."""
    if config.settings.auth_enabled and getattr(auth, "credential_id", None):
        return auth.credential_id
    return auth.actor


def has_claim(auth, claim: str) -> bool:
    """True only for a claim this session PROVED at sign-in (disclosed and true). API-key mode has no claims."""
    if not config.settings.auth_enabled or auth is None or claim not in CLAIMS:
        return False
    return claim in (getattr(auth, "claims", None) or ())


def is_revoked(credential_id: str) -> bool:
    """Revoked in the last good issuer-signed snapshot this region holds.

    Same freshness as every other auth decision here: the snapshot file is re-read (and re-verified) on each
    call, so a revocation is seen as soon as this region's sidecar has fetched it (one STATUS_REFRESH_MS,
    5 s by default). Only an explicit "r" counts: no snapshot, an unknown id or API-key mode is "not revoked",
    so the engine rule that uses this never rejects history it simply has no status for."""
    if not credential_id:
        return False
    status = getattr(adapter.get_adapter(), "status", None)
    snap = status.current() if status is not None else None
    return bool(snap and snap.statuses.get(credential_id) == "r")


class ClaimRequired(HTTPException):
    """403 {"error": "claim_required", "claim": ...}; rendered with exactly that body by claim_required_handler."""

    def __init__(self, claim: str):
        self.claim = claim
        super().__init__(403, detail={"error": "claim_required", "claim": claim})


def require_claim(auth, claim: str) -> None:
    if claim not in CLAIMS:
        raise ValueError(f"unknown claim {claim!r}; expected one of {CLAIMS}")
    if not has_claim(auth, claim):
        raise ClaimRequired(claim)


async def claim_required_handler(_request, exc: ClaimRequired) -> JSONResponse:
    """The contract body (3.3) at the top level, not wrapped in FastAPI's {"detail": ...}."""
    return JSONResponse(status_code=403, content={"error": "claim_required", "claim": exc.claim},
                        headers={"Cache-Control": "no-store"})
