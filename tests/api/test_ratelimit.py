"""Per-actor write rate limit (owner: Akshit). Contract v1.2, sections 8.1 and 8.3; master plan lane A, task 2.

The bucket is tested through the HTTP API (what a shopper sees) with a clock the test controls, so nothing here
sleeps and nothing depends on how fast the machine is.
"""
import dataclasses
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from region import config
from region.api import ratelimit, writes
from region.auth import Auth
from region.sync import ingest

from conftest import KEY, SUP, create_value

ALICE = {"X-API-Key": "dev-alice"}
BOB = {"X-API-Key": "dev-bob"}
CART = "R-3f9a2c10"
ORIGIN_OK = {"Origin": "http://localhost:5173"}


@pytest.fixture
def shop(client, monkeypatch):
    """The region with two shoppers who each have their own API key (and so their own actor)."""
    keys = {**config.settings.api_keys, "dev-alice": ("alice", "officer"), "dev-bob": ("bob", "officer")}
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, api_keys=keys))
    return client


@pytest.fixture
def clock(monkeypatch):
    """A clock that only moves when the test says so: clock.advance(seconds)."""
    class Clock:
        now = 1000.0

        def advance(self, seconds):
            self.now += seconds

    c = Clock()
    monkeypatch.setattr(ratelimit, "_clock", lambda: c.now)
    return c


def body(**over):
    return {"title": "Cart", "category": "cart", "severity": 1, **over}


def add(client, who, **over):
    return client.post("/v1/reports", json=body(**over), headers=who)


def left(r):
    return int(r.headers["x-ratelimit-remaining"])


def stored(db_path):
    con = sqlite3.connect(db_path)
    try:
        return [json.loads(r[0]) for r in con.execute("SELECT body FROM events ORDER BY seq")]
    finally:
        con.close()


class Settings:
    """config.settings with the two rate attributes set (what lane D's settings will carry) or removed (v1.1)."""

    def __init__(self, base, **rate):
        self._base, self._rate = base, rate

    def __getattr__(self, name):
        if name.startswith("rate_actor_"):
            if name in self._rate:
                return self._rate[name]
            raise AttributeError(name)
        return getattr(self._base, name)


# ---- off by default: limit 0 means no behaviour change ------------------------------------------------
def test_the_limiter_is_off_by_default_and_nothing_changes(shop, region):
    answers = [add(shop, BOB) for _ in range(30)]
    assert [r.status_code for r in answers] == [201] * 30
    assert all("x-ratelimit-limit" not in r.headers and "x-ratelimit-remaining" not in r.headers for r in answers)
    assert set(answers[0].json()) == {"report", "event", "event_id", "consistency"}       # the v1.1 body, unchanged
    assert [e["seq"] for e in stored(region)] == list(range(1, 31))
    assert ratelimit.take("bob") is None and ratelimit.peek("bob") is None and ratelimit.headers(None) == {}
    assert ratelimit.snapshot() == {"actor_burst": 0, "actor_refill_per_s": 1.0, "refused_actor": 0}


def test_a_burst_of_zero_switches_the_limiter_off_whatever_the_refill(shop):
    ratelimit.configure(burst=0, refill_per_s=5)
    assert [add(shop, BOB).status_code for _ in range(12)] == [201] * 12


# ---- R1: one shopper's spam never touches another shopper ---------------------------------------------
def test_bob_is_refused_after_his_burst_while_alice_is_accepted(shop, region, clock):
    ratelimit.configure(burst=3, refill_per_s=1)
    spam = [add(shop, BOB) for _ in range(3)]
    assert [r.status_code for r in spam] == [201, 201, 201]
    assert [r.headers["x-ratelimit-limit"] for r in spam] == ["3", "3", "3"] and [left(r) for r in spam] == [2, 1, 0]

    refused = add(shop, BOB)
    assert refused.status_code == 429
    assert refused.json() == {"error": "rate_limited", "scope": "actor", "retry_after_ms": 1000}   # contract 8.3
    assert refused.headers["retry-after"] == "1"
    assert refused.headers["x-ratelimit-limit"] == "3" and refused.headers["x-ratelimit-remaining"] == "0"

    alice = add(shop, ALICE)                                  # Alice shops normally: her allowance is her own
    assert alice.status_code == 201 and left(alice) == 2
    assert alice.json()["event_id"] == "mum:4"                # Bob's refused write used no sequence number
    assert [e["actor"] for e in stored(region)] == ["bob", "bob", "bob", "alice"]


