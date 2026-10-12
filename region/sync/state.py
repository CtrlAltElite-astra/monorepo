"""Replication bookkeeping (single owner: Simrit).

The ONLY module that writes origin_progress, peer_view, peer_state and links.
"""
import time

from region import config, store


# ---- per-origin watermarks ----------------------------------------------
def advance(conn, origin: str, seq: int) -> None:
    """Record that (origin, seq) is stored. Contiguous moves only through a
    gap-free prefix, so a hole is never skipped (PDF 2 6.2)."""
    row = conn.execute("SELECT contiguous, max_seen FROM origin_progress WHERE origin=?", (origin,)).fetchone()
    contiguous, max_seen = (row[0], row[1]) if row else (0, 0)
    max_seen = max(max_seen, seq)
    while conn.execute("SELECT 1 FROM events WHERE origin=? AND seq=?", (origin, contiguous + 1)).fetchone():
        contiguous += 1
    conn.execute(
        "INSERT INTO origin_progress (origin, contiguous, max_seen) VALUES (?,?,?) "
        "ON CONFLICT(origin) DO UPDATE SET contiguous=excluded.contiguous, max_seen=excluded.max_seen",
        (origin, contiguous, max_seen),
    )


def contiguous(origin: str) -> int:
    with store.read() as conn:
        if origin == config.settings.region:
            return store.own_head(conn)
        row = conn.execute("SELECT contiguous FROM origin_progress WHERE origin=?", (origin,)).fetchone()
        return row[0] if row else 0


def heads() -> dict:
    """{origin: contiguous} for every origin this region holds (own included)."""
    with store.read() as conn:
        out = {r[0]: r[1] for r in conn.execute("SELECT origin, contiguous FROM origin_progress")}
        out[config.settings.region] = store.own_head(conn)
        return out


def max_seen() -> dict:
    with store.read() as conn:
        out = {r[0]: r[1] for r in conn.execute("SELECT origin, max_seen FROM origin_progress")}
        out[config.settings.region] = store.own_head(conn)
        return out


# ---- peers ---------------------------------------------------------------
def save_peer_view(peer: str, peer_heads: dict) -> None:
    now = time.time()
    with store.tx() as conn:
        for origin, seq in peer_heads.items():
            conn.execute(
                "INSERT INTO peer_view (peer, origin, seq, seen_at) VALUES (?,?,?,?) "
                "ON CONFLICT(peer, origin) DO UPDATE SET seq=excluded.seq, seen_at=excluded.seen_at",
                (peer, origin, seq, now),
            )


def mark_ok(peer: str) -> None:
    with store.tx() as conn:
        conn.execute(
            "INSERT INTO peer_state (peer, last_ok, last_error, fails) VALUES (?,?,NULL,0) "
            "ON CONFLICT(peer) DO UPDATE SET last_ok=excluded.last_ok, last_error=NULL, fails=0",
            (peer, time.time()),
        )


def mark_error(peer: str, error: str) -> None:
    with store.tx() as conn:
        conn.execute(
            "INSERT INTO peer_state (peer, last_ok, last_error, fails) VALUES (?,NULL,?,1) "
            "ON CONFLICT(peer) DO UPDATE SET last_error=excluded.last_error, fails=fails+1",
            (peer, error),
        )


# ---- link switch ---------------------------------------------------------
def link_mode(peer: str) -> str:
    with store.read() as conn:
        row = conn.execute("SELECT mode FROM links WHERE peer=?", (peer,)).fetchone()
        return row[0] if row else "up"


def get_link(peer: str) -> dict:
    with store.read() as conn:
        row = conn.execute("SELECT mode, delay_ms, rate_bps, drop_pct FROM links WHERE peer=?", (peer,)).fetchone()
        if not row:
            return {"mode": "up", "delay_ms": 0, "rate_bps": 0, "drop_pct": 0}
        return {"mode": row[0], "delay_ms": row[1], "rate_bps": row[2], "drop_pct": row[3]}


def set_link(peer: str, mode: str, delay_ms: int = 0, rate_bps: int = 0, drop_pct: int = 0) -> None:
    with store.tx() as conn:
        conn.execute(
            "INSERT INTO links (peer, mode, delay_ms, rate_bps, drop_pct) VALUES (?,?,?,?,?) "
            "ON CONFLICT(peer) DO UPDATE SET mode=excluded.mode, delay_ms=excluded.delay_ms, "
            "rate_bps=excluded.rate_bps, drop_pct=excluded.drop_pct",
            (peer, mode, delay_ms, rate_bps, drop_pct),
        )


