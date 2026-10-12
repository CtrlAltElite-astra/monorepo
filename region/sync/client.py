"""Pull loop (owner: Simrit). Every SYNC_INTERVAL_MS, for every peer:
summary -> for each origin fetch pages after our contiguous watermark -> ingest.

A write never waits for this loop; a dead peer only changes peer_state.
Push workers run concurrently alongside pull for low-latency delivery.
"""
import asyncio
import logging

import httpx

from region import config, store
from region.sync import ingest, links, push, state

log = logging.getLogger("SyncShield.sync")


def _headers() -> dict:
    return {"X-Region": config.settings.region, "X-Peer-Token": config.settings.peer_token}


async def pull_once(client: httpx.AsyncClient, peer: str, base_url: str) -> None:
    if links.is_down(peer):
        state.mark_error(peer, "link_down")
        return
    try:
        # Check if peer was in an error / isolated state before this pull
        with store.read() as conn:
            row = conn.execute("SELECT fails, last_error FROM peer_state WHERE peer=?", (peer,)).fetchone()
            was_isolated_or_failed = (row and (row[0] > 0 or row[1] is not None))

        r = await client.get(f"{base_url}/internal/v1/sync/summary", headers=_headers())
        r.raise_for_status()
        summary = r.json()
        state.save_peer_view(peer, summary["heads"])

        for origin, head in summary["heads"].items():
            if origin == config.settings.region:
                continue  # never import our own history from a peer (seq reuse guard)
            after = state.contiguous(origin)
            while after < head:
                if links.is_down(peer):
                    state.mark_error(peer, "link_down")
                    return
                r = await client.get(
                    f"{base_url}/internal/v1/sync/events",
                    params={"origin": origin, "after": after, "limit": config.settings.page_size},
                    headers=_headers(),
                )
                r.raise_for_status()
                # Apply throttling if link is slow
                await links.bucket.throttle(peer, len(r.content))

                events = r.json()["events"]
                if not events:
                    break
                ingest.ingest(events, source=peer)
                new_after = state.contiguous(origin)
                if new_after == after:  # gap: retry next round
                    break
                after = new_after

        state.mark_ok(peer)

        # Fast heal: on recovering from partition/error, rebuild unacked push events immediately
        if was_isolated_or_failed:
            push.rebuild_unacked(peer)

    except httpx.HTTPStatusError as e:
        state.mark_error(peer, f"http_{e.response.status_code}")
    except Exception as e:  # timeout, refused, bad json ...
        state.mark_error(peer, type(e).__name__)


async def run_forever() -> None:
    """Pull from every peer each round. The peer list is read every round, so a peer added at runtime
    (POST /admin/config, used when a region joins) is synced from its next round on, with its own push worker."""
    interval = config.settings.sync_interval_ms / 1000
    push_tasks = {}   # peer -> push worker task

    def ensure_push_workers(peers: dict) -> None:
        if not config.settings.push_enabled:
            return
        for p, url in peers.items():
            task = push_tasks.get(p)
            if task is None or task.done():
                push_tasks[p] = asyncio.create_task(push.push_worker(p, url))

    try:
        async with httpx.AsyncClient(timeout=config.settings.http_timeout_s) as client:
            while True:
                peers = dict(config.settings.peers)
                ensure_push_workers(peers)
                try:
                    if peers:
                        await asyncio.gather(*(pull_once(client, p, url) for p, url in peers.items()))
                except Exception:
                    log.exception("pull round failed")
                await asyncio.sleep(interval)
    finally:
        for t in push_tasks.values():
            t.cancel()
