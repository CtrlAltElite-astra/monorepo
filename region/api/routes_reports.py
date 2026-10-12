"""POST/PATCH/GET /v1/reports (owner: Akshit)."""
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from contracts import schemas
from region import config, store
from region.api import writes
from region.auth import require_key

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


class PatchReport(BaseModel):
    field: str
    value: Any  # required, but null is allowed (clears assigned_team, location, people_estimate)
    expected: Optional[list[str]] = None


class NoteBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def build_report(conn, report_id: str) -> Optional[dict]:
    """Stored view + replication badge (4.3)."""
    view = store.get_view(conn, report_id)
    if view is None:
        return None
    return {
        "report_id": report_id,
        "state": view["state"],
        "live": view["live"],
        "conflicts": view["conflicts"],
        "incomplete": view["incomplete"],
        "consistency": {"replication": _replication(conn, report_id)},
    }


def _replication(conn, report_id: str) -> str:
    """LOCAL_ONLY | PARTIAL | REPLICATED from what each peer last reported."""
    peers = list(config.settings.peers)
    if not peers:
        return "LOCAL_ONLY"
    needed = store.report_origin_max(conn, report_id)
    confirmed = 0
    for peer in peers:
        have = {r[0]: r[1] for r in conn.execute("SELECT origin, seq FROM peer_view WHERE peer=?", (peer,))}
        if all(have.get(o, 0) >= s for o, s in needed.items()):
            confirmed += 1
    return "REPLICATED" if confirmed == len(peers) else "PARTIAL" if confirmed else "LOCAL_ONLY"


def _ok(ev: dict, status: int):
    with store.read() as conn:
        report = build_report(conn, ev["report_id"])
    return JSONResponse(
        status_code=status,
        content={"report": report, "event_id": ev["event_id"], "consistency": report["consistency"]},
    )


@router.post("/reports", status_code=201)
def create_report(body: CreateReport, auth=Depends(require_key)):
    if body.category not in schemas.CATEGORIES:
        raise HTTPException(422, f"category must be one of {schemas.CATEGORIES}")
    value = {
        "title": body.title, "category": body.category, "severity": body.severity,
        "status": "open", "assigned_team": None,
        "location": body.location.model_dump() if body.location else None,
        "description": body.description or "", "people_estimate": body.people_estimate,
    }
    ev = writes.local_write(None, "CREATE", None, value, auth.actor)
    return _ok(ev, 201)


@router.patch("/reports/{report_id}")
def patch_report(report_id: str, body: PatchReport, auth=Depends(require_key)):
    try:
        value = schemas.validate_field(body.field, body.value)
    except ValueError as e:
        raise HTTPException(422, str(e))
    try:
        ev = writes.local_write(report_id, "SET", body.field, value, auth.actor, expected=body.expected)
    except writes.NotFound:
        raise HTTPException(404, "unknown report")
    except writes.StaleEdit as e:
        return JSONResponse(status_code=409, content={"error": "stale_edit", "live": e.live})
    return _ok(ev, 200)


@router.post("/reports/{report_id}/notes", status_code=201)
def add_note(report_id: str, body: NoteBody, auth=Depends(require_key)):
    try:
        ev = writes.local_write(report_id, "NOTE", None, {"text": body.text}, auth.actor)
    except writes.NotFound:
        raise HTTPException(404, "unknown report")
    return _ok(ev, 201)


@router.get("/reports")
def list_reports(limit: int = Query(100, ge=0), cursor: int = Query(0, ge=0)):
    """Newest first (contracts.md 4.3); `limit` is clamped to 1..500, `cursor` is an offset."""
    with store.read() as conn:
        ids = store.report_ids_newest_first(conn, max(1, min(limit, 500)), cursor)
        return [build_report(conn, rid) for rid in ids]


@router.get("/reports/{report_id}")
def get_report(report_id: str):
    with store.read() as conn:
        report = build_report(conn, report_id)
    if report is None:
        raise HTTPException(404, "unknown report")
    return report
