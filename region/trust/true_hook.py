"""Opt-in wiring of TRUE credential sessions into the app (SyncShield convergence).

Called once from region/main.py. With AUTH_ENABLED off (the default) this only registers the renderer for the
contract's 403 claim_required (region/trust/claims.py; a route that never calls require_claim never sees it):
no import of PyJWT, no routes, no adapter swap, so a core run needs no Node sidecar and no extra dependency.
"""
from fastapi import FastAPI

from region import config
from region.trust import claims


def install(app: FastAPI) -> None:
    app.add_exception_handler(claims.ClaimRequired, claims.claim_required_handler)
    if not config.settings.auth_enabled:
        return
    from region.api import routes_auth          # imported lazily: needs PyJWT and httpx
    from region.trust import adapter
    from region.trust.true_adapter import build_from_settings

    adapter.set_adapter(build_from_settings())
    app.include_router(routes_auth.router)
