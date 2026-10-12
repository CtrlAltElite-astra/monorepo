"""POST /v1/reports/{id}/resolve and GET /v1/review (owner: Akshit; receipts by region/api/receipts.py).

Resolve (supervisor only) writes a RESOLVE event that supersedes exactly the live candidates:
  409 {"error": "stale_edit", "live": [...]}   the candidates changed while the supervisor was looking
  409 {"error": "no_conflict"}                  the field has nothing to settle
  403                                           officers (and anyone but a supervisor) may not resolve
The decision replicates like any event. A write that had not seen it is concurrent with it and opens a
new review item: a resolution never hides an edit it did not see.
"""
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from contracts import schemas
from region import store
from region.api import receipts, writes
from region.api.routes_reports import failure, respond
from region.auth import require
from region.engine.derive import order

router = APIRouter(prefix="/v1", tags=["review"])


class ResolveBody(BaseModel):
    field: str
    value: Any
    candidates: list[str] = Field(min_length=1, max_length=schemas.MAX_SUPERSEDES)
    reason: str = Field(min_length=1, max_length=schemas.MAX_REASON)
    op_id: Optional[str] = Field(default=None, min_length=1, max_length=schemas.OP_ID_MAX)


@router.post("/reports/{report_id}/resolve", status_code=201)
def resolve_report(report_id: str, body: ResolveBody, auth=Depends(require("resolve")),
                   idempotency_key: Optional[str] = Header(None)):
    try:
        value = schemas.validate_field(body.field, body.value)
    except ValueError as e:
        raise HTTPException(422, str(e))
    op_id = body.op_id or (idempotency_key[: schemas.OP_ID_MAX] if idempotency_key else None)
    try:
        result = writes.resolve(report_id, body.field, value, body.candidates, body.reason, auth.actor,
                                role=auth.role, auth_mode=auth.mode, op_id=op_id)
    except (writes.NotFound, writes.StaleEdit, writes.NoConflict, writes.OpIdReuse) as exc:
        return failure(exc)
    return respond(result, 201)


@router.get("/review")
def review_queue():
    """Open review items on this node, newest first: [{report_id, title, field, receipt}]."""
    items = []
    with store.read() as conn:
        for row in conn.execute("SELECT report_id, view FROM reports WHERE needs_review=1 AND incomplete=0"):
            report_id = row[0]
            view = store.get_view(conn, report_id)
            created = min((order(e) for e in store.events_for(conn, report_id) if e["type"] == "CREATE"), default=(0,))
            for conflict in view["conflicts"]:
                if conflict.get("needs_review", True):
                    items.append((created, {"report_id": report_id, "title": view["state"].get("title"),
                                            "field": conflict["field"], "receipt": receipts.receipt(conn, view, conflict)}))
    items.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in items]