def test_spamming_while_refused_changes_nothing_for_anyone(shop, region, clock):
    ratelimit.configure(burst=2, refill_per_s=1)
    add(shop, BOB), add(shop, BOB)
    assert [add(shop, BOB).status_code for _ in range(25)] == [429] * 25     # refusals are free: no debt builds up
    assert len(stored(region)) == 2
    clock.advance(1)
    assert add(shop, BOB).status_code == 201                                  # one second later one token is back
    assert left(add(shop, ALICE)) == 1 and ratelimit.snapshot()["refused_actor"] == 25


# ---- the bucket refills ----------------------------------------------------------------------------------
def test_the_bucket_refills_over_time_and_never_beyond_the_burst(shop, clock):
    ratelimit.configure(burst=2, refill_per_s=2)
    add(shop, BOB), add(shop, BOB)
    assert add(shop, BOB).json()["retry_after_ms"] == 500     # 2 tokens a second: half a second for the next one
    clock.advance(0.25)
    waiting = add(shop, BOB)
    assert waiting.status_code == 429 and waiting.json()["retry_after_ms"] == 250
    clock.advance(0.25)
    assert add(shop, BOB).status_code == 201                  # waited exactly as long as it was told to
    assert add(shop, BOB).status_code == 429
    clock.advance(3600)                                       # a long break fills the bucket, it never overfills it
    assert [left(add(shop, BOB)) for _ in range(2)] == [1, 0]
    assert add(shop, BOB).status_code == 429


def test_a_refill_of_zero_is_one_burst_and_never_again(shop, clock):
    ratelimit.configure(burst=2, refill_per_s=0)                # a legal setting: lane D's /admin/config accepts 0
    assert [add(shop, BOB).status_code for _ in range(2)] == [201, 201]
    refused = add(shop, BOB)
    assert refused.status_code == 429
    assert refused.json() == {"error": "rate_limited", "scope": "actor", "retry_after_ms": ratelimit.NEVER_MS}
    assert refused.headers["retry-after"] == "3600"
    clock.advance(86_400)                                        # a day later nothing has come back
    assert add(shop, BOB).status_code == 429
    assert add(shop, ALICE).status_code == 201                  # and Alice still has her own burst
    assert ratelimit.snapshot()["actor_refill_per_s"] == 0.0


def test_retry_after_is_whole_seconds_and_at_least_one(shop, clock):
    ratelimit.configure(burst=1, refill_per_s=0.4)
    add(shop, BOB)
    slow = add(shop, BOB)
    assert slow.json()["retry_after_ms"] == 2500 and slow.headers["retry-after"] == "3"
    ratelimit.configure(refill_per_s=50)
    fast = add(shop, BOB)
    assert fast.json()["retry_after_ms"] == 20 and fast.headers["retry-after"] == "1"


# ---- a replay is answered before the limiter ------------------------------------------------------------
def test_a_replay_is_never_refused_and_never_counted(shop, region, clock):
    ratelimit.configure(burst=2, refill_per_s=1)
    first = add(shop, BOB, op_id="op-1")
    assert first.status_code == 201 and left(first) == 1
    replays = [add(shop, BOB, op_id="op-1") for _ in range(10)]
    assert all(r.status_code == 200 and r.headers["idempotent-replay"] == "true" for r in replays)
    assert [left(r) for r in replays] == [1] * 10             # ten retries cost nothing
    assert left(add(shop, BOB)) == 0                          # the second token was still there
    assert add(shop, BOB).status_code == 429                  # now Bob is out of tokens ...
    again = add(shop, BOB, op_id="op-1")                      # ... and his retry is still answered
    assert again.status_code == 200 and again.json()["event_id"] == first.json()["event_id"] and left(again) == 0
    assert len(stored(region)) == 2 and ratelimit.snapshot()["refused_actor"] == 1


def test_a_replay_through_the_idempotency_key_header_is_not_counted_either(shop, clock):
    ratelimit.configure(burst=1, refill_per_s=1)
    first = shop.post("/v1/reports", json=body(), headers={**BOB, "Idempotency-Key": "key-1"})
    again = shop.post("/v1/reports", json=body(), headers={**BOB, "Idempotency-Key": "key-1"})
    assert (first.status_code, again.status_code) == (201, 200) and again.headers["idempotent-replay"] == "true"
    assert add(shop, BOB).status_code == 429                  # the one token went to the first write only


