"""API key -> (actor, role), and the role gate for writes (owner: Akshit).

The actor always comes from the credential, never from the request body. Decisions are made by the
active trust adapter (region/trust/adapter.py); the role table is region/trust/roles.py.

Base mode (AUTH_ENABLED off, the default): X-API-Key, unchanged.   401: no key or unknown key.  403: role.
Auth mode (AUTH_ENABLED=true, TRUE credential sessions): `Authorization: Bearer <session>` ONLY.
    X-API-Key is not read at all, so a legacy key can never bypass the gate.
    401  missing/invalid/expired session, unknown or revoked credential
    403  a valid session whose role may not do this
    503  this region has no usable status evidence (SDK fails over to a node that has)
"""
from collections import namedtuple
from typing import Optional

from fastapi import Header, HTTPException

from region import config
from region.trust import adapter

# Lane A's field order is kept (mode stays third); credential_id is appended and is None in base mode
# (an API key has no credential record). mode: online | cached | stale (event.auth_mode)
Auth = namedtuple("Auth", "actor role mode credential_id", defaults=("online", None))

_UNAVAILABLE = ("no_status", "status_too_old", "status_stale_for_purpose")


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def _fail(decision) -> HTTPException:
    code = decision.reason or "unauthorized"
    if code == adapter.FORBIDDEN:
        return HTTPException(403, detail={"code": "forbidden", "message": f"role '{decision.role}' may not do this"})
    if code in _UNAVAILABLE:
        return HTTPException(503, detail={"code": code, "message": "This region cannot confirm credential status"},
                             headers={"Retry-After": "1"})
    message = "credential has been revoked" if code == "revoked" else "missing or invalid session"
    return HTTPException(401, detail={"code": "credential_revoked" if code == "revoked" else code, "message": message})


def _authenticate(x_api_key, authorization, action):
    if config.settings.auth_enabled:
        d = adapter.get_adapter().verify_actor(_bearer(authorization), action)   # X-API-Key deliberately ignored
        if not d.allowed:
            raise _fail(d)
        return Auth(d.actor, d.role, d.mode, d.credential_id)
    d = adapter.get_adapter().verify_actor(x_api_key, action)
    if d.allowed:
        return Auth(d.actor, d.role, d.mode, d.credential_id)
    if d.reason == adapter.UNKNOWN_CREDENTIAL:
        raise HTTPException(401, "missing or unknown X-API-Key")
    raise HTTPException(403, f"role '{d.role}' may not {action}")


def require_key(x_api_key: str = Header(None), authorization: str = Header(None)) -> Auth:
    """Authenticate only (any known role). Kept for routes that check the role themselves."""
    return _authenticate(x_api_key, authorization, None)


def require(action: str):
    """Dependency factory: authenticate AND check the role may perform `action` (roles.PERMISSIONS)."""
    def dependency(x_api_key: str = Header(None), authorization: str = Header(None)) -> Auth:
        return _authenticate(x_api_key, authorization, action)
    return dependency


def read_context(x_api_key: str = Header(None), authorization: str = Header(None)) -> Optional[Auth]:
    """For read routes. Base mode: public as before (returns None). Auth mode: a valid session is required."""
    if not config.settings.auth_enabled:
        return None
    return _authenticate(x_api_key, authorization, None)
