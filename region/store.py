"""SQLite storage for one region (single owner: Akshit).

One connection, one process, one in-process lock. Everyone writes through
`tx()` (BEGIN IMMEDIATE) so seq/HLC allocation is gap-free. Run exactly one
Uvicorn worker per region.
"""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from region import config
from region.engine import canonical, derive as derive_mod, hlc

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
MIGRATION = MIGRATIONS_DIR / "001_init.sql"  # idempotent; also sets the per-connection PRAGMAs


def _ensure_op_id_column(conn: sqlite3.Connection) -> None:
    """Contract v1.1: events.op_id. SQLite has no ADD COLUMN IF NOT EXISTS, so it is guarded here; this also keeps a
    database that an earlier build already upgraded working (the column exists, the migration row does not)."""
    if "op_id" not in {r[1] for r in conn.execute("PRAGMA table_info(events)")}:
        conn.execute("ALTER TABLE events ADD COLUMN op_id TEXT")


def _migrate(conn: sqlite3.Connection) -> None:
    """001 is idempotent and runs every start (it carries the PRAGMAs). Every later file runs
    exactly once, inside one transaction together with its schema_migrations row, so a crash
    can never leave a half-applied ALTER TABLE behind."""
    conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    _ensure_op_id_column(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at REAL NOT NULL)")
    done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        if path == MIGRATION or path.name in done:
            continue
        conn.executescript(
            "BEGIN IMMEDIATE;\n" + path.read_text(encoding="utf-8")
            + f"\nINSERT INTO schema_migrations (name, applied_at) VALUES ('{path.name}', strftime('%s','now'));\nCOMMIT;"
        )


def init(db_path: str | None = None) -> None:
    """Open the DB (creating the file and schema if needed). Safe to call again."""
    global _conn
    path = db_path or config.settings.db_path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if _conn is not None:
        _conn.close()
    _conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    _conn.row_factory = sqlite3.Row
    _migrate(_conn)
    _conn.execute("INSERT OR IGNORE INTO node_meta (k, v) VALUES ('region', ?)", (config.settings.region,))
    stored = _conn.execute("SELECT v FROM node_meta WHERE k='region'").fetchone()[0]
    if stored != config.settings.region:
        raise RuntimeError(f"{path} belongs to region '{stored}', not '{config.settings.region}'")
    rebuild_stale_views()


def rebuild_stale_views() -> int:
    """Re-derive every stored view whose policy_version differs from the engine's.

    Views are a projection of the event log, so this is always safe. Without it a node that was
    upgraded would keep old-policy views for reports nobody touched, and nodes would disagree on
    state_hash until each report happened to be edited again.
    """
    n = 0
    with tx() as conn:
        rows = conn.execute("SELECT report_id, view FROM reports").fetchall()
        for rid, raw in rows:
            try:
                ver = json.loads(raw).get("policy_version")
            except ValueError:
                ver = None
            if ver != derive_mod.POLICY_VERSION:
                save_view(conn, rid, derive_mod.derive(events_for(conn, rid)))
                n += 1
    return n


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


@contextmanager
def tx():
    """Write transaction. Holds the region write lock; commit before returning."""
    with _lock:
        _conn.execute("BEGIN IMMEDIATE")
        try:
            yield _conn
        except BaseException:
            _conn.execute("ROLLBACK")
            raise
        else:
            _conn.execute("COMMIT")


@contextmanager
def read():
    with _lock:
        yield _conn


def now_ms() -> int:
    return int(time.time() * 1000)


# ---- node_meta: sequence and clock ---------------------------------------
def _meta_get(conn, k, default):
    row = conn.execute("SELECT v FROM node_meta WHERE k=?", (k,)).fetchone()
    return row[0] if row else default


