"""Per-actor write rate limit (owner: Akshit). Contract v1.2, sections 8.1 and 8.3.

A token bucket per actor: a full bucket holds `burst` tokens and gains `refill_per_s` tokens a second. Every write
that reaches the write path costs one token; with an empty bucket the write is refused with 429 and nothing is stored.
Off while burst is 0 (the default), so a region that sets nothing behaves exactly as before.

    RATE_ACTOR_BURST          tokens in a full bucket (0 = off)
    RATE_ACTOR_REFILL_PER_S   tokens added per second (default 1; 0 = no refill: one burst and then nothing)

Where the limit comes from, first match wins: configure() (tests and in-process callers), then
`config.settings.rate_actor_burst` / `.rate_actor_refill_per_s` when the settings object has them (so the runtime
`POST /admin/config` only has to update the settings), then the environment variables, then the defaults.

What is NOT limited: reads, peer replication, carry, and a replay of an op_id that is already stored (the write path
answers a replay before it asks the limiter, so a retry is never refused and never counted).

Honest limits: buckets live in this region's memory. A client that moves to another region starts with a full
allowance there (this bounds the load on each region, it is not a global quota), and a restart empties them.
"""
import math
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

from region import config

DEFAULT_REFILL_PER_S = 1.0
NEVER_MS = 3_600_000        # retry_after_ms when the bucket does not refill: effectively never, one hour
MAX_BUCKETS = 5000          # above this, buckets that have refilled completely are forgotten (they equal "no bucket")
_EPS = 1e-9                 # float slack: a client that waits exactly retry_after_ms must find its token there

_clock = time.monotonic     # replaced by tests
_lock = threading.Lock()
_buckets: dict = {}         # subject -> [tokens, updated_at]
_override: dict = {}        # configure(): {"burst": int, "refill_per_s": float}
_refused = 0


@dataclass(frozen=True)
class Decision:
    allowed: bool
    limit: int              # tokens in a full bucket
    remaining: int          # whole tokens left after this write
    retry_after_ms: int     # 0 when allowed; otherwise how long until one token is back


class RateLimited(Exception):
    """Raised by the write path; region/main.py turns it into the 429 of contract 8.3."""

    def __init__(self, decision: Decision):
        super().__init__("rate_limited")
        self.decision = decision


def key(auth) -> str:
    """Whose allowance a request uses: the actor of the request (the API key's actor, or the signed-in member's actor id)."""
    return auth.actor


# ---- settings ---------------------------------------------------------------------------------------------
def _number(raw, cast, fallback):
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return fallback


def limits() -> tuple:
    """(burst, refill_per_s) in force right now. burst 0 means the limiter is off."""
    burst = _override.get("burst")
    refill = _override.get("refill_per_s")
    if burst is None:
        burst = getattr(config.settings, "rate_actor_burst", None)
    if refill is None:
        refill = getattr(config.settings, "rate_actor_refill_per_s", None)
    if burst is None:
        burst = os.environ.get("RATE_ACTOR_BURST", 0)
    if refill is None:
        refill = os.environ.get("RATE_ACTOR_REFILL_PER_S", DEFAULT_REFILL_PER_S)
    burst = max(0, _number(burst, int, 0))
    refill = _number(refill, float, DEFAULT_REFILL_PER_S)
    if not (refill >= 0) or math.isinf(refill):   # negative, NaN or infinite: unusable, fall back; 0 is a real setting
        refill = DEFAULT_REFILL_PER_S
    return burst, refill


def configure(burst: Optional[int] = None, refill_per_s: Optional[float] = None) -> None:
    """Set the limit from inside the process. A value left as None is not changed."""
    with _lock:
        if burst is not None:
            _override["burst"] = burst
        if refill_per_s is not None:
            _override["refill_per_s"] = refill_per_s


def reset() -> None:
    """Forget every bucket, counter and configure() value (tests; also what a restart does)."""
    global _refused
    with _lock:
        _buckets.clear()
        _override.clear()
        _refused = 0


# ---- the bucket -------------------------------------------------------------------------------------------
def _level(subject: str, burst: int, refill: float, now: float) -> float:
    """Tokens in the subject's bucket at `now` (a subject never seen has a full bucket). Caller holds the lock."""
    state = _buckets.get(subject)
    if state is None:
        return float(burst)
    tokens, updated = state
    return min(float(burst), tokens + max(0.0, now - updated) * refill)


def _forget_full(burst: int, refill: float, now: float) -> None:
    for subject in [s for s in _buckets if _level(s, burst, refill, now) >= burst]:
        del _buckets[subject]


def take(subject: str) -> Optional[Decision]:
    """Spend one token of `subject`. None when the limiter is off; otherwise the decision (allowed or refused)."""
    global _refused
    burst, refill = limits()
    if burst <= 0:
        if _buckets:
            with _lock:
                _buckets.clear()        # switched off at runtime: nobody keeps an old, emptier bucket for later
        return None
    now = _clock()
    with _lock:
        tokens = _level(subject, burst, refill, now)
        if tokens >= 1.0 - _EPS:
            tokens = max(0.0, tokens - 1.0)
            _buckets[subject] = [tokens, now]
            if len(_buckets) > MAX_BUCKETS:
                _forget_full(burst, refill, now)
            return Decision(True, burst, int(tokens + _EPS), 0)
        _buckets[subject] = [tokens, now]
        _refused += 1
        wait_ms = max(1, math.ceil((1.0 - tokens) / refill * 1000)) if refill > 0 else NEVER_MS
        return Decision(False, burst, 0, wait_ms)


def peek(subject: str) -> Optional[Decision]:
    """The subject's allowance without spending anything (for the headers of a replayed write)."""
    burst, refill = limits()
    if burst <= 0:
        return None
    with _lock:
        return Decision(True, burst, int(_level(subject, burst, refill, _clock()) + _EPS), 0)


# ---- what the HTTP layer and the status builder need -----------------------------------------------------
def headers(decision: Optional[Decision]) -> dict:
    if decision is None:
        return {}
    out = {"X-RateLimit-Limit": str(decision.limit), "X-RateLimit-Remaining": str(decision.remaining)}
    if not decision.allowed:
        out["Retry-After"] = str(max(1, math.ceil(decision.retry_after_ms / 1000)))
    return out


def refusal_body(decision: Decision) -> dict:
    return {"error": "rate_limited", "scope": "actor", "retry_after_ms": decision.retry_after_ms}


def snapshot() -> dict:
    """The actor part of the status `rate` block (contract 8.4)."""
    burst, refill = limits()
    return {"actor_burst": burst, "actor_refill_per_s": refill, "refused_actor": _refused}
