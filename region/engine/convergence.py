"""The global convergence badge (owner: Rahil, U2). Pure: the caller fetches every /v1/status.

Never greener than the facts (plan 6.8):

    UNKNOWN       a configured node did not answer: we cannot know, so never green
    INCOMPATIBLE  nodes run different policy versions: equal events would not mean equal state
    CONVERGED     every node holds the same events (event_digest) and shows the same state (state_hash)
    SYNCING       the event sets still differ: replication has not finished (normal, not an error)
    DIVERGED      the SAME events but a DIFFERENT state: derive() is not deterministic. A bug alarm;
                  it must never happen.

DIVERGED is decided on equal event digests rather than equal heads: heads are contiguous
watermarks, so two nodes can show equal heads while one already holds an event beyond a gap.
That is still syncing, not a bug.
"""
BADGES = ("UNKNOWN", "INCOMPATIBLE", "CONVERGED", "SYNCING", "DIVERGED")


def badge(statuses: dict) -> tuple:
    """statuses: {region: its /v1/status body, or None when it did not answer} for EVERY configured
    node (a node not in PEERS is not configured). Returns (badge, reason in plain words)."""
    if not statuses:
        return "UNKNOWN", "no nodes configured"
    silent = sorted(r for r, s in statuses.items() if not isinstance(s, dict))
    if silent:
        return "UNKNOWN", f"no answer from {', '.join(silent)}"
    versions = {s.get("policy_version") for s in statuses.values()}
    if len(versions) > 1:
        return "INCOMPATIBLE", f"policy versions differ: {sorted(versions, key=str)}"
    events = {s.get("event_digest") for s in statuses.values()}
    states = {s.get("state_hash") for s in statuses.values()}
    if None in events or None in states:
        return "UNKNOWN", "a node did not report its digests"
    if len(events) > 1:
        behind = _behind(statuses)
        return "SYNCING", "event sets differ" + (f"; behind: {behind}" if behind else "")
    if len(states) > 1:
        return "DIVERGED", "same events but different state: derive() is not deterministic (bug)"
    return "CONVERGED", f"{len(statuses)} nodes hold the same events and show the same state"


def _behind(statuses: dict) -> str:
    """'pun misses mum:3-5' style summary from the heads every node reports."""
    best = {}
    for s in statuses.values():
        for origin, seq in (s.get("heads") or {}).items():
            best[origin] = max(best.get(origin, 0), seq)
    gaps = []
    for region, s in sorted(statuses.items()):
        heads = s.get("heads") or {}
        for origin, top in sorted(best.items()):
            have = heads.get(origin, 0)
            if have < top:
                gaps.append(f"{region} misses {origin}:{have + 1}" + (f"-{top}" if top > have + 1 else ""))
    return ", ".join(gaps)
