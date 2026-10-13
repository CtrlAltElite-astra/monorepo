"""Group commit (power), in process: batching really happens, acknowledgements wait for the COMMIT, one failed
write never takes its neighbours down, and with no concurrency nothing changes. (Owner: Simrit.)"""
import tempfile
import threading
from pathlib import Path

import pytest

from region import config, store
from region.sync import groupcommit


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        store.init(str(Path(tmp) / "gc.db"))
        with store.tx() as conn:   # the plain transaction: the scratch table exists before group commit is installed
            conn.execute("CREATE TABLE scratch (seq INTEGER PRIMARY KEY, who INTEGER)")
        before = config.settings
        was_installed = groupcommit.installed()   # region.main installs it when another test imported the app
        groupcommit.uninstall()                   # every test here starts from the plain transaction and its own counters
        yield
        groupcommit.uninstall()
        if was_installed:
            groupcommit.install()
        config.settings = before
        store.close()


def _insert(who: int, fail: bool = False) -> int:
    with store.tx() as conn:
        seq = store.next_seq(conn)
        conn.execute("INSERT INTO scratch (seq, who) VALUES (?, ?)", (seq, who))
        if fail:
            raise ValueError("this write is refused")
    return seq


def _run_threads(n: int, fail_every: int = 0):
    results, errors = [], []
    start = threading.Barrier(n)

    def work(i):
        start.wait()
        try:
            results.append(_insert(i, fail=bool(fail_every) and i % fail_every == 0))
        except ValueError:
            errors.append(i)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results, errors


def _rows():
    with store.read() as conn:
        return [r[0] for r in conn.execute("SELECT seq FROM scratch ORDER BY seq")]


def _set_power(p):
    config.update_runtime({"power": p})


def test_batching_happens_under_concurrency_and_nothing_is_lost(db):
    _set_power("high")
    gc = groupcommit.install()
    results, errors = _run_threads(64)
    assert not errors
    assert sorted(results) == list(range(1, 65))            # every write acknowledged, sequence gap-free
    assert _rows() == list(range(1, 65))                     # and every one is really in the database
    assert gc.writes == 64 and gc.commits < 64               # they shared transactions


def test_power_low_commits_every_write_on_its_own(db):
    _set_power("low")
    gc = groupcommit.install()
    results, _ = _run_threads(24)
    assert sorted(results) == list(range(1, 25))
    assert gc.commits == gc.writes == 24


def test_a_lone_write_is_never_held_back_waiting_for_company(db):
    _set_power("high")
    gc = groupcommit.install()
    for i in range(10):                                      # one after the other: nobody is ever waiting
        _insert(i)
    assert gc.commits == gc.writes == 10


def test_higher_power_means_fewer_commits_for_the_same_work(db):
    counts = {}
    for power in ("low", "medium", "high"):
        groupcommit.uninstall()
        _set_power(power)
        gc = groupcommit.install()
        _run_threads(48)
        counts[power] = gc.commits
    assert counts["low"] == 48
    assert counts["medium"] <= counts["low"]
    assert counts["high"] < counts["low"]


def test_a_failed_write_is_undone_alone_and_the_others_commit(db):
    _set_power("high")
    gc = groupcommit.install()
    results, errors = _run_threads(40, fail_every=5)
    assert sorted(errors) == [i for i in range(40) if i % 5 == 0]
    assert len(results) == 40 - len(errors)
    rows = _rows()
    assert sorted(results) == rows                          # acknowledged == stored: nothing extra, nothing missing
    assert len(rows) == len(set(rows))
    # the sequence stays gap-free: a failed write rolled its number back with it
    assert rows == list(range(1, len(rows) + 1))


def test_a_failed_lone_write_leaves_no_transaction_open(db):
    _set_power("high")
    groupcommit.install()
    with pytest.raises(ValueError):
        _insert(0, fail=True)
    assert _insert(1) == 1                                  # the next write works and reuses the number
    assert _rows() == [1]


def test_nested_transactions_are_refused_like_before(db):
    groupcommit.install()
    with pytest.raises(RuntimeError):
        with store.tx():
            with store.tx():
                pass
    assert _insert(1) == 1


def test_a_write_is_durable_when_it_is_acknowledged(db):
    _set_power("high")
    groupcommit.install()
    _run_threads(32)
    # a second connection (what a restarted process would see) must hold exactly what was acknowledged
    import sqlite3
    path = store._conn.execute("PRAGMA database_list").fetchone()[2]
    other = sqlite3.connect(path)
    try:
        assert [r[0] for r in other.execute("SELECT seq FROM scratch ORDER BY seq")] == list(range(1, 33))
    finally:
        other.close()


def test_uninstall_restores_the_plain_transaction(db):
    original = store.tx
    groupcommit.install()
    assert store.tx is not original and groupcommit.installed()
    groupcommit.uninstall()
    assert store.tx is original and not groupcommit.installed()
