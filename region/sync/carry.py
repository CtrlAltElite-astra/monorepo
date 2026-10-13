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
    revoked_credential  validly signed, but written for a credential the issuer has since revoked (sign-in mode; a region
                     that has heard of the revocation refuses it; claims.is_revoked reads the signed status snapshot)
    then             the same idempotent ingest() as replication: "new" or "dup"

A rejected event is kept in the quarantine table as evidence, never applied. A browser can therefore
only help: it cannot alter, forge or reorder history, and replays are ignored by event id.
ENABLE_CARRY=0 switches the endpoint off (503) without touching replication.
"""
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from contracts.schemas import EVENT_REQUIRED
from region import config, store
from region.auth import require_key
from region.engine import admit, canonical, signing
from region.trust import claims
from region.sync import ingest, state

router = APIRouter(tags=["carry"])

MAX_CARRY = 200
MAX_HAS = 500
MAX_BODY_BYTES = 512 * 1024
LEVEL_TTL_S = 1.0  # /healthz is probed constantly; the level is recomputed at most this often

# In-memory carry counters for /v1/status
_carry_metrics = {"received": 0, "new": 0, "dup": 0, "rejected": 0}
_level_cache: tuple[float, str] = (0.0, "HEALTHY")


def get_carry_metrics() -> dict:
    return dict(_carry_metrics)


def reset_carry_metrics() -> None:
    for k in _carry_metrics:
        _carry_metrics[k] = 0


def _level() -> str:
    global _level_cache
    now = time.monotonic()
    if now - _level_cache[0] > LEVEL_TTL_S:
        _level_cache = (now, state.snapshot()["level"])
    return _level_cache[1]


@router.get("/healthz")
def healthz():
    return {"region": config.settings.region, "ok": True, "level": _level(), "ts": int(time.time() * 1000)}


class HasBody(BaseModel):
    ids: list[str] = Field(max_length=MAX_HAS)


@router.post("/v1/events/has")
def events_has(body: HasBody, auth=Depends(require_key)):
    with store.read() as conn:
        return {"missing": store.missing_ids(conn, body.ids)}


def _reject(event, reason: str, source: str = "") -> dict:
    _carry_metrics["rejected"] += 1
    eid = event.get("event_id") if isinstance(event, dict) else None
    if source:
        with store.tx() as conn:
            store.quarantine(conn, event, source, reason)
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
    # Validly signed, but written for a credential that has since been revoked (engine rule: region/engine/admit.py).
    # Only this untrusted browser channel applies it: replication keeps history that was valid when it was written.
    if admit.reject_reason(ev, is_revoked=claims.is_revoked) == "revoked_credential":
        return "revoked_credential"
    return ""


@router.post("/v1/carry")
async def carry(request: Request, auth=Depends(require_key)):
    if not config.settings.carry_enabled:
        raise HTTPException(503, "carry_disabled")
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
    source = f"carry:{auth.actor}"
    for ev in events:
        _carry_metrics["received"] += 1
        reason = _check(ev)
        if reason:
            results.append(_reject(ev, reason, source))
            continue
        if ev["origin"] == me:
            with store.read() as conn:
                have = store.event_by_id(conn, ev["event_id"])
            if have is None:
                results.append(_reject(ev, "own_origin", source))
                continue
        stats = ingest.ingest([ev], source=source)  # quarantines what it rejects
        if stats["rejected"]:
            _carry_metrics["rejected"] += 1
            results.append({"event_id": ev["event_id"], "result": "rejected",
                            "reason": (stats["rejected_list"] or [{}])[0].get("reason", "rejected")})
        elif stats["new"]:
            _carry_metrics["new"] += 1
            with store.tx() as conn:  # remembered so GET /v1/reports/{id}/history can show "carried by a browser"
                store.mark_carried(conn, [ev["event_id"]])
            results.append({"event_id": ev["event_id"], "result": "new"})
        else:
            _carry_metrics["dup"] += 1
            results.append({"event_id": ev["event_id"], "result": "dup"})
    return {"results": results}
