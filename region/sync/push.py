"""Best-effort push replication fast path (owner: Simrit).

The event log is the authoritative outbox; pull anti-entropy repairs any lost pushes.
Events are prioritized (P0 critical < P1 normal < P2 bulk) so high-urgency reports
overtake bulk note queues on slow/throttled links (PDF 2 Section 6.2, 6.9).
"""
import asyncio
import json
import logging
import time

import httpx

from region import config, store
from region.engine import canonical
from region.sync import links, state

log = logging.getLogger("SyncShield.push")

# Per-peer in-memory priority queues: queue[peer] -> PushQueue
_queues: dict[str, "PushQueue"] = {}


class PushQueue:
    def __init__(self):
        self._q = asyncio.PriorityQueue()
        self._queued_ids = set()
        self._counts = {0: 0, 1: 0, 2: 0}

    def put(self, priority: int, hlc: tuple | list, event_id: str) -> None:
        if event_id in self._queued_ids:
            return
        self._queued_ids.add(event_id)
        p = priority if priority in (0, 1, 2) else 1
        self._counts[p] = self._counts.get(p, 0) + 1
        hlc_tuple = tuple(hlc) if isinstance(hlc, list) else hlc
        self._q.put_nowait((priority, hlc_tuple, event_id))

    def clear(self) -> None:
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._queued_ids.clear()
        self._counts = {0: 0, 1: 0, 2: 0}

    async def take(self, max_n: int = 50, max_wait_ms: int = 100) -> list[str]:
        try:
            item = await asyncio.wait_for(self._q.get(), timeout=max_wait_ms / 1000.0)
        except (asyncio.TimeoutError, TimeoutError):
            return []

        prio = item[0] if item[0] in (0, 1, 2) else 1
        if self._counts.get(prio, 0) > 0:
            self._counts[prio] -= 1
        batch = [item[2]]
        self._queued_ids.discard(item[2])
        while len(batch) < max_n and not self._q.empty():
            try:
                nxt = self._q.get_nowait()
                p_nxt = nxt[0] if nxt[0] in (0, 1, 2) else 1
                if self._counts.get(p_nxt, 0) > 0:
                    self._counts[p_nxt] -= 1
                batch.append(nxt[2])
                self._queued_ids.discard(nxt[2])
            except asyncio.QueueEmpty:
                break
        return batch

    def counts(self) -> dict[str, int]:
        return {
            "P0": self._counts.get(0, 0),
            "P1": self._counts.get(1, 0),
            "P2": self._counts.get(2, 0),
        }


def get_queue(peer: str) -> PushQueue:
    if peer not in _queues:
        _queues[peer] = PushQueue()
    return _queues[peer]


def get_queue_counts() -> dict[str, int]:
    totals = {"P0": 0, "P1": 0, "P2": 0}
    for q in _queues.values():
        c = q.counts()
        for k in totals:
            totals[k] += c[k]
    return totals



def classify_priority(ev: dict) -> int:
    """Classify event priority: 0 (critical), 1 (normal), 2 (bulk) per PDF 2 6.9."""
    if "priority" in ev and ev["priority"] in (0, 1, 2) and ev["priority"] != 1:
        return ev["priority"]
    ev_type = ev.get("type")
    if ev_type == "NOTE":
        return 2
    if ev_type == "RESOLVE":
        return 0
    if ev_type == "CREATE":
        val = ev.get("value", {})
        if isinstance(val, dict):
            if val.get("severity", 0) >= 4 or val.get("category") in ("collapse", "medical", "fire"):
                return 0
        return 1
    if ev_type == "SET":
        field = ev.get("field")
        val = ev.get("value")
        if field == "severity" and isinstance(val, (int, float)) and val >= 4:
            return 0
        if field == "assigned_team" and val:
            return 0
        if field == "status" and val in ("resolved", "closed", "cancelled"):
            return 0
    return 1


def enqueue_for_peer(peer: str, ev: dict) -> None:
    q = get_queue(peer)
    prio = classify_priority(ev)
    hlc_val = ev.get("hlc", [0, 0])
    q.put(prio, hlc_val, ev["event_id"])


def enqueue(ev: dict) -> None:
    """Enqueue an event for all configured peers."""
    for p in config.settings.peers:
        enqueue_for_peer(p, ev)


def rebuild_unacked(peer: str) -> None:
    """Rebuild push queue from unacknowledged events in the log (PDF 2 6.7)."""
    with store.read() as conn:
        row = conn.execute("SELECT seq FROM peer_view WHERE peer=? AND origin=?", (peer, config.settings.region)).fetchone()
        last_seq = row[0] if row else 0
        rows = conn.execute(
            "SELECT body FROM events WHERE origin=? AND seq>? ORDER BY seq",
            (config.settings.region, last_seq),
        ).fetchall()
        for r in rows:
            ev = json.loads(r[0])
            enqueue_for_peer(peer, ev)


def get_events_by_id(event_ids: list[str]) -> list[dict]:
    if not event_ids:
        return []
    with store.read() as conn:
        placeholders = ",".join("?" for _ in event_ids)
        rows = conn.execute(
            f"SELECT event_id, body FROM events WHERE event_id IN ({placeholders})", event_ids
        ).fetchall()
        id_map = {r[0]: json.loads(r[1]) for r in rows}
        return [id_map[eid] for eid in event_ids if eid in id_map]


async def push_worker(peer: str, base_url: str) -> None:
    q = get_queue(peer)
    headers = {"X-Region": config.settings.region, "X-Peer-Token": config.settings.peer_token}
    async with httpx.AsyncClient(timeout=config.settings.http_timeout_s) as client:
        while True:
            try:
                batch_ids = await q.take(max_n=config.settings.page_size, max_wait_ms=100)
                if not batch_ids:
                    if not links.is_down(peer):
                        rebuild_unacked(peer)
                    await asyncio.sleep(0.05)
                    continue

                if links.is_down(peer):
                    q.clear()
                    await asyncio.sleep(0.1)
                    continue

                evs = get_events_by_id(batch_ids)
                if not evs:
                    continue

                total_bytes = sum(len(canonical.dumps(e).encode("utf-8")) for e in evs)
                await links.bucket.throttle(peer, total_bytes)

                if links.is_down(peer):
                    continue

                r = await client.post(
                    f"{base_url}/internal/v1/sync/push",
                    json={"from": config.settings.region, "events": evs},
                    headers=headers,
                )
                if r.status_code == 200:
                    resp = r.json()
                    state.save_peer_view(peer, resp.get("heads", {}))
                    state.mark_ok(peer)
                elif r.status_code == 503:
                    state.mark_error(peer, "link_down")
            except asyncio.CancelledError:
                break
            except Exception as e:
                state.mark_error(peer, type(e).__name__)
                await asyncio.sleep(0.1)
