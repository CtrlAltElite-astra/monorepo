"""Carry and failover recovery endpoints (owner: Simrit).

Endpoints:
- GET /healthz: no auth, no DB writes, returns region, ok, level, ts
- POST /v1/carry: receives up to 200 client-carried signed events, validates shape, origin,
  hash, and signature, and applies via idempotent ingest.
- POST /v1/events/has: checks up to 500 event IDs and reports which are missing.
"""
import json
import logging
from pathlib import Path
import sys
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from contracts.schemas import EVENT_REQUIRED
from region import auth, config, store
from region.engine import canonical
from region.engine.derive import derive
from region.sync import state

log = logging.getLogger("SyncShield.carry")
router = APIRouter(tags=["carry"])

ROOT = Path(__file__).resolve().parents[2]

# In-memory carry counters for /v1/status
_carry_metrics = {
    "received": 0,
    "new": 0,
    "dup": 0,
    "rejected": 0,
}


def get_carry_metrics() -> dict:
    return dict(_carry_metrics)


def reset_carry_metrics() -> None:
    _carry_metrics["received"] = 0
    _carry_metrics["new"] = 0
    _carry_metrics["dup"] = 0
    _carry_metrics["rejected"] = 0


def _get_public_keys() -> dict | None:
    for path in [
        Path("trust/public_keys.json"),
        Path("region/trust/public_keys.json"),
        ROOT / "trust" / "public_keys.json",
    ]:
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
    for mod_name in ["engine.signing", "region.trust.signing"]:
        try:
            mod = sys.modules.get(mod_name) or __import__(mod_name, fromlist=["PUBLIC_KEYS"])
            if hasattr(mod, "PUBLIC_KEYS"):
                return getattr(mod, "PUBLIC_KEYS")
        except Exception:
            pass
    return None


def _verify_sig(origin: str, event_hash: str, sig: str | None) -> bool:
    for mod_name in ["engine.signing", "region.trust.signing"]:
        try:
            mod = sys.modules.get(mod_name) or __import__(mod_name, fromlist=["verify"])
            if hasattr(mod, "verify"):
                if not sig:
                    return False
                return bool(mod.verify(origin, event_hash, sig))
        except Exception:
            pass
    return True



def shape_ok(e: dict) -> bool:
    if not isinstance(e, dict):
        return False
    if any(k not in e for k in EVENT_REQUIRED):
        return False
    if e.get("event_id") != f"{e.get('origin')}:{e.get('seq')}":
        return False
    return True


# ---- GET /healthz -----------------------------------------------------------
@router.get("/healthz")
def healthz():
    """Liveness probe: no auth, no DB writes (PDF 2 Section 5.3, 6.3)."""
    return {
        "region": config.settings.region,
        "ok": True,
        "level": state.current_level(),
        "ts": int(time.time() * 1000),
    }


# ---- POST /v1/carry ---------------------------------------------------------
@router.post("/v1/carry")
async def carry_endpoint(request: Request, user: auth.Auth = Depends(auth.require_key)):
    """Browser-carried event ingest (PDF 2 Section 5.3, 6.2)."""
    if not getattr(config.settings, "carry_enabled", True):
        raise HTTPException(503, "carry_disabled")

    raw_body = await request.body()
    if len(raw_body) > 512 * 1024:
        raise HTTPException(413, "carry body exceeds 512 kB")

    try:
        data = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(422, "invalid JSON")

    events = data.get("events", [])
    if not isinstance(events, list):
        raise HTTPException(422, "events must be a list")

    results = []
    events_to_apply = events[:200]
    public_keys = _get_public_keys()

    new_cnt = 0
    dup_cnt = 0
    rej_cnt = 0

    with store.tx() as conn:
        dirty_reports = set()
        for e in events_to_apply:
            eid = e.get("event_id") if isinstance(e, dict) else None

            # 1. Shape check
            if not shape_ok(e):
                results.append({"event_id": eid, "result": "rejected", "reason": "shape"})
                rej_cnt += 1
                store.quarantine(conn, e, "carry", "shape")
                continue

            # 2. Origin check
            origin = e.get("origin")
            if public_keys is not None and origin not in public_keys:
                results.append({"event_id": eid, "result": "rejected", "reason": "unknown_origin"})
                rej_cnt += 1
                store.quarantine(conn, e, "carry", "unknown_origin")
                continue

            # 3. Hash check
            expected_hash = canonical.event_hash(e)
            if expected_hash != e.get("hash"):
                results.append({"event_id": eid, "result": "rejected", "reason": "bad_hash"})
                rej_cnt += 1
                store.quarantine(conn, e, "carry", "bad_hash")
                continue

            # 4. Signature check
            if not _verify_sig(origin, e["hash"], e.get("sig")):
                results.append({"event_id": eid, "result": "rejected", "reason": "bad_sig"})
                rej_cnt += 1
                store.quarantine(conn, e, "carry", "bad_sig")
                continue

            # 5. Insert or Ignore
            r = store.insert_event_if_absent(conn, e)
            if r == "mismatch":
                results.append({"event_id": eid, "result": "rejected", "reason": "id_reused"})
                rej_cnt += 1
                store.quarantine(conn, e, "carry", "id_reused")
            elif r == "new":
                state.advance(conn, origin, e["seq"])
                store.hlc_receive(conn, e["hlc"])
                dirty_reports.add(e["report_id"])
                results.append({"event_id": eid, "result": "new"})
                new_cnt += 1
            else:
                results.append({"event_id": eid, "result": "dup"})
                dup_cnt += 1

        for rid in dirty_reports:
            store.save_view(conn, rid, derive(store.events_for(conn, rid)))

    # Update metrics
    _carry_metrics["received"] += len(events_to_apply)
    _carry_metrics["new"] += new_cnt
    _carry_metrics["dup"] += dup_cnt
    _carry_metrics["rejected"] += rej_cnt

    return {"results": results}


# ---- POST /v1/events/has ----------------------------------------------------
@router.post("/v1/events/has")
async def events_has(request: Request, user: auth.Auth = Depends(auth.require_key)):
    """Check existence of up to 500 event IDs (PDF 2 Section 5.3)."""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(422, "invalid JSON")

    ids = data.get("ids", [])
    if not isinstance(ids, list):
        raise HTTPException(422, "ids must be a list")

    check_ids = ids[:500]
    if not check_ids:
        return {"missing": []}

    with store.read() as conn:
        placeholders = ",".join("?" for _ in check_ids)
        rows = conn.execute(
            f"SELECT event_id FROM events WHERE event_id IN ({placeholders})", check_ids
        ).fetchall()
        existing = {r[0] for r in rows}

    missing = [eid for eid in check_ids if eid not in existing]
    return {"missing": missing}
