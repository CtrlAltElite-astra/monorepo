"""A node in memory (owner: Rahil). Pure: no storage, no network, the caller supplies the clock.

Replica follows the same write and receive rules as the real region, built only
from engine functions. The convergence fuzz test runs three of them, and the
T17 harness uses them as nodes. Writes are built by envelope.new_event(), which
follows the real write path (region/api/writes.py): supersedes the live values,
observed as writes._observed records it, priority from engine.priority.classify.
"""
from datetime import datetime, timezone

from contracts.schemas import validate_field
from region.engine import admit, canonical, digest, envelope, hlc, signing
from region.engine.derive import derive, well_formed
from region.engine.envelope import make_event  # noqa: F401  (tests build events with it)


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
              reason=None, bulk=False, credential_id=None) -> dict:
        """A local write: one new event, built the way the write path builds it.

        An op_id this node already holds (applied here, or arrived from a peer) is not written
        again: the existing event is returned. credential_id: the signed-in credential that
        authorised the write (auth mode records it on the event); None in API-key mode.
        """
        if op_id:
            for e in self.events.values():
                if e.get("op_id") == op_id:
                    return e
        if type_ in ("SET", "RESOLVE"):
            value = validate_field(field, value)
        if type_ != "CREATE" and not self.of(report_id):
            raise KeyError(report_id)
        before = self.of(report_id)
        self.seq += 1
        self.clock = hlc.tick(self.clock, now_ms)
        signer = (lambda h: signing.sign_with(self.private_key, h)) if self.private_key else None
        extra = {"credential_id": credential_id} if credential_id else {}
        ev = envelope.new_event(
            self.region, self.seq, self.clock, type_, report_id, field, value, before, op_id=op_id,
            reason=reason, bulk=bulk, signer=signer,
            wall=datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat(timespec="seconds"), **extra)
        self.events[ev["event_id"]] = ev
        return ev

    def receive(self, events, now_ms: int, public_keys=None, is_revoked=admit.never_revoked) -> list:
        """Events from a peer or from a browser's outbox, checked the way ingest checks them.

        Skipped: a malformed event, a hash that does not match, an id already held. With
        public_keys (the rule for /v1/carry, admit.reject_reason) an event must also carry its
        origin's signature, and an event from a credential is_revoked reports is refused.
        Returns the ids that were new.
        """
        new = []
        for ev in events:
            if public_keys is not None:
                if admit.reject_reason(ev, public_keys, is_revoked=is_revoked) or ev["event_id"] in self.events:
                    continue
            elif not well_formed(ev) or ev["event_id"] in self.events or canonical.event_hash(ev) != ev.get("hash"):
                continue
            self.events[ev["event_id"]] = ev
            self.clock = hlc.receive(self.clock, tuple(ev["hlc"]), now_ms)
            new.append(ev["event_id"])
        return new