def set_link_mode(peer: str, mode: str) -> None:
    set_link(peer, mode)


def all_links() -> dict:
    return {p: link_mode(p) for p in config.settings.peers}


def all_links_detail() -> dict:
    return {p: get_link(p) for p in config.settings.peers}


# ---- status --------------------------------------------------------------
ISOLATED_FAILS = 3
ISOLATED_SILENCE_S = 5.0
_current_level = "HEALTHY"


def current_level() -> str:
    """Instant cached level for zero-overhead /healthz probes."""
    return _current_level


def set_current_level(lvl: str) -> None:
    global _current_level
    _current_level = lvl


def compute_risk(conn) -> dict:
    """Compute data-at-risk for this region (PDF 2 Section 6.8)."""
    region = config.settings.region
    peers = config.settings.peers
    if not peers:
        return {"unreplicated": 0, "critical_unreplicated": 0, "oldest_age_ms": 0}

    rows = conn.execute("SELECT seq FROM peer_view WHERE origin=?", (region,)).fetchall()
    max_peer_ack = max([r[0] for r in rows], default=0)

    unrep_rows = conn.execute(
        "SELECT seq, priority, hlc_l FROM events WHERE origin=? AND seq > ? ORDER BY seq ASC",
        (region, max_peer_ack),
    ).fetchall()

    unreplicated = len(unrep_rows)
    crit_unrep = sum(1 for r in unrep_rows if r[1] == 0)
    oldest_age_ms = 0
    if unreplicated > 0:
        now_ms = int(time.time() * 1000)
        oldest_age_ms = max(0, now_ms - unrep_rows[0][2])

    return {
        "unreplicated": unreplicated,
        "critical_unreplicated": crit_unrep,
        "oldest_age_ms": oldest_age_ms,
    }


def snapshot() -> dict:
    """Peers block + region level + risk + queues + carry for GET /v1/status."""
    global _current_level
    now = time.time()
    mine = heads()
    peers, level = {}, "HEALTHY"

    with store.read() as conn:
        has_isolated = False
        has_slow = False
        heads_differ = False

        for peer in config.settings.peers:
            st = conn.execute("SELECT last_ok, last_error, fails FROM peer_state WHERE peer=?", (peer,)).fetchone()
            last_ok = st["last_ok"] if st else None
            fails = st["fails"] if st else 0
            their = {r[0]: r[1] for r in conn.execute("SELECT origin, seq FROM peer_view WHERE peer=?", (peer,))}
            silent = last_ok is None or (now - last_ok) > ISOLATED_SILENCE_S
            isolated = fails >= ISOLATED_FAILS or silent
            l_mode = link_mode(peer)

            if isolated:
                has_isolated = True
            if l_mode == "slow":
                has_slow = True
            if any(their.get(o, 0) != mine.get(o, 0) for o in set(mine) | set(their)):
                heads_differ = True

            peers[peer] = {
                "link": l_mode,
                "reachable": not isolated,
                "last_ok_s": round(now - last_ok, 1) if last_ok else None,
                "last_ok_ms": int((now - last_ok) * 1000) if last_ok else None,
                "acked": their,
                "their_heads": their,
                "last_error": st["last_error"] if st else None,
                "fails": fails,
            }

        if not config.settings.peers:
            level = "HEALTHY"
        elif has_isolated:
            level = "ISOLATED"
        elif has_slow:
            level = "CONSTRAINED"
        elif heads_differ:
            level = "RECOVERING"
        else:
            level = "HEALTHY"

        # a node that is refusing writes (region limit or full write queue) says so, unless it is cut off
        if level != "ISOLATED":
            from region.sync import scale
            if scale.recently_overloaded():
                level = "OVERLOADED"

        _current_level = level
        risk = compute_risk(conn)

    # Get priority queue counts
    try:
        from region.sync import push
        queues = push.get_queue_counts()
    except Exception:
        queues = {"P0": 0, "P1": 0, "P2": 0}

    # Get carry metrics
    try:
        from region.sync import carry
        carry_stats = carry.get_carry_metrics()
    except Exception:
        carry_stats = {"received": 0, "new": 0, "dup": 0, "rejected": 0}

    return {
        "peers": peers,
        "level": level,
        "risk": risk,
        "queues": queues,
        "carry": carry_stats,
    }

