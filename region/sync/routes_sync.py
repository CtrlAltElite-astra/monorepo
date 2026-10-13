"""/internal/v1/sync/* (owner: Simrit). Peer-only; blocked when the link is down."""
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from region import config, store
from region.sync import ingest, links, state

router = APIRouter(prefix="/internal/v1/sync", tags=["sync"])


def peer_guard(x_region: str = Header(None), x_peer_token: str = Header(None)) -> str:
    if x_peer_token != config.settings.peer_token or x_region not in config.settings.peers:
        raise HTTPException(401, "unknown peer")
    if links.is_down(x_region):  # inbound half of the partition switch
        raise HTTPException(503, "link_down")
    return x_region


_REGION_ID = re.compile(r"^[a-z]{2,8}$")
_LOOPBACK_URL = re.compile(r"^http://(127\.0\.0\.1|localhost):\d{2,5}$")


@router.post("/join")
def join(body: dict, x_peer_token: str = Header(None)):
    """Scaling out: a new region (Bengaluru) announces itself to a running one, which then pulls from it and pushes to it.

    Needs only the shared peer token, the same secret every peer already holds, so it works in sign-in mode too, where the
    admin route needs an admin session. The address must be a loopback http address and the id a short region id, so a
    holder of the token cannot point this region at an arbitrary host."""
    if x_peer_token != config.settings.peer_token:
        raise HTTPException(401, "unknown peer")
    name, url = str(body.get("name", "")), str(body.get("url", "")).rstrip("/")
    if not _REGION_ID.match(name) or name == config.settings.region or not _LOOPBACK_URL.match(url):
        raise HTTPException(422, "name must be a short region id and url a loopback http address")
    config.add_peer(name, url)
    return {"region": config.settings.region, "peers": sorted(config.settings.peers)}


@router.get("/summary")
def summary(peer: str = Depends(peer_guard)):
    with store.read() as conn:
        return {
            "region": config.settings.region,
            "heads": state.heads(),
            "max_seen": state.max_seen(),
            "event_count": store.event_count(conn),
        }


@router.get("/events")
def events(origin: str, after: int = 0, limit: int = Query(200, ge=1, le=200), peer: str = Depends(peer_guard)):
    with store.read() as conn:
        page = store.events_after(conn, origin, after, limit + 1)
    return {"events": page[:limit], "has_more": len(page) > limit}


@router.post("/push")
def push(body: dict, peer: str = Depends(peer_guard)):
    source = body.get("from", peer)
    events_list = body.get("events", [])
    stats = ingest.ingest(events_list, source=source)
    return {
        "stored": stats.get("stored", 0),
        "duplicate": stats.get("duplicate", 0),
        "rejected": stats.get("rejected_list", []),
        "heads": state.heads(),
    }
