"""GET /v1/status and /v1/health (owner: Akshit mounts; Simrit's region/sync/status.py builds the payload).

Status v1.1 (contract 5.4): heads, peers (acked, last_ok_ms), ladder level, digest and state hash, quarantine
count, data-at-risk meter, priority queue depth and this node's carry counters.
"""
from fastapi import APIRouter

from region import config
from region.sync import status as status_builder

router = APIRouter(prefix="/v1", tags=["status"])


@router.get("/health")
def health():
    return {"region": config.settings.region, "ok": True}


@router.get("/status")
def status():
    return status_builder.build_status()
