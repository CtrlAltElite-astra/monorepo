"""derive() v1 (owner: Rahil; implemented on request by Miti). Pure function of a SET of events of one report.

Result depends only on the set, never on arrival order or duplicates. No I/O, no randomness, no
wall clock in any decision.

Policy v1 (plan v1.1 section 6.6), applied only when the live values of a field disagree:

    title, description, category   latest by tie-break order            no review
    severity                       highest value                         receipt only
    status                         most active (open < assigned < resolved < closed = cancelled);
                                   lowest wins, latest breaks the tie    review
    assigned_team                  earliest by tie-break order; flag DOUBLE_DISPATCH   review
    location                       latest; review if candidates >= 200 m apart
    people_estimate                highest, never summed                 review
    notes                          union by event id

Plus two rules that are not per-field:

    op_id groups        events sharing an op_id are ONE write (a retried request that landed on two
                        nodes): superseding one member supersedes all; only one member stays live and
                        only one note is shown. No double items, no phantom conflicts.
    CLOSE_VS_NEW_INFO   a live close (closed / cancelled) and a NOTE or severity >= 4 write that did
                        not observe each other raise a review item; status stays as derived.
                        Events without `observed` (older nodes) never trigger it.
"""
import math

from contracts.schemas import FIELDS
from region.engine import canonical

POLICY_VERSION = 1

# status "activity" ladder: lower = more active = safer to show while in doubt
LADDER = {"open": 0, "assigned": 1, "resolved": 2, "closed": 3, "cancelled": 3}
FAR_METRES = 200.0
CLOSED = ("closed", "cancelled")


def order(e: dict):
    """Tie-break order (hlc_l, hlc_c, origin, seq). Total, because (origin, seq) is unique."""
    return (e["hlc"][0], e["hlc"][1], e["origin"], e["seq"])


def _fval(e: dict, f: str):
    """Value of field f in e: a CREATE carries all initial fields, a SET/RESOLVE one."""
    return e["value"][f] if e["type"] == "CREATE" else e["value"]


# ---- op_id groups ---------------------------------------------------------
def _op_groups(evs: list) -> dict:
    """event_id -> sorted tuple of every event id sharing its op_id (only groups of 2 or more)."""
    by_op: dict = {}
    for e in evs:
        op = e.get("op_id")
        if op:
            by_op.setdefault(op, []).append(e["event_id"])
    out = {}
    for ids in by_op.values():
        if len(ids) > 1:
            grp = tuple(sorted(ids))
            for i in ids:
                out[i] = grp
    return out


def _one_per_group(events: list, groups: dict) -> list:
    """Keep the earliest member (tie-break order) of each op group; input must be sorted by order."""
    seen, out = set(), []
    for e in events:
        g = groups.get(e["event_id"])
        if g is not None:
            if g in seen:
                continue
            seen.add(g)
        out.append(e)
    return out


