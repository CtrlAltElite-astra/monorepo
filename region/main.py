"""App wiring (owner: Akshit). Run: uvicorn region.main:app --port 8001 --workers 1"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from region import config, store
from region.api import routes_reports, routes_status
from region.sync import carry, client, routes_admin, routes_sync


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init()
    task = asyncio.create_task(client.run_forever())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)  # let the pull loop stop before the DB closes
        store.close()


app = FastAPI(title=f"SyncShield region {config.settings.region}", version="1", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(routes_reports.router)
app.include_router(routes_status.router)
app.include_router(routes_sync.router)
app.include_router(routes_admin.router)
app.include_router(carry.router)
