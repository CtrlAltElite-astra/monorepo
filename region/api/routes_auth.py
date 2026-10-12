"""/v1/auth/* : TRUE credential login for this region (mounted only when AUTH_ENABLED=true).

The browser proves membership with a TRUE presentation; this region's loopback sidecar runs the partner's real
verification and returns a short-lived regional session token. FastAPI only relays: it holds no credential
crypto of its own, and the browser never talks to the sidecar directly.

    POST /v1/auth/challenge   one-time nonce + audience chosen HERE, and the claims to disclose
                              optional body {"claim": "age_over_18" | "member"}: also ask for that ONE attribute
    POST /v1/auth/session     {challenge_id, presentation} -> {token, expires_at, role, auth_mode, ...}
    GET  /v1/auth/me          identity DERIVED from the verified session and current local status (never echoed)
                              claims: attributes proven at sign-in; disclosed: attribute names the shop received
    POST /v1/auth/logout      stateless: tells the client to drop its token (see docs: no instant revocation)
    GET  /v1/auth/status      public, no personal data: is login available here, and how fresh is the status?
"""
import time
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from region import config
from region.auth import Auth, require_key
from region.trust import adapter

router = APIRouter(prefix="/v1/auth", tags=["auth"])

NO_STORE = {"Cache-Control": "no-store"}
SIDECAR_TIMEOUT = httpx.Timeout(connect=0.5, read=2.0, write=1.0, pool=1.0)


async def _sidecar(method: str, path: str, json: Optional[dict] = None):
    """Call this region's sidecar. Returns (status, body). Never raises on transport errors."""
    s = config.settings
    if not s.auth_sidecar_url:
        return 503, {"error": {"code": "auth_unavailable", "message": "No auth sidecar is configured for this region"}}
    try:
        async with httpx.AsyncClient(timeout=SIDECAR_TIMEOUT) as client:
            r = await client.request(method, s.auth_sidecar_url + path, json=json,
                                     headers={"X-Sidecar-Token": s.auth_sidecar_token})
        try:
            return r.status_code, r.json()
        except ValueError:
            return 502, {"error": {"code": "auth_bad_response", "message": "Sidecar returned a non-JSON answer"}}
    except httpx.HTTPError:
        return 503, {"error": {"code": "auth_unavailable", "message": "This region's auth sidecar is not reachable"}}


class ChallengeBody(BaseModel):
    claim: Optional[Literal["age_over_18", "member"]] = None   # at most one attribute per sign-in


@router.post("/challenge")
async def challenge(body: Optional[ChallengeBody] = None):
    status, out = await _sidecar("POST", "/internal/v1/challenge",
                                 {"claim": body.claim} if body is not None and body.claim else {})
    return JSONResponse(status_code=status, content=out, headers=NO_STORE)


class SessionBody(BaseModel):
    challenge_id: str = Field(min_length=1, max_length=200)
    presentation: str = Field(min_length=1, max_length=20000)


@router.post("/session")
async def session(body: SessionBody):
    status, out = await _sidecar("POST", "/internal/v1/session",
                                 {"challenge_id": body.challenge_id, "presentation": body.presentation})
    # the token is a bearer secret: it must never be cached by a browser or a proxy
    return JSONResponse(status_code=status, content=out, headers=NO_STORE)


@router.get("/me")
def me(auth: Auth = Depends(require_key), authorization: str = Header(None)):
    """Re-derive everything from the verified session; nothing is echoed from the request."""
    a = adapter.get_adapter()
    token = authorization.split(" ", 1)[1].strip() if authorization and " " in authorization else None
    decision, sess, status = a.decide(token, None)
    return JSONResponse(content={
        "actor_id": auth.actor,
        "role": auth.role,
        "credential_id": auth.credential_id,
        "auth_mode": decision.mode,
        "status_ver": decision.status_ver,
        "status_age_s": status.age if status else None,
        "session_expires_at": sess.exp if sess else None,
        "region": config.settings.region,
        "claims": list(auth.claims),
        "disclosed": list(sess.disclosed) if sess else [],
    }, headers=NO_STORE)


@router.post("/logout")
def logout(_: Auth = Depends(require_key)):
    # Sessions are stateless and short-lived. We cannot reach into other regions to cancel one; the client
    # discards the token and stops renewing it. A credential REVOCATION, by contrast, is enforced through the
    # status snapshot at every region that has received it.
    return JSONResponse(content={"logged_out": True, "note": "token discarded client-side; expires within its TTL"},
                        headers=NO_STORE)


@router.get("/status")
async def status():
    a = adapter.get_adapter()
    snap = getattr(a, "status", None) and a.status.current()
    now = int(time.time())
    sidecar_status, sidecar_body = await _sidecar("GET", "/internal/v1/health")
    return {
        "enabled": True,
        "region": config.settings.region,
        "login_available": sidecar_status == 200,
        "status": ({"ver": snap.ver, "age_s": max(0, now - snap.issued_at)} if snap else None),
    }