def test_reusing_an_op_id_for_a_different_request_is_refused_before_the_limiter(shop, clock):
    ratelimit.configure(burst=2, refill_per_s=1)
    add(shop, BOB, op_id="op-1")
    reuse = add(shop, BOB, op_id="op-1", title="Something else")
    assert reuse.status_code == 409 and reuse.json() == {"error": "op_id_reuse"}
    assert left(add(shop, BOB)) == 0                          # the 409 did not cost a token


# ---- what is limited and what is not -------------------------------------------------------------------
def test_reads_are_never_limited(shop, clock):
    ratelimit.configure(burst=1, refill_per_s=1)
    rid = add(shop, BOB).json()["report"]["report_id"]
    assert add(shop, BOB).status_code == 429                  # Bob cannot write ...
    for _ in range(40):                                       # ... and reads as much as he likes
        for path in ("/v1/reports", f"/v1/reports/{rid}", f"/v1/reports/{rid}/history", "/v1/review", "/v1/status",
                     "/v1/health", "/healthz"):
            r = shop.get(path, headers=BOB)
            assert r.status_code == 200 and "x-ratelimit-remaining" not in r.headers
    assert ratelimit.snapshot()["refused_actor"] == 1


def test_every_kind_of_write_costs_one_token(shop, region, clock):
    ratelimit.configure(burst=3, refill_per_s=1)
    rid = add(shop, BOB).json()["report"]["report_id"]
    patch = shop.patch(f"/v1/reports/{rid}", json={"field": "severity", "value": 2}, headers=BOB)
    note = shop.post(f"/v1/reports/{rid}/notes", json={"text": "x"}, headers=BOB)
    assert (patch.status_code, left(patch), note.status_code, left(note)) == (200, 1, 201, 0)
    refused = [shop.patch(f"/v1/reports/{rid}", json={"field": "severity", "value": 3}, headers=BOB),
               shop.post(f"/v1/reports/{rid}/notes", json={"text": "y"}, headers=BOB), add(shop, BOB)]
    assert [r.status_code for r in refused] == [429, 429, 429]
    assert all(r.json()["error"] == "rate_limited" and r.json()["scope"] == "actor" for r in refused)
    assert len(stored(region)) == 3 and shop.get(f"/v1/reports/{rid}").json()["state"]["severity"] == 2


def test_resolve_is_limited_like_any_other_write(client, region, mk_event, clock):
    client.post("/v1/reports", json=body(report_id=CART), headers=KEY)
    client.patch(f"/v1/reports/{CART}", json={"field": "status", "value": "assigned"}, headers=KEY)
    ingest.ingest([mk_event("pun", 1, "SET", CART, field="status", value="closed", supersedes=["mum:1"])], source="pun")
    ratelimit.configure(burst=1, refill_per_s=1)
    decision = {"field": "status", "value": "closed", "candidates": ["mum:2", "pun:1"], "reason": "confirmed"}
    done = client.post(f"/v1/reports/{CART}/resolve", json=decision, headers=SUP)
    assert done.status_code == 201 and done.headers["x-ratelimit-limit"] == "1" and left(done) == 0
    more = client.post(f"/v1/reports/{CART}/resolve", json={**decision, "value": "assigned"}, headers=SUP)
    assert more.status_code == 429 and more.json() == {"error": "rate_limited", "scope": "actor", "retry_after_ms": 1000}
    assert sum(1 for e in stored(region) if e["type"] == "RESOLVE") == 1
    assert client.patch(f"/v1/reports/{CART}", json={"field": "severity", "value": 2}, headers=KEY).status_code == 200  # the officer has his own bucket


def test_a_write_refused_for_its_own_reason_is_charged_and_says_what_is_left(shop, region, clock):
    ratelimit.configure(burst=3, refill_per_s=1)
    rid = add(shop, BOB, report_id=CART).json()["report"]["report_id"]
    stale = shop.patch(f"/v1/reports/{rid}", json={"field": "severity", "value": 2, "expected": ["mum:99"]}, headers=BOB)
    assert stale.status_code == 409 and stale.json()["error"] == "stale_edit" and left(stale) == 1
    missing = shop.post("/v1/reports/R-00000000/notes", json={"text": "x"}, headers=BOB)
    assert missing.status_code == 404 and left(missing) == 0
    assert add(shop, BOB).status_code == 429                  # three attempts reached the write path: three tokens
    assert len(stored(region)) == 1
    clock.advance(1)
    exists = add(shop, BOB, report_id=CART)
    assert exists.status_code == 409 and exists.json() == {"error": "report_exists"} and left(exists) == 0