# ---- policy ---------------------------------------------------------------
def _metres(a: dict, b: dict) -> float:
    la1, lo1, la2, lo2 = (a["lat_e6"] / 1e6, a["lon_e6"] / 1e6, b["lat_e6"] / 1e6, b["lon_e6"] / 1e6)
    p1, p2 = math.radians(la1), math.radians(la2)
    dphi, dlam = p2 - p1, math.radians(lo2 - lo1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * 6371000.0 * math.asin(min(1.0, math.sqrt(h)))


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else float("-inf")


def _resolve(f: str, live: list):
    """live: >= 2 events with distinct values, sorted by tie-break order.
    Returns (winner_event, rule, needs_review, flags)."""
    vals = {e["event_id"]: _fval(e, f) for e in live}
    if f in ("title", "description", "category"):
        return live[-1], "LATEST_HLC", False, []
    if f == "severity":
        return max(live, key=lambda e: (_num(vals[e["event_id"]]), order(e))), "HIGHEST", False, []
    if f == "status":
        rank = lambda e: LADDER.get(vals[e["event_id"]], 99)  # noqa: E731
        lowest = min(rank(e) for e in live)
        return [e for e in live if rank(e) == lowest][-1], "STATUS_MOST_ACTIVE", True, []
    if f == "assigned_team":
        teams = {canonical.dumps(v) for v in vals.values() if v is not None}
        flags = ["DOUBLE_DISPATCH"] if len(teams) > 1 else []
        return live[0], "EARLIEST_HLC", True, flags
    if f == "location":
        pts = [v for v in vals.values() if isinstance(v, dict)]
        far = any(_metres(a, b) >= FAR_METRES for i, a in enumerate(pts) for b in pts[i + 1:])
        return live[-1], "LATEST_HLC", far, (["FAR_APART"] if far else [])
    if f == "people_estimate":
        return max(live, key=lambda e: (_num(vals[e["event_id"]]), order(e))), "HIGHEST", True, []
    return live[-1], "LATEST_HLC", True, []  # unknown field: be conservative, flag it


def _candidate(e: dict, f: str) -> dict:
    return {"event_id": e["event_id"], "value": _fval(e, f) if f else None, "origin": e["origin"],
            "actor": e.get("actor"), "wall": e.get("wall")}


def _close_vs_new_info(evs: list, live_status: list, groups: dict) -> list:
    """One review item per live close that did not observe a note / big upgrade (and vice versa).

    The item lists the close first, then every unseen change, so a supervisor decides once."""
    out = []
    closes = [e for e in live_status if _fval(e, "status") in CLOSED and e.get("observed") is not None]
    infos = [e for e in evs
             if e.get("observed") is not None
             and (e["type"] == "NOTE"
                  or (e["type"] == "SET" and e["field"] == "severity" and _num(e["value"]) >= 4))]
    for c in sorted(closes, key=order):
        unseen = []
        for i in sorted(infos, key=order):
            g = groups.get(i["event_id"])
            if g is not None and g == groups.get(c["event_id"]):
                continue
            if i["event_id"] in c["observed"] or c["event_id"] in i["observed"]:
                continue
            unseen.append(i)
        unseen = _one_per_group(unseen, groups)
        if not unseen:
            continue
        out.append({
            "field": "status",
            "relationship": "CONCURRENT",
            "candidates": [_candidate(c, "status")] + [{**_candidate(i, None), "value": _fval_any(i)} for i in unseen],
            "values": [_fval(c, "status")] + [_fval_any(i) for i in unseen],
            "event_ids": [c["event_id"]] + [i["event_id"] for i in unseen],
            "rule": "CLOSE_VS_NEW_INFO",
            "policy_version": POLICY_VERSION,
            "provisional": _fval(c, "status"),
            "needs_review": True,
            "flags": ["CLOSE_VS_NEW_INFO"],
        })
    return out


def _fval_any(e: dict):
    """Display value of an information event: a note's text or a severity value."""
    return e["value"]["text"] if e["type"] == "NOTE" else e["value"]


def derive(events) -> dict:
    """events: all events of ONE report, any order, duplicates allowed.

    Returns {"state": {field: value, ..., "notes": [{"id", "text"}]},
             "live": {field: [event_id, ...]},   # values nobody superseded, in tie-break order
             "conflicts": [receipt, ...],        # see below
             "incomplete": True until the CREATE has arrived,
             "policy_version": POLICY_VERSION}

    A receipt is {"field", "relationship", "candidates": [{event_id, value, origin, actor, wall}],
    "values", "event_ids" (same order as candidates), "rule", "policy_version", "provisional",
    "needs_review", "flags"}. `values` and `event_ids` are kept for older readers.
    """
    evs = list({e["event_id"]: e for e in events}.values())
    groups = _op_groups(evs)
    view = {
        "state": {},
        "live": {},
        "conflicts": [],
        "incomplete": not any(e["type"] == "CREATE" for e in evs),
        "policy_version": POLICY_VERSION,
    }
    live_status: list = []
    for f in FIELDS:
        cands = [
            e for e in evs
            if (e["type"] == "CREATE" and isinstance(e["value"], dict) and f in e["value"])
            or (e["type"] in ("SET", "RESOLVE") and e["field"] == f)
        ]
        gone = {sid for e in cands for sid in e["supersedes"]}
        # superseding one member of an op group supersedes the whole group
        gone |= {m for sid in list(gone) for m in groups.get(sid, ())}
        live = _one_per_group(sorted((e for e in cands if e["event_id"] not in gone), key=order), groups)
        view["live"][f] = [e["event_id"] for e in live]
        if f == "status":
            live_status = live
        if not live:
            view["state"][f] = None
            continue
        distinct = {canonical.dumps(_fval(e, f)) for e in live}
        if len(distinct) == 1:
            view["state"][f] = _fval(live[0], f)   # concurrent but equal: not a conflict
            continue
        winner, rule, review, flags = _resolve(f, live)
        view["state"][f] = _fval(winner, f)
        view["conflicts"].append({
            "field": f,
            "relationship": "CONCURRENT",
            "candidates": [_candidate(e, f) for e in live],
            "values": [_fval(e, f) for e in live],
            "event_ids": [e["event_id"] for e in live],
            "rule": rule,
            "policy_version": POLICY_VERSION,
            "provisional": _fval(winner, f),
            "needs_review": review,
            "flags": flags,
        })

    view["conflicts"].extend(_close_vs_new_info(evs, live_status, groups))

    notes = _one_per_group(sorted((e for e in evs if e["type"] == "NOTE"), key=order), groups)
    view["state"]["notes"] = [{"id": e["event_id"], "text": e["value"]["text"]} for e in notes]
    return view
