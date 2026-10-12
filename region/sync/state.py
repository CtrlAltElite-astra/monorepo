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


def snapshot() -> dict:
    """Peers block + region level for GET /v1/status."""
    now = time.time()
    mine = heads()
    peers, level = {}, "HEALTHY"
    with store.read() as conn:
        for peer in config.settings.peers:
            st = conn.execute("SELECT last_ok, last_error, fails FROM peer_state WHERE peer=?", (peer,)).fetchone()
            last_ok = st["last_ok"] if st else None
            fails = st["fails"] if st else 0
            their = {r[0]: r[1] for r in conn.execute("SELECT origin, seq FROM peer_view WHERE peer=?", (peer,))}
            silent = last_ok is None or (now - last_ok) > ISOLATED_SILENCE_S
            isolated = fails >= ISOLATED_FAILS or silent
            peers[peer] = {
                "link": link_mode(peer),
                "reachable": not isolated,
                "last_ok_s": round(now - last_ok, 1) if last_ok else None,
                "their_heads": their,
                "last_error": st["last_error"] if st else None,
                "fails": fails,
            }
            if isolated:
                level = "ISOLATED"
            elif level != "ISOLATED" and any(their.get(o, 0) != mine.get(o, 0) for o in set(mine) | set(their)):
                level = "SYNCING"
    if not config.settings.peers:
        level = "HEALTHY"
    return {"peers": peers, "level": level}
