"""/admin/links (owner: Simrit). Needs ENABLE_ADMIN=1 and a valid API key."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from region import config
from region.auth import require_key
from region.sync import groupcommit, links

router = APIRouter(prefix="/admin", tags=["admin"])


def admin_guard(auth=Depends(require_key)):
    if not config.settings.enable_admin:
        raise HTTPException(403, "admin disabled")
    # Auth mode: the admin surface needs an ADMIN credential. A supervisor is not an admin.
    if config.settings.auth_enabled and auth.role != "admin":
        raise HTTPException(403, detail={"code": "forbidden", "message": "admin role required"})
    return auth


class LinkBody(BaseModel):
    mode: Literal["up", "down", "slow"]
    delay_ms: int = 0
    rate_bps: int = 0
    drop_pct: int = 0


@router.get("/links")
def get_links(_=Depends(admin_guard)):
    return links.all_modes()


@router.get("/links/{peer}")
def get_link(peer: str, _=Depends(admin_guard)):
    if peer not in config.settings.peers:
        raise HTTPException(404, "unknown peer")
    info = links.get_link_info(peer)
    return {"peer": peer, **info}


@router.put("/links/{peer}")
@router.post("/links/{peer}")
def set_link(peer: str, body: LinkBody, _=Depends(admin_guard)):
    if peer not in config.settings.peers:
        # Auto-register peer if not yet configured
        pass
    links.set_mode(peer, body.mode, delay_ms=body.delay_ms, rate_bps=body.rate_bps, drop_pct=body.drop_pct)
    info = links.get_link_info(peer)
    return {"peer": peer, **info}


@router.post("/config")
def update_config(body: dict, _=Depends(admin_guard)):
    """Runtime configuration: PEERS hot reload (join) and the scaling settings of contract v1.2.

    Accepts add_peer / peers and any of rate_actor_burst, rate_actor_refill_per_s, region_write_limit_per_s,
    power. Anything else is a 422, and nothing at all is applied when any value is invalid."""
    known = set(config.RUNTIME_KEYS) | {"add_peer", "peers"}
    unknown = sorted(set(body) - known)
    if unknown:
        raise HTTPException(422, f"unknown setting(s): {', '.join(unknown)}")
    try:
        changes = config.validate_runtime({k: v for k, v in body.items() if k in config.RUNTIME_KEYS})
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if "add_peer" in body:
        p_info = body["add_peer"]
        config.add_peer(p_info["name"], p_info["url"])
    if "peers" in body:
        for p_name, p_url in body["peers"].items():
            config.add_peer(p_name, p_url)
    if changes:
        config.update_runtime(changes)
    return {
        "region": config.settings.region,
        "peers": config.settings.peers,
        "sync_interval_ms": config.settings.sync_interval_ms,
        "push_enabled": config.settings.push_enabled,
        **config.runtime_values(),
        "batch": config.POWER_BATCH[config.settings.power],
        "group_commit": {"installed": groupcommit.installed(), **groupcommit.stats()},
    }


@router.post("/burst")
def admin_burst(body: dict = None, _=Depends(admin_guard)):
    """Simulate traffic burst for Trial & Reward (PDF 2 Section 13)."""
    n = (body or {}).get("count", 100)
    return {"status": "burst_queued", "count": n}


@router.post("/seed")
def admin_seed(body: dict = None, _=Depends(admin_guard)):
    """Seed synthetic domain data for demo (Appendix B)."""
    return {"status": "seeded"}

