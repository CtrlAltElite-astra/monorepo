"""Zero-loss failover endpoints (plan v1.1 sections 5.3 and 6.2).

    GET  /healthz          liveness + level, no auth, reads only. The browser SDK probes this.
    POST /v1/events/has    which of these event ids this node does NOT hold.
    POST /v1/carry         a browser hands over signed events it holds from a node that died.

Carry is the one route that accepts events from an UNTRUSTED sender, so every event is checked:

    shape            required keys present, event_id == "<origin>:<seq>"
    unknown_origin   the origin has no registered public key (cannot be verified, fails closed)
    bad_hash         the hash does not match the content (the browser edited it)
    bad_sig          missing or invalid signature (the browser forged it)
    own_origin       an event of THIS node that this node does not hold: never imported (would
                     reuse a sequence number); it was committed before it was acknowledged
    then             the same idempotent ingest() as replication: "new" or "dup"

A browser can therefore only help: it cannot alter, forge or reorder history, and replays are
ignored by event id.
"""
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from contracts.schemas import EVENT_REQUIRED
from region import config, store
from region.auth import require_key
from region.engine import canonical, signing
from region.sync import ingest, state

router = APIRouter(tags=["carry"])

MAX_CARRY = 200
MAX_HAS = 500
MAX_BODY_BYTES = 512 * 1024

_stats = {"received": 0, "new": 0, "dup": 0, "rejected": 0}


def carry_stats() -> dict:
    return dict(_stats)


@router.get("/healthz")
def healthz():
    return {"region": config.settings.region, "ok": True, "level": state.snapshot()["level"],
            "ts": int(time.time() * 1000)}


class HasBody(BaseModel):
    ids: list[str] = Field(max_length=MAX_HAS)


@router.post("/v1/events/has")
def events_has(body: HasBody, auth=Depends(require_key)):
    with store.read() as conn:
        return {"missing": store.missing_ids(conn, body.ids)}


def _reject(event, reason: str) -> dict:
    _stats["rejected"] += 1
    eid = event.get("event_id") if isinstance(event, dict) else None
    return {"event_id": eid, "result": "rejected", "reason": reason}


def _check(ev) -> str:
    """Return a rejection reason, or '' when the event may be handed to ingest."""
    if not isinstance(ev, dict) or any(k not in ev for k in EVENT_REQUIRED):
        return "shape"
    if ev.get("event_id") != f"{ev.get('origin')}:{ev.get('seq')}" or not isinstance(ev.get("seq"), int):
        return "shape"
    if not isinstance(ev.get("hlc"), list) or len(ev["hlc"]) != 2 or not isinstance(ev.get("supersedes"), list):
        return "shape"
    if not signing.has_key(ev["origin"]):
        return "unknown_origin"
    if canonical.event_hash(ev) != ev["hash"]:
        return "bad_hash"
    if not signing.verify(ev["origin"], ev["hash"], ev.get("sig")):
        return "bad_sig"
    return ""


@router.post("/v1/carry")
async def carry(request: Request, auth=Depends(require_key)):
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(413, "body too large")
    try:
        events = json.loads(raw).get("events")
    except (ValueError, AttributeError):
        raise HTTPException(422, "body must be {\"events\": [...]}")
    if not isinstance(events, list):
        raise HTTPException(422, "events must be a list")
    if len(events) > MAX_CARRY:
        raise HTTPException(422, f"at most {MAX_CARRY} events per carry")

    results = []
    me = config.settings.region
    for ev in events:
        _stats["received"] += 1
        reason = _check(ev)
        if reason:
            results.append(_reject(ev, reason))
            continue
        if ev["origin"] == me:
            with store.read() as conn:
                have = store.event_by_id(conn, ev["event_id"])
            if have is None:
                results.append(_reject(ev, "own_origin"))
                continue
        stats = ingest.ingest([ev], source=f"carry:{auth.actor}")
        if stats["rejected"]:
            _stats["rejected"] += 1
            results.append({"event_id": ev["event_id"], "result": "rejected",
                            "reason": (stats["rejected_list"] or [{}])[0].get("reason", "rejected")})
        elif stats["new"]:
            _stats["new"] += 1
            with store.tx() as conn:  # remembered so GET /v1/reports/{id}/history can show "carried by a browser"
                store.mark_carried(conn, [ev["event_id"]])
            results.append({"event_id": ev["event_id"], "result": "new"})
        else:
            _stats["dup"] += 1
            results.append({"event_id": ev["event_id"], "result": "dup"})
    return {"results": results}
