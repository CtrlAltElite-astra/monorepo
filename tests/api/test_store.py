"""Storage guarantees (owner: Akshit): WAL + synchronous=FULL, gap-free seq, rollback, region guard."""
import dataclasses
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from region import config, store
from region.api import writes

from conftest import create_value

TABLES = {"node_meta", "events", "reports", "origin_progress", "peer_view", "peer_state",
          "links", "quarantine", "idempotency"}


def test_wal_and_full_synchronous(region):
    with store.read() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL


def test_schema_has_every_table_and_init_is_repeatable(region):
    store.init()
    store.init()
    with store.read() as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert TABLES <= names
        assert conn.execute("SELECT v FROM node_meta WHERE k='region'").fetchone()[0] == "mum"


def test_a_database_cannot_be_reused_by_another_region(region, monkeypatch):
    store.close()
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, region="pun"))
    with pytest.raises(RuntimeError, match="belongs to region 'mum'"):
        store.init()


def test_sequence_is_gap_free_and_clock_monotonic_under_concurrent_writers(region):
    def one(i):
        return writes.local_write(None, "CREATE", None, create_value(f"r{i}"), "officer-mum-1").event["seq"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        seqs = list(pool.map(one, range(100)))
    assert sorted(seqs) == list(range(1, 101))  # no gaps, no duplicates
    with store.read() as conn:
        rows = conn.execute("SELECT seq, hlc_l, hlc_c FROM events ORDER BY seq").fetchall()
        assert store.own_head(conn) == 100
    clocks = [(r[1], r[2]) for r in rows]
    assert all(a < b for a, b in zip(clocks, clocks[1:]))  # strictly increasing with seq


def test_a_failed_transaction_rolls_back_and_does_not_burn_a_sequence_number(region):
    with pytest.raises(ValueError):
        with store.tx() as conn:
            assert store.next_seq(conn) == 1
            raise ValueError("boom")
    with store.tx() as conn:
        assert store.next_seq(conn) == 1  # still 1: the failed attempt left no gap


def test_insert_if_absent_distinguishes_new_dup_and_mismatch(region, mk_event):
    ev = mk_event("pun", 1, "CREATE", "R-pun-1", value=create_value())
    with store.tx() as conn:
        assert store.insert_event_if_absent(conn, ev) == "new"
        assert store.insert_event_if_absent(conn, ev) == "dup"
        forged = dict(ev, hash="0" * 64)
        assert store.insert_event_if_absent(conn, forged) == "mismatch"
        assert store.event_count(conn) == 1


def test_committed_data_survives_a_connection_loss_without_a_clean_close(region):
    """What a kill -9 leaves behind: the file is reopened with no graceful shutdown."""
    writes.local_write(None, "CREATE", None, create_value("durable"), "officer-mum-1")
    con = sqlite3.connect(region)  # a brand new connection, as after a crash
    try:
        assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    finally:
        con.close()
