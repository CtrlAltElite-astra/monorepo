"""A reference per-actor token bucket (owner: Rahil). Scaling plan 3.1 and 3.3.

Written from the contract alone, so the fairness properties do not depend on lane A's
region/api/ratelimit.py. In the joining phase the same properties are pointed at the real
limiter by changing make_limiter() in test_limiter_properties.py.

    burst                each actor starts with `burst` tokens and never holds more; 0 means off
    refill_per_s         tokens come back continuously at this rate
    a write              costs one token; with less than one token it is refused (429) with retry_after_ms
    an op_id replay      is answered before the limiter: it costs nothing and is never counted
    a read               is never limited

Arithmetic is exact (Fraction), so no rounding can hide or invent a token.
"""
import math
from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class Decision:
    accepted: bool
    remaining: int        # whole tokens left after this request (X-RateLimit-Remaining)
    retry_after_ms: int   # 0 when accepted


class ReferenceBucket:
    def __init__(self, burst: int, refill_per_s: int):
        self.burst, self.refill = burst, Fraction(refill_per_s)
        self.tokens = {}    # actor -> Fraction
        self.updated = {}   # actor -> last time seen (ms)

    def allow(self, actor: str, now_ms: int) -> Decision:
        if self.burst == 0:
            return Decision(True, 0, 0)
        tokens = self.tokens.get(actor, Fraction(self.burst))
        if actor in self.updated:
            tokens = min(Fraction(self.burst), tokens + self.refill * (now_ms - self.updated[actor]) / 1000)
        self.updated[actor] = now_ms
        if tokens >= 1:
            self.tokens[actor] = tokens - 1
            return Decision(True, math.floor(tokens - 1), 0)
        self.tokens[actor] = tokens
        wait = math.ceil((1 - tokens) * 1000 / self.refill) if self.refill else 0
        return Decision(False, 0, wait)


class WriteGate:
    """The order the write path uses: an op_id already stored is answered first, then the limiter."""

    def __init__(self, limiter):
        self.limiter, self.stored = limiter, set()

    def write(self, actor: str, op_id: str, now_ms: int):
        """('replay', None) for an op_id already stored, else ('accepted' | 'refused', Decision)."""
        if op_id in self.stored:
            return "replay", None
        decision = self.limiter.allow(actor, now_ms)
        if decision.accepted:
            self.stored.add(op_id)
        return ("accepted" if decision.accepted else "refused"), decision
