"""GET /v1/review and POST /v1/reports/{id}/resolve (plan v1.1 section 6.7).

A review item is a conflict receipt with needs_review. Resolving writes a RESOLVE event that supersedes exactly
the live candidates, so it replicates like any other event and a write that had not seen it re-opens review.
409 {"error": "stale_resolve", "live": [...]} when the candidates changed meanwhile;
409 {"error": "no_conflict"} when there is nothing to decide.
"""
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from contracts import schemas
from region import config, store
from region.api import writes
from region.api.routes_reports import _ok
from region.auth import read_context, require
from region.trust import ownership

router = APIRouter(prefix="/v1", tags=["review"])


class ResolveBody(BaseModel):
    field: str
    value: Any = None
    candidates: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=500)
    op_id: Optional[str] = Field(default=None, min_length=1, max_length=80)


@router.get("/review")
def review_queue(auth=Depends(read_context)):
    """Open review items this caller may see. Officers see only their own carts."""
    with store.read() as conn:
        owners = ownership.owners(conn) if auth is not None else {}
        out = []
        for rid in store.report_ids_newest_first(conn, 100000, 0):
            if auth is not None and not ownership.can_read(auth, owners.get(rid)):
                continue
            view = store.get_view(conn, rid)
            for c in (view or {}).get("conflicts", []):
                if c.get("needs_review"):
                    out.append({"report_id": rid, "title": view["state"].get("title"), "field": c["field"], "receipt": c})
        return out


@router.post("/reports/{report_id}/resolve")
def resolve(report_id: str, body: ResolveBody, auth=Depends(require("resolve"))):
    try:
        value = schemas.validate_field(body.field, body.value)
    except ValueError as e:
        raise HTTPException(422, str(e))
    with store.read() as conn:
        view = store.get_view(conn, report_id)
    if view is None:
        raise HTTPException(404, "unknown report")
    if not any(c["field"] == body.field and c.get("needs_review") for c in view["conflicts"]):
        return JSONResponse(status_code=409, content={"error": "no_conflict"})
    prov = ({"role": auth.role, "auth_mode": auth.mode, "credential_id": auth.credential_id}
            if config.settings.auth_enabled else None)
    try:
        ev, replayed = writes.local_write_ex(
            report_id, "RESOLVE", body.field, value, auth.actor,
            expected=body.candidates, op_id=body.op_id, provenance=prov, reason=body.reason)
    except writes.NotFound:
        raise HTTPException(404, "unknown report")
    except writes.StaleEdit as e:
        return JSONResponse(status_code=409, content={"error": "stale_resolve", "live": e.live})
    except writes.OpIdReuse:
        return JSONResponse(status_code=409, content={"error": "op_id_reuse"})
    return _ok(ev, 201, replayed)