def test_requests_that_never_reach_the_write_path_cost_nothing(shop, region, clock):
    ratelimit.configure(burst=1, refill_per_s=1)
    for _ in range(5):
        assert shop.post("/v1/reports", json={"title": "", "category": "cart", "severity": 1}, headers=BOB).status_code == 422
        assert shop.post("/v1/reports", json=body(category="nonsense"), headers=BOB).status_code == 422
        assert shop.post("/v1/reports", json=body(), headers={"X-API-Key": "nobody"}).status_code == 401
        assert shop.post("/v1/reports", json=body()).status_code == 401
        assert shop.post("/v1/reports", json=body(), headers={"X-API-Key": "dev-admin"}).status_code == 403
    ok = add(shop, BOB)
    assert ok.status_code == 201 and left(ok) == 0 and len(stored(region)) == 1
    assert ratelimit.peek("admin-mum-1").remaining == 1 and ratelimit.snapshot()["refused_actor"] == 0


def test_replication_and_carry_are_never_limited(shop, region, mk_event, clock):
    ratelimit.configure(burst=1, refill_per_s=1)
    add(shop, BOB)
    events = [mk_event("pun", n, "CREATE", f"R-pun-{n}", value=create_value(f"cart {n}"), actor="bob") for n in range(1, 21)]
    assert ingest.ingest(events, source="pun")["new"] == 20   # twenty of "bob"'s events arrive from a peer
    carried = [mk_event("del", n, "CREATE", f"R-del-{n}", value=create_value(f"cart {n}"), actor="bob") for n in range(1, 6)]
    r = shop.post("/v1/carry", json={"events": carried}, headers=BOB)
    assert r.status_code == 200 and "x-ratelimit-remaining" not in r.headers
    assert len(stored(region)) == 26 and ratelimit.snapshot()["refused_actor"] == 0


# ---- where the limit comes from --------------------------------------------------------------------------
def test_the_limit_is_read_from_the_environment(shop, monkeypatch, clock):
    monkeypatch.setattr(config, "settings", Settings(config.settings))               # a v1.1 settings object
    monkeypatch.setenv("RATE_ACTOR_BURST", "2")
    monkeypatch.setenv("RATE_ACTOR_REFILL_PER_S", "4")
    assert ratelimit.limits() == (2, 4.0)
    assert [add(shop, BOB).status_code for _ in range(3)] == [201, 201, 429]
    assert add(shop, BOB).json()["retry_after_ms"] == 250


def test_the_limit_follows_the_settings_object_when_it_carries_it(shop, monkeypatch, clock):
    """Lane D adds the two settings and `POST /admin/config` updates them; the limiter picks that up by itself."""
    monkeypatch.setenv("RATE_ACTOR_BURST", "50")                                     # the settings win over the environment
    monkeypatch.setattr(config, "settings", Settings(config.settings, rate_actor_burst=2, rate_actor_refill_per_s=1))
    assert [add(shop, BOB).status_code for _ in range(3)] == [201, 201, 429]
    monkeypatch.setattr(config, "settings", Settings(config.settings, rate_actor_burst=0, rate_actor_refill_per_s=1))
    assert [add(shop, BOB).status_code for _ in range(5)] == [201] * 5              # switched off at runtime
    monkeypatch.setattr(config, "settings", Settings(config.settings, rate_actor_burst=1, rate_actor_refill_per_s=1))
    assert [add(shop, BOB).status_code for _ in range(2)] == [201, 429]             # switched on again: a fresh bucket


def test_raising_and_lowering_the_burst_at_runtime(shop, clock):
    ratelimit.configure(burst=2, refill_per_s=1)
    add(shop, BOB), add(shop, BOB)
    assert add(shop, BOB).status_code == 429
    ratelimit.configure(burst=5)                              # a bigger bucket does not hand out tokens by itself
    assert add(shop, BOB).status_code == 429
    clock.advance(10)
    assert [left(add(shop, BOB)) for _ in range(5)] == [4, 3, 2, 1, 0]
    clock.advance(10)
    ratelimit.configure(burst=1)                              # a smaller bucket caps what was saved up
    assert [add(shop, BOB).status_code for _ in range(2)] == [201, 429]


