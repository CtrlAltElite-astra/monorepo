"""POST / PATCH / GET /v1/reports, notes and history (owner: Akshit).

Write responses (contract v1.1): {"report": <view>, "event": <full signed envelope>, "event_id", "consistency"}.
A retried write (same op_id, or the Idempotency-Key header) answers 200 with the original event and the header
`Idempotent-Replay: true`; the same op_id with a different body is 409 op_id_reuse.

Contract v1.2 (8.3): while a per-actor limit is set (region/api/ratelimit.py) every answer to a write that reached
the write path carries `X-RateLimit-Limit` and `X-RateLimit-Remaining`; an actor with no tokens left gets 429
`rate_limited` (built in region/main.py). Reads are never limited.
"""
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from contracts import schemas
from region import config, store
from region.api import ratelimit, receipts, writes
from region.auth import read_context, require
from region.engine import canonical
from region.trust import ownership
from region.engine.derive import order
from region.sync import state
from region.trust import signing

router = APIRouter(prefix="/v1", tags=["reports"])

_OP = dict(default=None, min_length=1, max_length=schemas.OP_ID_MAX)


class Location(BaseModel):
    lat_e6: int
    lon_e6: int


class CreateReport(BaseModel):
    report_id: Optional[str] = Field(default=None, pattern=schemas.CLIENT_REPORT_ID_PATTERN)
    op_id: Optional[str] = Field(**_OP)
    title: str = Field(min_length=1, max_length=schemas.MAX_TITLE)
    category: str
    severity: int = Field(ge=1, le=5)
    location: Optional[Location] = None
    description: Optional[str] = Field(default=None, max_length=schemas.MAX_DESCRIPTION)
    people_estimate: Optional[int] = Field(default=None, ge=0)


class PatchReport(BaseModel):
    field: str
    value: Any  # required, but null is allowed (clears assigned_team, location, people_estimate)
    expected: Optional[list[str]] = Field(default=None, max_length=schemas.MAX_SUPERSEDES)
    op_id: Optional[str] = Field(**_OP)


class NoteBody(BaseModel):
    text: str = Field(min_length=1, max_length=schemas.MAX_NOTE)
    op_id: Optional[str] = Field(**_OP)


def _reachability() -> dict:
    """{peer: reachable now}, from the sync layer's own peer bookkeeping."""
    return {p: v["reachable"] for p, v in state.snapshot()["peers"].items()}


def build_report(conn, report_id: str, reachable: Optional[dict] = None) -> Optional[dict]:
    """Stored view + receipts + consistency block. Pass `reachable` when building many reports at once."""
    view = store.get_view(conn, report_id)
    if view is None:
        return None
    return {
        "report_id": report_id,
        "state": view["state"],
        "live": view["live"],
        "conflicts": [receipts.receipt(conn, view, c) for c in view["conflicts"]],
        "incomplete": view["incomplete"],
        "consistency": _consistency(conn, report_id, _reachability() if reachable is None else reachable),
    }


def _consistency(conn, report_id: str, reachable: dict) -> dict:
    """Replication badge plus which peers hold the report (PDF 6.8).

    A peer confirms a report when its last summary shows it holds every event of the report
    (peer_view[peer][origin] >= the highest seq of that origin among the report's events).
    stale_possible: any peer is unreachable right now, so a newer edit may exist that we cannot see.
    """
    peers = sorted(config.settings.peers)
    needed = store.report_origin_max(conn, report_id)
    confirmed, awaiting = [], []
    for peer in peers:
        have = {r[0]: r[1] for r in conn.execute("SELECT origin, seq FROM peer_view WHERE peer=?", (peer,))}
        (confirmed if all(have.get(o, 0) >= s for o, s in needed.items()) else awaiting).append(peer)
    replication = "LOCAL_ONLY" if not confirmed else "REPLICATED" if not awaiting else "PARTIAL"
    return {
        "replication": replication,
        "confirmed_by": confirmed,
        "awaiting": awaiting,
        "stale_possible": any(not reachable.get(p, False) for p in peers),
    }


def respond(result: writes.WriteResult, status: int) -> JSONResponse:
    """The 2xx body of every write: the view, the signed event, and (for a replay) the replay header."""
    ev = result.event
    with store.read() as conn:
        report = build_report(conn, ev["report_id"])
    body = {"report": report, "event": ev, "event_id": ev["event_id"], "consistency": report["consistency"]}
    allowance = ratelimit.headers(result.rate)  # {} while the limit is off
    if result.replay:
        return JSONResponse(status_code=200, content=body, headers={"Idempotent-Replay": "true", **allowance})
    return JSONResponse(status_code=status, content=body, headers=allowance)


def failure(exc: Exception) -> JSONResponse:
    """409 / 404 answers shared by every write route. A write that was charged a token and then refused for its
    own reason still reports the caller's allowance."""
    allowance = ratelimit.headers(getattr(exc, "rate", None))
    if isinstance(exc, writes.NotFound):
        raise HTTPException(404, "unknown report", headers=allowance or None)
    if isinstance(exc, writes.StaleEdit):
        return JSONResponse(status_code=409, content={"error": "stale_edit", "live": exc.live}, headers=allowance)
    if isinstance(exc, writes.OpIdReuse):
        return JSONResponse(status_code=409, content={"error": "op_id_reuse"})
    if isinstance(exc, writes.ReportExists):
        return JSONResponse(status_code=409, content={"error": "report_exists"}, headers=allowance)
    if isinstance(exc, writes.NoConflict):
        return JSONResponse(status_code=409, content={"error": "no_conflict"}, headers=allowance)
    raise exc


