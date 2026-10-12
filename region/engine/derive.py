"""derive() v0 (owner: Rahil). Pure function of a SET of events of one report.

Result depends only on the set, never on arrival order or duplicates.
v0 rule: when live values disagree show the latest by (hlc, origin, seq)
and flag a conflict. PDF 2 replaces the single rule with the per-field policy;
the function shape stays the same.
"""
from contracts.schemas import FIELDS
from region.engine import canonical

POLICY_VERSION = 0


def order(e: dict):
    """Tie-break order (hlc_l, hlc_c, origin, seq). Total, because (origin, seq) is unique."""
    return (e["hlc"][0], e["hlc"][1], e["origin"], e["seq"])


def _fval(e: dict, f: str):
    """Value of field f in e: a CREATE carries all initial fields, a SET/RESOLVE one."""
    return e["value"][f] if e["type"] == "CREATE" else e["value"]


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


def derive(events) -> dict:
    """events: all events of ONE report, any order, duplicates allowed.

    Returns {"state": {field: value, ..., "notes": [{"id", "text"}]},
             "live": {field: [event_id, ...]},  # values nobody superseded, in tie-break order
             "conflicts": [{"field", "values", "event_ids", "rule", "needs_review"}],
             "incomplete": True until the CREATE has arrived,
             "policy_version": POLICY_VERSION}
    """
    evs = list({e["event_id"]: e for e in events}.values())
    groups = op_groups(evs)
    view = {
        "state": {},
        "live": {},
        "conflicts": [],
        "incomplete": not any(e["type"] == "CREATE" for e in evs),
        "policy_version": POLICY_VERSION,
    }
    for f in FIELDS:
        cands = [
            e for e in evs
            if (e["type"] == "CREATE" and isinstance(e["value"], dict) and f in e["value"])
            or (e["type"] in ("SET", "RESOLVE") and e["field"] == f)
        ]
        gone = _with_copies(groups, {sid for e in cands for sid in e["supersedes"]})
        live = _first_copies(groups, sorted((e for e in cands if e["event_id"] not in gone), key=order))
        vals = [_fval(e, f) for e in live]
        view["live"][f] = [e["event_id"] for e in live]
        view["state"][f] = vals[-1] if vals else None
        if len({canonical.dumps(v) for v in vals}) > 1:
            view["conflicts"].append({
                "field": f,
                "values": vals,
                "event_ids": view["live"][f],
                "rule": "v0-latest",
                "needs_review": True,
            })
    view["state"]["notes"] = [
        {"id": e["event_id"], "text": e["value"]["text"]}
        for e in _first_copies(groups, sorted(evs, key=order)) if e["type"] == "NOTE"
    ]
    return view
