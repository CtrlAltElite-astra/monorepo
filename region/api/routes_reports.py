"""POST/PATCH/GET /v1/reports (owner: Akshit; v1.1 op_id / signed event by Miti)."""
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from contracts import schemas
from region import config, store
from region.api import writes
from region.auth import read_context, require
from region.trust import ownership
from region.engine.derive import order
from region.sync import state

router = APIRouter(prefix="/v1", tags=["reports"])


class Location(BaseModel):
    lat_e6: int
    lon_e6: int


class CreateReport(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    category: str
    severity: int = Field(ge=1, le=5)
    location: Optional[Location] = None
    description: Optional[str] = Field(default=None, max_length=2000)
    people_estimate: Optional[int] = Field(default=None, ge=0)
    # v1.1 (optional): a retried create with the same op_id is applied once
    op_id: Optional[str] = Field(default=None, min_length=1, max_length=80)
    report_id: Optional[str] = Field(default=None, max_length=40)


class PatchReport(BaseModel):
    field: str
    value: Any  # required, but null is allowed (clears assigned_team, location, people_estimate)
    expected: Optional[list[str]] = None
    op_id: Optional[str] = Field(default=None, min_length=1, max_length=80)


class NoteBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    op_id: Optional[str] = Field(default=None, min_length=1, max_length=80)


def _reachability() -> dict:
    """{peer: reachable now}, from the sync layer's own peer bookkeeping."""
    return {p: v["reachable"] for p, v in state.snapshot()["peers"].items()}


def build_report(conn, report_id: str, reachable: Optional[dict] = None) -> Optional[dict]:
    """Stored view + consistency block. Pass `reachable` when building many reports at once."""
    view = store.get_view(conn, report_id)
    if view is None:
        return None
    return {
        "report_id": report_id,
        "state": view["state"],
        "live": view["live"],
        "conflicts": view["conflicts"],
        "incomplete": view["incomplete"],
        "consistency": _consistency(conn, report_id, _reachability() if reachable is None else reachable),
    }


def _consistency(conn, report_id: str, reachable: dict) -> dict:
    """Replication badge plus which peers hold the report (PDF 2 6.8).

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


def _ok(ev: dict, status: int, replayed: bool = False):
    """201/200 body: the report view and, since v1.1, the full signed event (the SDK carries it on failover)."""
    with store.read() as conn:
        report = build_report(conn, ev["report_id"])
    return JSONResponse(
        status_code=200 if replayed else status,
        content={"report": report, "event_id": ev["event_id"], "event": ev, "consistency": report["consistency"]},
        headers={"Idempotent-Replay": "true"} if replayed else None,
    )


def _op(body_op, idem_header) -> Optional[str]:
    return body_op or idem_header


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
                  idempotency_key: Optional[str] = Header(default=None)):
    if body.category not in schemas.CATEGORIES:
        raise HTTPException(422, f"category must be one of {schemas.CATEGORIES}")
    if body.report_id is not None and not writes.CLIENT_REPORT_ID.match(body.report_id):
        raise HTTPException(422, "report_id must look like R-<8 hex characters>")
    value = {
        "title": body.title, "category": body.category, "severity": body.severity,
        "status": "open", "assigned_team": None,
        "location": body.location.model_dump() if body.location else None,
        "description": body.description or "", "people_estimate": body.people_estimate,
    }
    try:
        ev, replayed = writes.local_write_ex(None, "CREATE", None, value, auth.actor,
                                             op_id=_op(body.op_id, idempotency_key), new_report_id=body.report_id,
                                             provenance=_prov(auth))
    except writes.OpIdReuse:
        return JSONResponse(status_code=409, content={"error": "op_id_reuse"})
    except writes.ReportExists:
        return JSONResponse(status_code=409, content={"error": "report_exists"})
    return _ok(ev, 201, replayed)


@router.patch("/reports/{report_id}")
def patch_report(report_id: str, body: PatchReport, auth=Depends(require("set")),
                 idempotency_key: Optional[str] = Header(default=None)):
    try:
        value = schemas.validate_field(body.field, body.value)
    except ValueError as e:
        raise HTTPException(422, str(e))
    _guard_write(report_id, auth)
    try:
        ev, replayed = writes.local_write_ex(report_id, "SET", body.field, value, auth.actor,
                                             expected=body.expected, op_id=_op(body.op_id, idempotency_key),
                                             provenance=_prov(auth))
    except writes.NotFound:
        raise HTTPException(404, "unknown report")
    except writes.StaleEdit as e:
        return JSONResponse(status_code=409, content={"error": "stale_edit", "live": e.live})
    except writes.OpIdReuse:
        return JSONResponse(status_code=409, content={"error": "op_id_reuse"})
    return _ok(ev, 200, replayed)


@router.post("/reports/{report_id}/notes", status_code=201)
def add_note(report_id: str, body: NoteBody, auth=Depends(require("note")),
             idempotency_key: Optional[str] = Header(default=None)):
    _guard_write(report_id, auth)
    try:
        ev, replayed = writes.local_write_ex(report_id, "NOTE", None, {"text": body.text}, auth.actor,
                                             op_id=_op(body.op_id, idempotency_key), provenance=_prov(auth))
    except writes.NotFound:
        raise HTTPException(404, "unknown report")
    except writes.OpIdReuse:
        return JSONResponse(status_code=409, content={"error": "op_id_reuse"})
    return _ok(ev, 201, replayed)


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


@router.get("/reports/{report_id}/history")
def report_history(report_id: str, auth=Depends(read_context)):
    """Audit trail: every event of the report (any origin) in tie-break (HLC) order, exactly as
    stored and replicated, so `supersedes`, `actor`, `hash` and `sig` are visible."""
    with store.read() as conn:
        events = store.events_for(conn, report_id)
        if events and auth is not None and not ownership.can_read(auth, ownership.report_owner(conn, report_id)):
            events = []
    if not events:
        raise HTTPException(404, "unknown report")
    return sorted(events, key=order)