@pytest.mark.parametrize("burst,refill,expected", [
    ("abc", "1", (0, 1.0)), ("-4", "1", (0, 1.0)), ("3", "0", (3, 0.0)), ("3", "-2", (3, 1.0)), ("3", "nan", (3, 1.0)),
    ("3", "inf", (3, 1.0)), ("3", "x", (3, 1.0)), ("", "", (0, 1.0)), ("7", "2.5", (7, 2.5)),
])
def test_unusable_settings_fall_back_to_safe_values(monkeypatch, burst, refill, expected):
    monkeypatch.setattr(config, "settings", Settings(config.settings))
    monkeypatch.setenv("RATE_ACTOR_BURST", burst)
    monkeypatch.setenv("RATE_ACTOR_REFILL_PER_S", refill)
    assert ratelimit.limits() == expected


# ---- the pieces other lanes use --------------------------------------------------------------------------
def test_snapshot_reports_the_limit_and_the_refusals_for_the_status_block(shop, clock):
    ratelimit.configure(burst=1, refill_per_s=2)
    add(shop, BOB), add(shop, BOB), add(shop, BOB), add(shop, ALICE), add(shop, ALICE)
    assert ratelimit.snapshot() == {"actor_burst": 1, "actor_refill_per_s": 2.0, "refused_actor": 3}
    assert set(ratelimit.snapshot()) <= {"actor_burst", "actor_refill_per_s", "region_limit_per_s", "refused_actor", "refused_region"}


def test_the_key_function_decides_whose_allowance_a_request_spends(shop, monkeypatch, clock):
    assert ratelimit.key(Auth("alice", "officer")) == "alice"                          # today: the API key's actor
    ratelimit.configure(burst=2, refill_per_s=1)
    monkeypatch.setattr(ratelimit, "key", lambda auth: "one-person")                  # the swap the joining step makes
    assert [add(shop, who).status_code for who in (ALICE, BOB, ALICE)] == [201, 201, 429]


def test_a_direct_caller_is_limited_by_its_actor_unless_it_names_a_key(region, clock):
    ratelimit.configure(burst=1, refill_per_s=1)
    assert writes.local_write(None, "CREATE", None, create_value("a"), "worker").rate.remaining == 0
    with pytest.raises(ratelimit.RateLimited) as refused:
        writes.local_write(None, "CREATE", None, create_value("b"), "worker")
    assert refused.value.decision.retry_after_ms == 1000 and not refused.value.decision.allowed
    assert writes.local_write(None, "CREATE", None, create_value("c"), "worker", rate_key="someone-else").rate.remaining == 0


def test_many_threads_never_get_more_than_the_burst(region, clock):
    ratelimit.configure(burst=5, refill_per_s=1)

    def attempt(n):
        try:
            writes.local_write(None, "CREATE", None, create_value(f"r{n}"), "bob" if n % 2 else "alice")
            return n % 2
        except ratelimit.RateLimited:
            return None

    with ThreadPoolExecutor(max_workers=16) as pool:
        accepted = [a for a in pool.map(attempt, range(80)) if a is not None]
    assert accepted.count(0) == 5 and accepted.count(1) == 5                           # five each, whatever the interleaving
    assert sorted(e["seq"] for e in stored(region)) == list(range(1, 11))             # and the sequence is gap-free
    assert ratelimit.snapshot()["refused_actor"] == 70


def test_buckets_that_have_refilled_are_forgotten(monkeypatch, clock):
    ratelimit.configure(burst=2, refill_per_s=1)
    monkeypatch.setattr(ratelimit, "MAX_BUCKETS", 10)
    for n in range(30):
        ratelimit.take(f"visitor-{n}")
    assert len(ratelimit._buckets) == 30                      # all of them still owe a token: nothing can be dropped
    clock.advance(5)
    ratelimit.take("late")
    assert set(ratelimit._buckets) == {"late"}                # the others are full again, which is the same as unknown
    assert ratelimit.peek("visitor-3").remaining == 2


# ---- the browser can read the headers ----------------------------------------------------------------------
def test_the_rate_headers_are_exposed_to_the_browser_on_accepted_and_refused_writes(shop, clock):
    ratelimit.configure(burst=1, refill_per_s=1)
    ok = shop.post("/v1/reports", json=body(), headers={**BOB, **ORIGIN_OK})
    refused = shop.post("/v1/reports", json=body(), headers={**BOB, **ORIGIN_OK})
    assert (ok.status_code, refused.status_code) == (201, 429)
    for r in (ok, refused):
        exposed = r.headers["access-control-expose-headers"].lower()
        assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert all(h in exposed for h in ("x-ratelimit-limit", "x-ratelimit-remaining", "retry-after"))
