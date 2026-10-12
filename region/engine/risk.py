"""Data at risk (owner: Rahil). Pure helper for the status builder (plan 6.8).

What a node would lose if it died right now: its own events that no peer has
acknowledged yet.
"""


def data_at_risk(own_events, peer_heads: dict, now_ms: int) -> dict:
    """own_events: (seq, priority, written_ms) for every event THIS node originated.
    peer_heads: {peer: highest seq of this node's events that the peer acknowledged}.

    An event is safe once ANY peer holds it, so it is at risk while its seq is above every head.
    """
    safe = max(peer_heads.values(), default=0)
    at_risk = sorted(e for e in own_events if e[0] > safe)
    return {
        "unreplicated": len(at_risk),
        "critical_unreplicated": sum(1 for _, priority, _ in at_risk if priority == 0),
        "oldest_age_ms": max(0, now_ms - at_risk[0][2]) if at_risk else 0,
    }
