"""API key -> (actor, role), and the role gate for writes (owner: Akshit).

The actor always comes from the key, never from the request body. Decisions are made by the
active trust adapter (region/trust/adapter.py); the role table is region/trust/roles.py.

401: no key, or a key this region does not know.   403: a known key whose role may not do this.
"""
from collections import namedtuple

from fastapi import Header, HTTPException

from region.trust import adapter

Auth = namedtuple("Auth", "actor role mode", defaults=("online",))  # mode: online | cached | stale (event.auth_mode)


def require_key(x_api_key: str = Header(None)) -> Auth:
    """Authenticate only (any known role). Kept for routes that check the role themselves."""
    d = adapter.get_adapter().verify_actor(x_api_key, None)
    if not d.allowed:
        raise HTTPException(401, "missing or unknown X-API-Key")
    return Auth(d.actor, d.role, d.mode)


def require(action: str):
    """Dependency factory: authenticate AND check the role may perform `action` (roles.PERMISSIONS)."""
    def dependency(x_api_key: str = Header(None)) -> Auth:
        d = adapter.get_adapter().verify_actor(x_api_key, action)
        if d.allowed:
            return Auth(d.actor, d.role, d.mode)
        if d.reason == adapter.UNKNOWN_CREDENTIAL:
            raise HTTPException(401, "missing or unknown X-API-Key")
        raise HTTPException(403, f"role '{d.role}' may not {action}")
    return dependency
