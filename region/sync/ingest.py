"""Idempotent ingest of events from a peer (owner: Simrit).

Used by pull now and by push in PDF 2. Applying a page twice inserts nothing.
Received events are checked for shape, hash and identity only, never against
the receiver's business rules.
"""
from contracts.schemas import EVENT_REQUIRED
from region import store
from region.engine import canonical, signing
from region.engine.derive import derive
from region.sync import state


def ingest(events: list, source: str) -> dict:
    stats = {"new": 0, "dup": 0, "rejected": 0, "rejected_list": []}
    with store.tx() as conn:
        dirty = set()
        for ev in events:
            if not isinstance(ev, dict) or any(k not in ev for k in EVENT_REQUIRED) \
                    or ev.get("event_id") != f"{ev.get('origin')}:{ev.get('seq')}":
                store.quarantine(conn, ev, source, "bad_shape")
                stats["rejected"] += 1
                eid = ev.get("event_id") if isinstance(ev, dict) else None
                stats["rejected_list"].append({"event_id": eid, "reason": "bad_shape"})
                continue
            if canonical.event_hash(ev) != ev["hash"]:
                store.quarantine(conn, ev, source, "bad_hash")
                stats["rejected"] += 1
                stats["rejected_list"].append({"event_id": ev["event_id"], "reason": "bad_hash"})
                continue
            # Fail closed (plan 6.2): once any key is registered (verification is on), a received event
            # must carry a valid signature by its origin. With no registry at all (unprovisioned dev/test
            # nodes) there is nothing to verify against, so only shape and hash are checked.
            if signing.public_keys() and not signing.verify(ev["origin"], ev["hash"], ev.get("sig")):
                store.quarantine(conn, ev, source, "bad_sig")
                stats["rejected"] += 1
                stats["rejected_list"].append({"event_id": ev["event_id"], "reason": "bad_sig"})
                continue
            r = store.insert_event_if_absent(conn, ev)
            if r == "mismatch":
                store.quarantine(conn, ev, source, "id_reused")
                stats["rejected"] += 1
                stats["rejected_list"].append({"event_id": ev["event_id"], "reason": "id_reused"})
            elif r == "new":
                state.advance(conn, ev["origin"], ev["seq"])
                store.hlc_receive(conn, ev["hlc"])
                dirty.add(ev["report_id"])
                stats["new"] += 1
            else:
                stats["dup"] += 1
        for rid in dirty:
            store.save_view(conn, rid, derive(store.events_for(conn, rid)))
    stats["stored"] = stats["new"]
    stats["duplicate"] = stats["dup"]
    return stats
