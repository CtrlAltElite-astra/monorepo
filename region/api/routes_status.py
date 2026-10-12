"""GET /v1/status and /v1/health (owner: Akshit + Simrit)."""
from fastapi import APIRouter

from contracts.schemas import CONTRACT_VERSION
from region import config
from region.sync import status as status_builder

router = APIRouter(prefix="/v1", tags=["status"])


@router.get("/health")
def health():
    return {"region": config.settings.region, "ok": True}


@router.get("/status")
def status():
    return status_builder.build_status()

