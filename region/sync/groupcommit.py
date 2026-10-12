"""Group commit: several writes share one SQLite transaction (owner: Simrit). This is what "power" changes.

Why: every `store.tx()` ends in a COMMIT, and with `synchronous=FULL` each COMMIT waits for the disk. Under
concurrent load most of a write's time is that wait. If N writers that are already queued share one COMMIT, the
disk wait is paid once instead of N times.

How (a drop-in replacement for `store.tx`, same contract for the caller):
  * One writer at a time still runs inside the region lock, so sequence numbers stay gap-free and the single
    worker rule holds. Each write gets its own SAVEPOINT: a write that raises is undone on its own and does not
    disturb the others in the batch (the plain `tx` rolled the whole transaction back, which is the same thing
    when there is only one write).
  * After its write a thread looks at how many other threads are waiting to write. If nobody is waiting, or the
    batch is full (`batch` writes), it COMMITs. Otherwise it releases the lock and waits.
  * A write is acknowledged (the `with` block returns) only after the COMMIT that contains it. A waiting thread
    sleeps until the batch's commit has happened, and if that COMMIT failed it raises the same error.
  * With no concurrency nothing is waiting, so every write commits immediately: today's behaviour, byte for byte.
  * A waiting thread that is not woken within MAX_WAIT_S commits the batch itself, so a stalled writer can
    never hold an acknowledged-looking write back for long.

Limits (state them, do not hide them):
  * Reads on the same connection can see a batch's writes a few milliseconds before the COMMIT.
  * `batch` is a cap, not a delay: it never makes a lone write wait for company.
  * Nested `tx()` on one thread is refused (the plain `tx` could not nest either: BEGIN inside BEGIN fails).
"""
import threading
import time
from contextlib import contextmanager

from region import config, store

MAX_WAIT_S = 0.25


def batch_size() -> int:
    return config.POWER_BATCH.get(config.settings.power, config.POWER_BATCH["medium"])


class GroupCommit:
    def __init__(self):
        self._cv = threading.Condition(store._lock)   # the region write lock
        self._counter = threading.Lock()
        self._waiting = 0        # threads that want to write and do not hold the lock yet
        self._open = False       # a SQLite transaction is open
        self._count = 0          # writes in the open transaction
        self._epoch = 0          # number of COMMITs so far
        self._errors = {}        # epoch -> exception raised by that COMMIT
        self._owner = None
        self.commits = 0
        self.writes = 0

    # -- helpers; call with the lock held ---------------------------------------------------------------
    def _others_waiting(self) -> int:
        with self._counter:
            return self._waiting

    def _commit(self) -> None:
        conn = store._conn
        epoch = self._epoch
        try:
            conn.execute("COMMIT")
        except BaseException as exc:      # the batch did not become durable: every writer in it must hear that
            self._errors[epoch] = exc
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
        finally:
            self.commits += 1
            self._open = False
            self._count = 0
            self._epoch += 1
            self._errors.pop(epoch - 8, None)   # keep a short memory only
            self._cv.notify_all()

    # -- the drop-in -------------------------------------------------------------------------------------
    @contextmanager
    def tx(self):
        with self._counter:
            self._waiting += 1
        counted = True
        try:
            with self._cv:
                with self._counter:
                    self._waiting -= 1
                counted = False
                me = threading.get_ident()
                if self._owner == me:
                    raise RuntimeError("nested store.tx() is not supported")
                conn = store._conn
                if not self._open:
                    conn.execute("BEGIN IMMEDIATE")
                    self._open, self._count = True, 0
                conn.execute("SAVEPOINT gc")
                self._owner = me
                failed = None
                try:
                    yield conn
                except BaseException as exc:
                    failed = exc
                    conn.execute("ROLLBACK TO gc")
                finally:
                    conn.execute("RELEASE gc")
                    self._owner = None
                if failed is not None:
                    # nothing of mine to wait for; just make sure a batch that others wait on still gets its COMMIT
                    if self._open and (self._count == 0 or self._others_waiting() == 0):
                        self._commit() if self._count else self._end_empty(conn)
                    raise failed
                self._count += 1
                self.writes += 1
                epoch = self._epoch
                if self._others_waiting() == 0 or self._count >= batch_size():
                    self._commit()
                else:
                    deadline = time.monotonic() + MAX_WAIT_S
                    while self._epoch == epoch:
                        left = deadline - time.monotonic()
                        if left <= 0:
                            self._commit()      # nobody finished the batch in time: do it ourselves
                            break
                        self._cv.wait(left)
                err = self._errors.get(epoch)
                if err is not None:
                    raise err
        finally:
            if counted:
                with self._counter:
                    self._waiting -= 1

    def _end_empty(self, conn) -> None:
        conn.execute("COMMIT")
        self._open = False
        self._count = 0

    def stats(self) -> dict:
        return {"writes": self.writes, "commits": self.commits}


_gc = None
_original_tx = None


def install() -> GroupCommit:
    """Make every `store.tx()` in this process a group-commit transaction. Idempotent."""
    global _gc, _original_tx
    if _gc is None:
        _gc = GroupCommit()
        _original_tx = store.tx
        store.tx = _gc.tx
    return _gc


def uninstall() -> None:
    global _gc, _original_tx
    if _gc is not None:
        store.tx = _original_tx
        _gc, _original_tx = None, None


def installed() -> bool:
    return _gc is not None


def stats() -> dict:
    return _gc.stats() if _gc else {"writes": 0, "commits": 0}
