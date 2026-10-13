"""Fairness of the per-actor rate limit, as properties (owner: Rahil). Scaling plan, lane B task 2.

Hypothesis generates random streams of writes from five shoppers, with replays of earlier op_ids
and gaps of any length, and checks that for every stream:

    1. no actor is ever accepted more than burst + refill x elapsed time, over any window;
    2. one actor's spam never changes what another actor is allowed;
    3. a replay of a stored op_id costs nothing: it never changes any other decision;
    4. burst 0 means off: every write is accepted;
    5. a refused actor that waits retry_after_ms is accepted again, and the headers stay in range.

They run against the REAL limiter, region/api/ratelimit.py, through a small adapter with an injected clock
(joining step J3). tests/scale/reference_bucket.py, the bucket written from contract 3.1 and 3.3 alone, still
supplies the WriteGate (the order the write path uses: a replay is answered before the limiter) and stays as the
specification the real limiter is held to.
"""
from fractions import Fraction

import pytest
from hypothesis import given, settings, strategies as st

from reference_bucket import Decision, WriteGate
from region.api import ratelimit

SHOPPERS = ("Alice", "Bob", "Chitra", "Dev", "Esha")
EXAMPLES = 300


class RealLimiter:
    """region/api/ratelimit.py behind allow(actor, now_ms) -> Decision. The module is global state, so only one of
    these is live at a time: building a new one starts from empty buckets (the properties always do that)."""

    def __init__(self, burst: int, refill_per_s: int):
        ratelimit.reset()
        ratelimit.configure(burst=burst, refill_per_s=float(refill_per_s))
        self.now_ms = 0
        ratelimit._clock = lambda: self.now_ms / 1000

    def allow(self, actor: str, now_ms: int) -> Decision:
        self.now_ms = now_ms
        d = ratelimit.take(actor)            # None = limiter off (burst 0)
        if d is None:
            return Decision(True, 0, 0)
        return Decision(d.allowed, d.remaining, 0 if d.allowed else d.retry_after_ms)


@pytest.fixture(scope="module", autouse=True)
def _restore_the_real_clock():
    clock = ratelimit._clock
    yield
    ratelimit.reset()
    ratelimit._clock = clock


def make_limiter(burst: int, refill_per_s: int):
    """The limiter under test: anything with allow(actor, now_ms) -> Decision."""
    return RealLimiter(burst, refill_per_s)


# a stream: (gap in ms since the previous request, actor, replay of an earlier op_id?)
STEP = st.tuples(st.integers(0, 1500), st.sampled_from(SHOPPERS), st.booleans())
STREAM = st.lists(STEP, min_size=1, max_size=80)
BURST = st.integers(1, 12)
REFILL = st.integers(1, 6)


def run(stream, burst, refill):
    """Feed a stream through the write gate. Returns [(time, actor, op_id, outcome, decision)]."""
    gate, now, out, ops = WriteGate(make_limiter(burst, refill)), 0, [], []
    for i, (gap, actor, replay) in enumerate(stream):
        now += gap
        mine = [op for op, who in ops if who == actor]
        op_id = mine[-1] if replay and mine else f"{actor}-{i}"
        outcome, decision = gate.write(actor, op_id, now)
        if outcome == "accepted":
            ops.append((op_id, actor))
        out.append((now, actor, op_id, outcome, decision))
    return out


def decisions_for(trace, actor):
    return [(t, op, outcome) for t, a, op, outcome, _ in trace if a == actor and outcome != "replay"]


@settings(max_examples=EXAMPLES, deadline=None, derandomize=True)
@given(STREAM, BURST, REFILL)
def test_no_actor_exceeds_burst_plus_refill_over_any_window(stream, burst, refill):
    trace = run(stream, burst, refill)
    for actor in SHOPPERS:
        times = [t for t, a, _, outcome, _ in trace if a == actor and outcome == "accepted"]
        for i in range(len(times)):
            for j in range(i, len(times)):
                allowed = burst + Fraction(refill) * (times[j] - times[i]) / 1000
                assert j - i + 1 <= allowed, (actor, times[i], times[j], j - i + 1, allowed)


@settings(max_examples=EXAMPLES, deadline=None, derandomize=True)
@given(STREAM, BURST, REFILL, st.sampled_from(SHOPPERS))
def test_one_actors_spam_never_changes_another_actors_allowance(stream, burst, refill, spammer):
    """Add a burst of spam from one shopper between every step: every other shopper's decisions stay the same."""
    spammed = []
    for gap, actor, replay in stream:
        spammed.append((gap, actor, replay))
        spammed += [(0, spammer, False)] * 5
    quiet, noisy = run(stream, burst, refill), run(spammed, burst, refill)
    for actor in SHOPPERS:
        if actor != spammer:
            assert [o for _, _, o in decisions_for(quiet, actor)] == [o for _, _, o in decisions_for(noisy, actor)]


@settings(max_examples=EXAMPLES, deadline=None, derandomize=True)
@given(STREAM, BURST, REFILL)
def test_a_replay_costs_nothing(stream, burst, refill):
    """The same stream with every replay removed gives the same decision for every fresh write."""
    with_replays = run(stream, burst, refill)
    assert all(d is None for _, _, _, outcome, d in with_replays if outcome == "replay")  # the limiter was not asked
    kept, gap_carry = [], 0
    for (gap, actor, replay), (_, _, _, outcome, _) in zip(stream, with_replays):
        if outcome == "replay":
            gap_carry += gap  # keep the clock where it was
            continue
        kept.append((gap + gap_carry, actor, False))
        gap_carry = 0
    without = run(kept, burst, refill)
    fresh = [(t, a, outcome) for t, a, _, outcome, _ in with_replays if outcome != "replay"]
    assert fresh == [(t, a, outcome) for t, a, _, outcome, _ in without]


@settings(max_examples=100, deadline=None, derandomize=True)
@given(STREAM, REFILL)
def test_burst_zero_means_off(stream, refill):
    assert all(outcome != "refused" for *_, outcome, _ in run(stream, 0, refill))


@settings(max_examples=EXAMPLES, deadline=None, derandomize=True)
@given(STREAM, BURST, REFILL)
def test_retry_after_is_honest_and_headers_stay_in_range(stream, burst, refill):
    trace = run(stream, burst, refill)
    for k, (now, actor, _, outcome, d) in enumerate(trace):
        if d is None:
            continue
        assert 0 <= d.remaining < burst
        if outcome == "refused":
            assert d.retry_after_ms > 0
            again = make_limiter(burst, refill)
            # this actor's requests up to and including this one, into a fresh limiter, then wait as advised
            for t, a, _, _, dd in trace[:k + 1]:
                if a == actor and dd is not None:
                    again.allow(actor, t)
            assert again.allow(actor, now + d.retry_after_ms).accepted
        else:
            assert d.retry_after_ms == 0


def test_spam_as_bob_scenario_from_the_plan():
    """R1: Bob spams 30 writes in one second; he is refused after his burst while Alice is never refused."""
    gate = WriteGate(make_limiter(10, 1))
    bob = [gate.write("Bob", f"bob-{i}", 100 + i * 30)[0] for i in range(30)]
    alice = [gate.write("Alice", f"alice-{i}", 120 + i * 300)[0] for i in range(3)]
    assert bob[:10] == ["accepted"] * 10 and set(bob[10:]) == {"refused"}
    assert alice == ["accepted"] * 3
    assert gate.write("Bob", "bob-0", 2000)[0] == "replay"  # his retry of a stored write is free
    assert gate.write("Bob", "bob-late", 100 + 29 * 30 + 1000)[0] == "accepted"  # one token back after a second
