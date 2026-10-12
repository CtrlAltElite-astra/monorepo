"""Scaling controls at runtime (owner: Simrit): power, the whole-region write limit, the load figures.

Contract v1.2 (section 3). Everything is OFF by default: `REGION_WRITE_LIMIT_PER_S=0` means no limit and
`POWER=medium` batches only when writes are already queued, so a lone write behaves exactly as before.

`install(app)` is the single hook, and the only thing another lane has to call:
    * adds `RegionGate`, an ASGI middleware that counts write requests and refuses them with
      503 {"error":"overloaded","scope":"region"} + Retry-After when the region is over its limit;
    * switches `store.tx` to group commit (region/sync/groupcommit.py), so the power setting has a real effect.
Place the call before the CORS middleware is added, so the 503 also carries the CORS headers.

What is limited: write requests under /v1/reports (create, patch, note, resolve). Never limited: reads,
/v1/carry, /v1/events/has, /internal (peer replication) and /admin. An op_id that is already stored is a replay
and is never refused and never counted.
"""
import asyncio
import json
import threading
import time
from collections import deque

from region import config, store
from region.sync import groupcommit

LOAD_WINDOW_S = 3.0          # writes-per-second is measured over this many seconds
OVERLOAD_MEMORY_S = 2.0      # a refusal in the last few seconds keeps the node reporting OVERLOADED
MAX_PEEK_BYTES = 64 * 1024   # bodies larger than this are not inspected for an op_id
WRITE_METHODS = ("POST", "PATCH", "PUT", "DELETE")


def _is_write(scope) -> bool:
    return scope["method"] in WRITE_METHODS and scope["path"].startswith("/v1/reports")


class _Meter:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.admitted = deque()    # monotonic times of writes admitted (the limiter's window: 1 s)
            self.done = deque()        # monotonic times of writes that succeeded (the load window)
            self.inflight = 0
            self.refused_region = 0
            self.last_refusal = None


METER = _Meter()


def _trim(q: deque, horizon: float) -> None:
    while q and q[0] < horizon:
        q.popleft()


def writes_per_s() -> float:
    now = time.monotonic()
    with METER.lock:
        _trim(METER.done, now - LOAD_WINDOW_S)
        return round(len(METER.done) / LOAD_WINDOW_S, 1)


def queue_depth() -> int:
    with METER.lock:
        return METER.inflight


def batch() -> int:
    return groupcommit.batch_size()


def recently_overloaded() -> bool:
    with METER.lock:
        return METER.last_refusal is not None and time.monotonic() - METER.last_refusal < OVERLOAD_MEMORY_S


def load_block() -> dict:
    return {"writes_per_s": writes_per_s(), "power": config.settings.power, "batch": batch(),
            "queue_depth": queue_depth()}


def rate_block() -> dict:
    s = config.settings
    refused_actor = 0
    try:   # lane A's limiter; absent until it is merged, then its counters appear here without any change
        from region.api import ratelimit
        snap = ratelimit.snapshot() or {}
        refused_actor = int(snap.get("refused_actor", snap.get("refused", 0)))
    except Exception:
        pass
    with METER.lock:
        refused_region = METER.refused_region
    return {"actor_burst": s.rate_actor_burst, "actor_refill_per_s": s.rate_actor_refill_per_s,
            "region_limit_per_s": s.region_write_limit_per_s, "refused_actor": refused_actor,
            "refused_region": refused_region}


def _decide(now: float):
    """Refuse (a reason string) or admit (None) one more write request. Call with the meter lock held."""
    s = config.settings
    if s.write_queue_max > 0 and METER.inflight >= s.write_queue_max:
        return "queue"
    if s.region_write_limit_per_s > 0:
        _trim(METER.admitted, now - 1.0)
        if len(METER.admitted) >= s.region_write_limit_per_s:
            return "limit"
    return None


def _is_replay(headers: dict, body: bytes) -> bool:
    op_id = headers.get(b"idempotency-key", b"").decode("latin-1")[:64] or None
    if body:
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict) and isinstance(parsed.get("op_id"), str) and parsed["op_id"]:
                op_id = parsed["op_id"]
        except ValueError:
            pass
    if not op_id:
        return False
    with store.read() as conn:
        return store.event_by_op(conn, op_id) is not None


async def _read_body(receive, headers) -> tuple:
    """Buffer a small request body so it can be inspected and then replayed to the app untouched."""
    try:
        length = int(headers.get(b"content-length", b"0") or 0)
    except ValueError:
        length = MAX_PEEK_BYTES + 1
    if length > MAX_PEEK_BYTES:
        return None, None
    chunks, messages = [], []
    while True:
        msg = await receive()
        messages.append(msg)
        if msg["type"] != "http.request":
            break
        chunks.append(msg.get("body", b""))
        if not msg.get("more_body"):
            break
    return b"".join(chunks), messages


class RegionGate:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not _is_write(scope):
            return await self.app(scope, receive, send)

        s = config.settings
        pressure = s.region_write_limit_per_s > 0 or s.write_queue_max > 0
        headers = {k: v for k, v in scope.get("headers", [])}
        replay = False
        refuse = None

        with METER.lock:
            refuse = _decide(time.monotonic()) if pressure else None
        if refuse or s.region_write_limit_per_s > 0:
            # only now is the body worth reading: a replay is never refused and never counted
            body, msgs = await _read_body(receive, headers)
            if msgs is not None:
                replay = await asyncio.to_thread(_is_replay, headers, body)
                it = iter(msgs)

                async def replayed(_it=it, _orig=receive):
                    try:
                        return next(_it)
                    except StopIteration:
                        return await _orig()
                receive = replayed

        if refuse and not replay:
            with METER.lock:
                METER.refused_region += 1
                METER.last_refusal = time.monotonic()
            payload = json.dumps({"error": "overloaded", "scope": "region"}).encode()
            await send({"type": "http.response.start", "status": 503,
                        "headers": [(b"content-type", b"application/json"), (b"retry-after", b"1"),
                                    (b"content-length", str(len(payload)).encode())]})
            await send({"type": "http.response.body", "body": payload})
            return

        with METER.lock:
            METER.inflight += 1
            if not replay:
                METER.admitted.append(time.monotonic())
        ok = {"v": False}

        async def watch(message):
            if message["type"] == "http.response.start":
                names = {k.lower() for k, _ in message.get("headers", [])}
                ok["v"] = message["status"] < 300 and b"idempotent-replay" not in names
            await send(message)

        try:
            await self.app(scope, receive, watch)
        finally:
            with METER.lock:
                METER.inflight -= 1
                if ok["v"]:
                    METER.done.append(time.monotonic())


def install(app) -> None:
    """The one hook: region limit gate + group commit. Idempotent for the commit part."""
    app.add_middleware(RegionGate)
    groupcommit.install()
