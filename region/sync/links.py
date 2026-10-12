"""Link switch & traffic shaping API (owner: Simrit).

One 'down' blocks BOTH directions on this region: outbound pulls and pushes are skipped
and /internal answers 503 to that peer.
'slow' applies delay_ms latency and rate_bps token-bucket bandwidth throttling.
"""
import asyncio
import time

from region.sync import state

MODES = ("up", "down", "slow")


def mode(peer: str) -> str:
    return state.link_mode(peer)


def is_down(peer: str) -> bool:
    return state.link_mode(peer) == "down"


def is_slow(peer: str) -> bool:
    return state.link_mode(peer) == "slow"


def get_link_info(peer: str) -> dict:
    return state.get_link(peer)


def set_mode(peer: str, new_mode: str, delay_ms: int = 0, rate_bps: int = 0, drop_pct: int = 0) -> None:
    if new_mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    state.set_link(peer, new_mode, delay_ms=delay_ms, rate_bps=rate_bps, drop_pct=drop_pct)


def all_modes() -> dict:
    return state.all_links()


def all_details() -> dict:
    return state.all_links_detail()


class TokenBucket:
    def __init__(self):
        self._tokens: dict[str, float] = {}
        self._last_time: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def throttle(self, peer: str, num_bytes: int) -> None:
        """Applies delay_ms and limits byte rate according to rate_bps when link is 'slow'."""
        info = state.get_link(peer)
        if info.get("mode") != "slow":
            return
        delay_ms = info.get("delay_ms", 0)
        rate_bps = info.get("rate_bps", 0)

        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000.0)

        if rate_bps <= 0 or num_bytes <= 0:
            return

        async with self._lock:
            now = time.monotonic()
            last = self._last_time.get(peer, now)
            elapsed = now - last
            current_tokens = self._tokens.get(peer, float(rate_bps))
            # Replenish tokens up to max capacity
            current_tokens = min(float(max(rate_bps, num_bytes)), current_tokens + elapsed * rate_bps)
            if current_tokens < num_bytes:
                deficit = num_bytes - current_tokens
                wait_s = deficit / rate_bps
                self._tokens[peer] = 0.0
                self._last_time[peer] = now + wait_s
            else:
                self._tokens[peer] = current_tokens - num_bytes
                self._last_time[peer] = now
                wait_s = 0.0

        if wait_s > 0:
            await asyncio.sleep(wait_s)


bucket = TokenBucket()