def _op(body_op: Optional[str], header_op: Optional[str]) -> Optional[str]:
    return body_op or (header_op[: schemas.OP_ID_MAX] if header_op else None)


def _prov(auth) -> Optional[dict]:
    """Auth mode: provenance for the signed event, taken ONLY from the verified session."""
    if not config.settings.auth_enabled:
        return None
    return {"role": auth.role, "auth_mode": auth.mode, "credential_id": auth.credential_id}


def _guard_write(report_id: str, auth) -> None:
    """Auth mode: a cart may be edited only by its owner. Other people's carts do not exist for officers."""
    if not config.settings.auth_enabled:
        return
    with store.read() as conn:
        if not store.report_exists(conn, report_id):
            return                                   # the write path answers 404
        owner = ownership.report_owner(conn, report_id)
    if not ownership.can_read(auth, owner):
        raise HTTPException(404, "unknown report")   # no existence leak
    if not ownership.can_write(auth, owner):
        raise HTTPException(403, detail={"code": "not_your_cart", "message": "Only the cart owner may edit it"})


@router.post("/reports", status_code=201)
def create_report(body: CreateReport, auth=Depends(require("create")),
                  idempotency_key: Optional[str] = Header(None)):
    if body.category not in schemas.CATEGORIES:
        raise HTTPException(422, f"category must be one of {schemas.CATEGORIES}")
    value = {
        "title": body.title, "category": body.category, "severity": body.severity,
        "status": "open", "assigned_team": None,
        "location": body.location.model_dump() if body.location else None,
        "description": body.description or "", "people_estimate": body.people_estimate,
    }
    try:
        result = writes.local_write(body.report_id, "CREATE", None, value, auth.actor,
                                    op_id=_op(body.op_id, idempotency_key), role=auth.role, auth_mode=auth.mode,
                                    provenance=_prov(auth), rate_key=ratelimit.key(auth))
    except (writes.OpIdReuse, writes.ReportExists) as exc:
        return failure(exc)
    return respond(result, 201)


@router.patch("/reports/{report_id}")
def patch_report(report_id: str, body: PatchReport, auth=Depends(require("set")),
                 idempotency_key: Optional[str] = Header(None)):
    try:
        value = schemas.validate_field(body.field, body.value)
    except ValueError as e:
        raise HTTPException(422, str(e))
    _guard_write(report_id, auth)
    try:
        result = writes.local_write(report_id, "SET", body.field, value, auth.actor, expected=body.expected,
                                    op_id=_op(body.op_id, idempotency_key), role=auth.role, auth_mode=auth.mode,
                                    provenance=_prov(auth), rate_key=ratelimit.key(auth))
    except (writes.NotFound, writes.StaleEdit, writes.OpIdReuse) as exc:
        return failure(exc)
    return respond(result, 200)


@router.post("/reports/{report_id}/notes", status_code=201)
def add_note(report_id: str, body: NoteBody, auth=Depends(require("note")),
             idempotency_key: Optional[str] = Header(None)):
    _guard_write(report_id, auth)
    try:
        result = writes.local_write(report_id, "NOTE", None, {"text": body.text}, auth.actor,
                                    op_id=_op(body.op_id, idempotency_key), role=auth.role, auth_mode=auth.mode,
                                    provenance=_prov(auth), rate_key=ratelimit.key(auth))
    except (writes.NotFound, writes.OpIdReuse) as exc:
        return failure(exc)
    return respond(result, 201)


@router.get("/reports")
def list_reports(limit: int = Query(100, ge=0), cursor: int = Query(0, ge=0), auth=Depends(read_context)):
    """Newest first (contracts.md 4.3); `limit` is clamped to 1..500, `cursor` is an offset.
    Auth mode: only carts the caller may read (region/trust/ownership.py)."""
    reachable = _reachability()  # once per request, not once per report
    lim = max(1, min(limit, 500))
    with store.read() as conn:
        if auth is None:
            ids = store.report_ids_newest_first(conn, lim, cursor)
        else:
            owners = ownership.owners(conn)
            everything = store.report_ids_newest_first(conn, 100000, 0)
            ids = [r for r in everything if ownership.can_read(auth, owners.get(r))][cursor:cursor + lim]
        return [build_report(conn, rid, reachable) for rid in ids]


@router.get("/reports/{report_id}")
def get_report(report_id: str, auth=Depends(read_context)):
    with store.read() as conn:
        report = build_report(conn, report_id)
        if report is not None and auth is not None and not ownership.can_read(auth, ownership.report_owner(conn, report_id)):
            report = None
    if report is None:
        raise HTTPException(404, "unknown report")
    return report


def sig_status(ev: dict) -> str:
    """verified | bad | unsigned. `bad` also covers an event whose body no longer matches its hash."""
    if ev.get("sig") is None:
        return "unsigned"
    if canonical.event_hash(ev) != ev.get("hash"):
        return "bad"
    return "verified" if signing.verify(ev.get("origin"), ev["hash"], ev["sig"]) else "bad"


@router.get("/reports/{report_id}/history")
def report_history(report_id: str, auth=Depends(read_context)):
    """Audit trail: every event of the report (any origin) in tie-break (HLC) order, exactly as stored and
    replicated, each with sig_status and whether it reached this node through POST /v1/carry.
    Auth mode: only for carts the caller may read."""
    with store.read() as conn:
        events = sorted(store.events_for(conn, report_id), key=order)
        if events and auth is not None and not ownership.can_read(auth, ownership.report_owner(conn, report_id)):
            events = []
        carried = store.carried_ids(conn, [e["event_id"] for e in events])
    if not events:
        raise HTTPException(404, "unknown report")
    return [{**e, "sig_status": sig_status(e), "carried": e["event_id"] in carried} for e in events]