def _meta_set(conn, k, v):
    conn.execute("INSERT INTO node_meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


def next_seq(conn) -> int:
    """Allocate the next gap-free sequence number inside the current tx."""
    seq = int(_meta_get(conn, "next_seq", 1))
    _meta_set(conn, "next_seq", seq + 1)
    return seq


def own_head(conn) -> int:
    return int(_meta_get(conn, "next_seq", 1)) - 1


def load_hlc(conn) -> tuple:
    return (int(_meta_get(conn, "hlc_l", 0)), int(_meta_get(conn, "hlc_c", 0)))


def save_hlc(conn, h) -> None:
    _meta_set(conn, "hlc_l", h[0])
    _meta_set(conn, "hlc_c", h[1])


def hlc_receive(conn, remote) -> None:
    save_hlc(conn, hlc.receive(load_hlc(conn), tuple(remote), now_ms()))


# ---- events --------------------------------------------------------------
def _row_params(ev: dict):
    return (
        ev["event_id"], ev["origin"], ev["seq"], ev["report_id"], ev["type"], ev.get("field"),
        ev.get("priority", 1), ev["hlc"][0], ev["hlc"][1], ev["hash"], canonical.dumps(ev), time.time(),
        ev.get("op_id"),
    )


_INSERT = ("INSERT INTO events (event_id, origin, seq, report_id, type, field, priority, hlc_l, hlc_c, "
           "hash, body, received_at, op_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)")


def insert_event(conn, ev: dict) -> None:
    """Own events only. A duplicate id here is a bug (raises IntegrityError)."""
    conn.execute(_INSERT, _row_params(ev))


def insert_event_if_absent(conn, ev: dict) -> str:
    """Returns 'new' | 'dup' | 'mismatch' (same id, different content)."""
    row = conn.execute("SELECT hash FROM events WHERE event_id=?", (ev["event_id"],)).fetchone()
    if row is not None:
        return "dup" if row[0] == ev["hash"] else "mismatch"
    conn.execute(_INSERT, _row_params(ev))
    return "new"


def event_by_id(conn, event_id: str):
    row = conn.execute("SELECT body FROM events WHERE event_id=?", (event_id,)).fetchone()
    return json.loads(row[0]) if row else None


def missing_ids(conn, ids: list) -> list:
    """Which of these event ids this node does NOT hold (order preserved)."""
    have = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" for _ in chunk)
        have.update(r[0] for r in conn.execute(f"SELECT event_id FROM events WHERE event_id IN ({q})", chunk))
    return [i for i in ids if i not in have]


def events_for(conn, report_id: str) -> list:
    rows = conn.execute("SELECT body FROM events WHERE report_id=?", (report_id,)).fetchall()
    return [json.loads(r[0]) for r in rows]


def event_by_op(conn, op_id: str):
    """The first event (lowest HLC order) that carries this client op_id, or None.

    It may have been written here, or have arrived by replication or carry."""
    rows = conn.execute("SELECT body FROM events WHERE op_id=? ORDER BY hlc_l, hlc_c, origin, seq LIMIT 1", (op_id,)).fetchall()
    return json.loads(rows[0][0]) if rows else None


def mark_carried(conn, event_ids) -> None:
    """Remember that these events reached this node through POST /v1/carry (history shows it)."""
    now = time.time()
    conn.executemany("INSERT OR IGNORE INTO carried_events (event_id, at) VALUES (?, ?)", [(i, now) for i in event_ids])


def carried_ids(conn, event_ids) -> set:
    ids = list(event_ids)
    if not ids:
        return set()
    marks = ",".join("?" for _ in ids)
    return {r[0] for r in conn.execute(f"SELECT event_id FROM carried_events WHERE event_id IN ({marks})", ids)}


def carried_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM carried_events").fetchone()[0]


def events_after(conn, origin: str, after: int, limit: int) -> list:
    rows = conn.execute(
        "SELECT body FROM events WHERE origin=? AND seq>? ORDER BY seq LIMIT ?", (origin, after, limit)
    ).fetchall()
    return [json.loads(r[0]) for r in rows]


def event_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]


def event_pairs(conn) -> list:
    return [(r[0], r[1]) for r in conn.execute("SELECT event_id, hash FROM events")]


def report_exists(conn, report_id: str) -> bool:
    return conn.execute("SELECT 1 FROM events WHERE report_id=? LIMIT 1", (report_id,)).fetchone() is not None


def live_ids(conn, report_id: str, field: str) -> list:
    """Event ids currently live for a field, computed from the full event set."""
    return derive_mod.derive(events_for(conn, report_id))["live"].get(field, [])


# ---- views ---------------------------------------------------------------
def save_view(conn, report_id: str, view: dict) -> None:
    conn.execute(
        "INSERT INTO reports (report_id, view, n_conflicts, needs_review, incomplete, updated_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(report_id) DO UPDATE SET view=excluded.view, "
        "n_conflicts=excluded.n_conflicts, needs_review=excluded.needs_review, "
        "incomplete=excluded.incomplete, updated_at=excluded.updated_at",
        (report_id, canonical.dumps(view), len(view["conflicts"]),
         int(any(c["needs_review"] for c in view["conflicts"])), int(view["incomplete"]), time.time()),
    )


def get_view(conn, report_id: str):
    row = conn.execute("SELECT view FROM reports WHERE report_id=?", (report_id,)).fetchone()
    return json.loads(row[0]) if row else None


def all_views(conn, include_incomplete: bool = False) -> dict:
    q = "SELECT report_id, view FROM reports" + ("" if include_incomplete else " WHERE incomplete=0")
    return {r[0]: json.loads(r[1]) for r in conn.execute(q)}


def report_ids_newest_first(conn, limit: int, offset: int) -> list:
    """Complete reports, newest CREATE first by (hlc, origin, seq).

    The CREATE event is identical on every region, so every region returns the
    same order for the same event set (a plain per-origin seq would not be).
    """
    rows = conn.execute(
        "SELECT e.report_id FROM events e JOIN reports r ON r.report_id = e.report_id "
        "WHERE e.type='CREATE' AND r.incomplete=0 "
        "ORDER BY e.hlc_l DESC, e.hlc_c DESC, e.origin DESC, e.seq DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [r[0] for r in rows]


def report_origin_max(conn, report_id: str) -> dict:
    rows = conn.execute("SELECT origin, MAX(seq) FROM events WHERE report_id=? GROUP BY origin", (report_id,))
    return {r[0]: r[1] for r in rows}


# ---- quarantine ----------------------------------------------------------
def quarantine(conn, ev, source: str, reason: str) -> None:
    eid = ev.get("event_id") if isinstance(ev, dict) else None
    conn.execute("INSERT INTO quarantine (event_id, source, reason, body, at) VALUES (?,?,?,?,?)",
                 (eid, source, reason, json.dumps(ev, default=str), time.time()))


def quarantine_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0]
