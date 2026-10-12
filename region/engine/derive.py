"""derive() v1.1 (owner: Rahil). Pure function of a SET of events of one report.

The result depends only on the set, never on arrival order or duplicates. For
each field the writes that nobody replaced are live. If they disagree that is a
conflict: policy.py picks the value shown meanwhile and derive() records a
receipt with every candidate and the rule used. Copies of one client write
(same op_id) count once.
"""
from contracts.schemas import FIELDS
from region.engine import canonical
from region.engine.policy import POLICY_V1, POLICY_VERSION  # noqa: F401  (/v1/status reads POLICY_VERSION here)


def order(e: dict):
    """Tie-break order (hlc_l, hlc_c, origin, seq). Total, because (origin, seq) is unique."""
    return (e["hlc"][0], e["hlc"][1], e["origin"], e["seq"])


def _fval(e: dict, f: str):
    """Value of field f in e: a CREATE carries all initial fields, a SET/RESOLVE one."""
    return e["value"][f] if e["type"] == "CREATE" else e["value"]


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def well_formed(e) -> bool:
    """True when e has the shape derive() relies on.

    Anything else is ignored, the same way on every node, so one malformed event
    can never make derive() raise and block the reports around it.
    """
    if not isinstance(e, dict) or "value" not in e:
        return False
    hlc, sup, kind, value = e.get("hlc"), e.get("supersedes"), e.get("type"), e["value"]
    return (
        isinstance(e.get("event_id"), str) and isinstance(e.get("origin"), str) and _int(e.get("seq"))
        and isinstance(hlc, (list, tuple)) and len(hlc) == 2 and all(_int(x) for x in hlc)
        and isinstance(sup, (list, tuple)) and all(isinstance(s, str) for s in sup)
        and (kind != "CREATE" or isinstance(value, dict))
        and (kind not in ("SET", "RESOLVE") or isinstance(e.get("field"), str))
        and (kind != "NOTE" or (isinstance(value, dict) and isinstance(value.get("text"), str)))
    )


def op_groups(evs) -> dict:
    """{event_id: ids of every copy of the same client write, oldest first}.

    A client that gets no answer retries on another node with the same op_id, so one
    write can exist as two events. Events are copies when they share an op_id AND say
    the same thing (type, field, value); the same op_id with different content is not
    merged. Only events that have a copy appear in the result.
    """
    by_write = {}
    for e in evs:
        op = e.get("op_id")
        if isinstance(op, str) and op:
            by_write.setdefault(canonical.dumps([op, e["type"], e.get("field"), e["value"]]), []).append(e)
    groups = {}
    for copies in by_write.values():
        if len(copies) > 1:
            ids = tuple(e["event_id"] for e in sorted(copies, key=order))
            groups.update((i, ids) for i in ids)
    return groups


def _with_copies(groups: dict, ids) -> set:
    """ids plus every copy of each: replacing one copy of a retried write replaces them all."""
    return {c for i in ids for c in groups.get(i, (i,))}


def _first_copies(groups: dict, evs) -> list:
    """evs without the later copies of a retried write."""
    return [e for e in evs if groups.get(e["event_id"], (e["event_id"],))[0] == e["event_id"]]


def _receipt(field: str, pairs: list, value, rule: str, review: bool, flags: list, version: int) -> dict:
    """What a conflict looked like and how it was settled (plan 6.7).

    values and event_ids repeat the candidates in the older v0 shape, which the contract keeps.
    """
    return {
        "field": field,
        "relationship": "CONCURRENT",
        "candidates": [{"event_id": e["event_id"], "value": v, "origin": e["origin"],
                        "actor": e.get("actor"), "wall": e.get("wall")} for e, v in pairs],
        "rule": rule,
        "policy_version": version,
        "provisional": value,
        "needs_review": review,
        "flags": flags,
        "values": [v for _, v in pairs],
        "event_ids": [e["event_id"] for e, _ in pairs],
    }


def derive(events, policy=POLICY_V1) -> dict:
    """events: all events of ONE report, any order, duplicates allowed.

    Returns {"state": {field: value, ..., "notes": [{"id", "text"}]},
             "live": {field: [event_id, ...]},  # values nobody replaced, oldest first
             "conflicts": [receipt, ...],       # at most one per field, in FIELDS order
             "incomplete": True until the CREATE has arrived,
             "policy_version": policy.version}
    """
    evs = list({e["event_id"]: e for e in events if well_formed(e)}.values())
    groups = op_groups(evs)
    live = {}
    for f in FIELDS:
        cands = [
            e for e in evs
            if (e["type"] == "CREATE" and f in e["value"]) or (e["type"] in ("SET", "RESOLVE") and e["field"] == f)
        ]
        gone = _with_copies(groups, {sid for e in cands for sid in e["supersedes"]})
        kept = _first_copies(groups, sorted((e for e in cands if e["event_id"] not in gone), key=order))
        live[f] = [(e, _fval(e, f)) for e in kept]
    view = {
        "state": {},
        "live": {f: [e["event_id"] for e, _ in live[f]] for f in FIELDS},
        "conflicts": [],
        "incomplete": not any(e["type"] == "CREATE" for e in evs),
        "policy_version": policy.version,
    }
    for f in FIELDS:
        values = [v for _, v in live[f]]
        if len({canonical.dumps(v) for v in values}) > 1:
            value, rule, review, flags = policy.resolve(f, live[f])
            view["conflicts"].append(_receipt(f, live[f], value, rule, review, flags, policy.version))
        else:
            value = values[-1] if values else None  # one value, or several equal ones: not a conflict
        view["state"][f] = value
    view["state"]["notes"] = [
        {"id": e["event_id"], "text": e["value"]["text"]}
        for e in _first_copies(groups, sorted(evs, key=order)) if e["type"] == "NOTE"
    ]
    return view
