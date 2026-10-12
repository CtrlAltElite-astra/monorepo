"""A node in memory (owner: Rahil). Pure: no storage, no network, the caller supplies the clock.

Replica follows the same write and receive rules as the real region, built only
from engine functions. The convergence fuzz test runs three of them, and the
example fixtures are made with it. region/api/writes.py must fill supersedes,
observed, priority and sig the same way.
"""
from datetime import datetime, timezone

from contracts.schemas import validate_field
from region.engine import admit, canonical, digest, hlc, policy, priority, signing
from region.engine.derive import derive, well_formed


def make_event(origin, seq, type_, field=None, value=None, supersedes=(), hlc=None, report_id="R-1", **extra) -> dict:
    """One sealed event envelope (contract 5.1): the given fields, defaults for the rest, and its hash.

    extra overrides or adds keys, e.g. op_id="..." or observed=[...] (observed is absent unless given).
    """
    ev = dict(v=1, event_id=f"{origin}:{seq}", origin=origin, seq=seq, report_id=report_id, type=type_,
              field=field, value=value, supersedes=list(supersedes), hlc=list(hlc or (1000 + seq, 0)),
              priority=1, op_id=None, actor=f"agent-{origin}-1", reason=None, wall="", sig=None)
    ev.update(extra)
    ev["hash"] = canonical.event_hash(ev)
    return ev


class Replica:
    def __init__(self, region: str, private_key=None):
        self.region, self.private_key = region, private_key  # with a key, every event it writes is signed
        self.events, self.seq, self.clock = {}, 0, (0, 0)

    def of(self, report_id: str) -> list:
        return [e for e in self.events.values() if e["report_id"] == report_id]

    def view(self, report_id: str) -> dict:
        return derive(self.of(report_id))

    def views(self) -> dict:
        return {rid: self.view(rid) for rid in sorted({e["report_id"] for e in self.events.values()})}

    def heads(self) -> dict:
        """{origin: n} where this node holds seq 1..n of that origin: the gap-safe watermark."""
        seqs = {}
        for e in self.events.values():
            seqs.setdefault(e["origin"], set()).add(e["seq"])
        out = {}
        for origin, held in seqs.items():
            n = 0
            while n + 1 in held:
                n += 1
            out[origin] = n
        return out

    def event_digest(self) -> str:
        return digest.event_digest((i, e["hash"]) for i, e in self.events.items())

    def state_hash(self) -> str:
        return digest.state_hash(self.views())

    def write(self, now_ms: int, type_: str, report_id: str, field=None, value=None, op_id=None,
              reason=None, bulk=False) -> dict:
        """A local write: one new event, built the way the write path builds it.

        An op_id this node already holds (applied here, or arrived from a peer) is not written
        again: the existing event is returned.
        """
        if op_id:
            for e in self.events.values():
                if e.get("op_id") == op_id:
                    return e
        if type_ in ("SET", "RESOLVE"):
            value = validate_field(field, value)
        if type_ != "CREATE" and not self.of(report_id):
            raise KeyError(report_id)
        before = self.view(report_id)
        self.seq += 1
        self.clock = hlc.tick(self.clock, now_ms)
        extra = {}
        observed = policy.observed_for(type_, field, value, before)
        if observed is not None:
            extra["observed"] = observed
        ev = make_event(
            self.region, self.seq, type_, field, value,
            supersedes=before["live"].get(field, []) if type_ in ("SET", "RESOLVE") else [],
            hlc=self.clock, report_id=report_id, op_id=op_id, reason=reason,
            priority=priority.classify({"type": type_, "field": field, "value": value}, before, bulk),
            wall=datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat(timespec="seconds"), **extra)
        if self.private_key:
            ev["sig"] = signing.sign_with(self.private_key, ev["hash"])
        self.events[ev["event_id"]] = ev
        return ev

    def receive(self, events, now_ms: int, public_keys=None) -> list:
        """Events from a peer or from a browser's outbox, checked the way ingest checks them.

        Skipped: a malformed event, a hash that does not match, an id already held. With
        public_keys (the rule for /v1/carry, admit.reject_reason) an event must also carry its
        origin's signature. Returns the ids that were new.
        """
        new = []
        for ev in events:
            if public_keys is not None:
                if admit.reject_reason(ev, public_keys) or ev["event_id"] in self.events:
                    continue
            elif not well_formed(ev) or ev["event_id"] in self.events or canonical.event_hash(ev) != ev.get("hash"):
                continue
            self.events[ev["event_id"]] = ev
            self.clock = hlc.receive(self.clock, tuple(ev["hlc"]), now_ms)
            new.append(ev["event_id"])
        return new
