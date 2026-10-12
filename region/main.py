"""App wiring (owner: Akshit). Run: uvicorn region.main:app --port 8001 --workers 1

Environment read here: CORS_ORIGINS (comma separated, default the web dev/preview origins; "*" allows any).
"""
import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from contracts import schemas
from region import config, store
from region.api import routes_reports, routes_review, routes_status, writes
from region.engine import signing
from region.sync import carry, client, routes_admin, routes_sync
from region.trust import true_hook

DEFAULT_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173", "http://127.0.0.1:4173")


def cors_origins() -> list:
    raw = os.environ.get("CORS_ORIGINS", "")
    items = [o.strip() for o in raw.split(",") if o.strip()]
    return items or list(DEFAULT_ORIGINS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init()
    if not (signing.can_sign(config.settings.region) and signing.has_key(config.settings.region)):
        signing.ensure_keys([config.settings.region])  # this region signs every event it writes (run_one provisions all up front)
    writes.set_loop(asyncio.get_running_loop())     # lets the write path hand events to the push queue
    task = asyncio.create_task(client.run_forever())
    try:
        yield
    finally:
        writes.set_loop(None)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)  # let the pull loop stop before the DB closes
        store.close()


app = FastAPI(title=f"SyncShield region {config.settings.region}", version="1.1", lifespan=lifespan)


@app.middleware("http")
async def limit_body(request: Request, call_next):
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > schemas.MAX_BODY_BYTES:
        return JSONResponse(status_code=422, content={"detail": f"body too large (max {schemas.MAX_BODY_BYTES} bytes)"})
    return await call_next(request)


app.add_middleware(  # added last = outermost, so even the 422 above carries CORS headers
    CORSMiddleware, allow_origins=cors_origins(), allow_methods=["*"],
    allow_headers=["X-Api-Key", "Authorization", "Content-Type", "Idempotency-Key", "X-Admin-Key"],
    expose_headers=["Idempotent-Replay", "Retry-After"],
)
app.include_router(routes_reports.router)
app.include_router(routes_review.router)
app.include_router(routes_status.router)
app.include_router(routes_sync.router)
app.include_router(routes_admin.router)
app.include_router(carry.router)
true_hook.install(app)  # no-op unless AUTH_ENABLED=true (TRUE credential sessions)
