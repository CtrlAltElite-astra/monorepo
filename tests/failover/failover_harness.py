"""T17 harness (owner: Rahil): one shopper's browser, behaving as the failover SDK of plan 6.3.

Python twin of web/src/sdk/syncshieldClient.ts, so the backend can prove zero-loss
failover without a browser. It speaks contract v1.1 through a pluggable transport:

    send(region, method, path, body) -> (status, json)     raises Unreachable on no answer

test_t17_in_memory.py plugs in in-memory nodes (runs today); test_t17_live.py plugs in
real region processes over HTTP (runs once /v1/carry and write path v2 are merged).
"""
import uuid

import httpx

CARRY_PAGE = 200           # contract 5.3: at most 200 events per /v1/carry call
MAX_OUTBOX = 500
FAILBACK_GOOD_PROBES = 3


class Unreachable(Exception):
    """No answer: timeout, refused connection, or the process is gone."""


class Shopper:
    def __init__(self, send, regions, max_outbox=MAX_OUTBOX):
        self.send, self.regions, self.max_outbox = send, list(regions), max_outbox
        self.down, self.good_probes, self.last = set(), {}, None
        self.outbox = {}        # event_id -> signed event the shopper was acknowledged, oldest first
        self.acknowledged = {}  # event_id -> region that answered 2xx: what the shopper was told is saved
        self.errors = 0         # actions no node answered: the only error a shopper would ever see
        self.timeline = []      # one (region or None, nodes tried before it, status) per action
        self.carried = []       # every /v1/carry result row

    # ---- the one call every storefront action goes through -----------------
    def call(self, method, path, body):
        """Send to the first node that answers, carrying the outbox to a node first used now.

        A retry reuses the op_id, so a write that committed but never answered counts once.
        403 / 409 / 422 are answers, not failover cases.
        """
        body = dict(body, op_id=body.get("op_id") or str(uuid.uuid4()))
        tried = 0
        for region in self._order():
            try:
                if region != self.last and self.outbox:
                    self.carry(region)
                status, resp = self.send(region, method, path, body)
            except Unreachable:
                self._mark_down(region)
                tried += 1
                continue
            if status >= 500:
                self._mark_down(region)
                tried += 1
                continue
            if 200 <= status < 300:
                ev = resp["event"]
                self.outbox[ev["event_id"]] = ev
                self.acknowledged[ev["event_id"]] = region
                self.last = region
                self._bound()
            self.timeline.append((region, tried, status))
            return status, resp
        self.errors += 1  # every node down: the SDK would queue the action on the device
        self.timeline.append((None, tried, None))
        return None, None

    def carry(self, region):
        """POST the whole outbox, oldest first, in pages. Raises Unreachable if the node does not answer."""
        events = list(self.outbox.values())
        for i in range(0, len(events), CARRY_PAGE):
            status, resp = self.send(region, "POST", "/v1/carry", {"events": events[i:i + CARRY_PAGE]})
            if status >= 500:
                raise Unreachable(f"{region} /v1/carry {status}")
            if status == 200:
                self.carried += [dict(row, to=region) for row in resp["results"]]

    # ---- background work of the SDK ----------------------------------------
    def prune(self):
        """Drop every outbox event that some peer of the serving node has acknowledged."""
        if self.last is None:
            return
        try:
            status, s = self.send(self.last, "GET", "/v1/status", None)
        except Unreachable:
            return
        if status != 200:
            return
        # contract 5.4 calls it acked; the status builder today reports the same heads as their_heads
        acked = [p.get("acked") or p.get("their_heads") or {} for p in (s.get("peers") or {}).values()]
        for eid, e in list(self.outbox.items()):
            if any(a.get(e["origin"], 0) >= e["seq"] for a in acked):
                del self.outbox[eid]

    def probe(self):
        """GET /healthz on nodes marked down; one comes back after FAILBACK_GOOD_PROBES good answers."""
        for region in list(self.down):
            try:
                ok = self.send(region, "GET", "/healthz", None)[0] == 200
            except Unreachable:
                ok = False
            self.good_probes[region] = self.good_probes.get(region, 0) + 1 if ok else 0
            if self.good_probes[region] >= FAILBACK_GOOD_PROBES:
                self.down.discard(region)

    # ---- helpers -----------------------------------------------------------
    def _order(self):
        """Nodes that answered last time first, in preference order; nodes marked down last."""
        return [r for r in self.regions if r not in self.down] + [r for r in self.regions if r in self.down]

    def _mark_down(self, region):
        self.down.add(region)
        self.good_probes[region] = 0
        if self.last == region:
            self.last = None

    def _bound(self):
        while len(self.outbox) > self.max_outbox:
            del self.outbox[next(iter(self.outbox))]


def http_send(urls: dict, headers: dict, timeout_s: float = 0.6):
    """Transport to real regions. Any network failure or timeout is Unreachable."""
    client = httpx.Client(timeout=timeout_s)

    def send(region, method, path, body):
        try:
            r = client.request(method, urls[region] + path, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise Unreachable(f"{region}: {exc!r}") from exc
        return r.status_code, (r.json() if r.content else None)

    send.close = client.close
    return send
