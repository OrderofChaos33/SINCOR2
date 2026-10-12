"""Run ledger for the central scheduler (one-scheduler, STREAM B).

SHADOW MODE: observational only. Every job run gets a begin/finish record:
job_id, run_id (uuid), started_at, finished_at, state, result_summary, error.

- Postgres when a reachable DATABASE_URL is configured: an additive
  ``scheduler_job_runs`` table created if-not-exists via SQLAlchemy
  (create_all(checkfirst=True) — additive only, never alters or drops).
- Otherwise an in-memory ring buffer of the last 500 runs, so nothing
  breaks without a DB.

Nothing here changes what jobs run or when. The ``timeout`` state exists
for the parallel timeouts/circuit-breaker stream; ``timeout_sec`` on
``wrap_with_ledger`` is reserved for that stream (None = no enforcement,
i.e. today's behavior).
"""
from __future__ import annotations

import collections
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("sincor2.scheduler_ledger")

RING_BUFFER_MAX = 500
RESULT_SUMMARY_MAX = 500
ERROR_MAX = 1000

STATE_STARTED = "started"
STATE_OK = "ok"
STATE_ERROR = "error"
STATE_TIMEOUT = "timeout"

_TABLE = "scheduler_job_runs"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _truncate(value: Optional[str], limit: int) -> Optional[str]:
    if value is None:
        return None
    value = str(value)
    return value if len(value) <= limit else value[: limit - 3] + "..."


class _JobTimeout(TimeoutError):
    """Raised when a ledger-wrapped job exceeds its timeout_sec budget."""


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class _MemoryLedger:
    """Thread-safe in-memory ring buffer (last RING_BUFFER_MAX runs)."""

    def __init__(self) -> None:
        self._runs: Dict[str, Dict[str, Any]] = {}
        self._order: collections.deque = collections.deque()
        self._lock = threading.Lock()

    def begin(self, job_id: str) -> str:
        run_id = uuid.uuid4().hex
        rec = {
            "run_id": run_id,
            "job_id": job_id,
            "started_at": _utcnow(),
            "finished_at": None,
            "state": STATE_STARTED,
            "result_summary": None,
            "error": None,
        }
        with self._lock:
            while len(self._order) >= RING_BUFFER_MAX:
                oldest = self._order.popleft()
                self._runs.pop(oldest, None)
            self._order.append(run_id)
            self._runs[run_id] = rec
        return run_id

    def finish(
        self,
        run_id: str,
        state: str,
        result_summary: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            rec = self._runs.get(run_id)
            if rec is None:
                return
            rec["finished_at"] = _utcnow()
            rec["state"] = state
            rec["result_summary"] = result_summary
            rec["error"] = error

    def interrupted(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                dict(rec)
                for rec in self._runs.values()
                if rec["state"] == STATE_STARTED
            ]

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            ids = list(self._order)[-limit:]
            return [dict(self._runs[i]) for i in ids if i in self._runs]

    def count(self) -> int:
        with self._lock:
            return len(self._runs)


class _PostgresLedger:
    """SQLAlchemy-Core backend. Table created if-not-exists (additive only)."""

    def __init__(self, url: str) -> None:
        from sqlalchemy import (
            Column,
            DateTime,
            Integer,
            MetaData,
            String,
            Table,
            Text,
            create_engine,
        )

        self._engine = create_engine(url, pool_pre_ping=True)
        metadata = MetaData()
        self._t = Table(
            _TABLE,
            metadata,
            # Integer (not BigInteger): SQLite only auto-assigns rowids for
            # INTEGER PRIMARY KEY; Postgres maps this to serial/integer fine.
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("run_id", String(36), nullable=False, unique=True),
            Column("job_id", String(128), nullable=False, index=True),
            Column("started_at", DateTime(timezone=True), nullable=False),
            Column("finished_at", DateTime(timezone=True), nullable=True),
            Column("state", String(16), nullable=False, default=STATE_STARTED, index=True),
            Column("result_summary", Text, nullable=True),
            Column("error", Text, nullable=True),
        )
        # Additive migration only: never alters or drops existing tables.
        metadata.create_all(self._engine, checkfirst=True)

    def begin(self, job_id: str) -> str:
        from sqlalchemy import insert

        run_id = uuid.uuid4().hex
        with self._engine.begin() as conn:
            conn.execute(
                insert(self._t).values(
                    run_id=run_id,
                    job_id=job_id,
                    started_at=_utcnow(),
                    state=STATE_STARTED,
                )
            )
        return run_id

    def finish(
        self,
        run_id: str,
        state: str,
        result_summary: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        from sqlalchemy import update

        with self._engine.begin() as conn:
            conn.execute(
                update(self._t)
                .where(self._t.c.run_id == run_id)
                .values(
                    finished_at=_utcnow(),
                    state=state,
                    result_summary=result_summary,
                    error=error,
                )
            )

    def interrupted(self) -> List[Dict[str, Any]]:
        from sqlalchemy import select

        with self._engine.connect() as conn:
            rows = conn.execute(
                select(self._t).where(self._t.c.state == STATE_STARTED).order_by(self._t.c.started_at)
            ).mappings()
            return [dict(r) for r in rows]

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        from sqlalchemy import desc, select

        with self._engine.connect() as conn:
            rows = conn.execute(
                select(self._t).order_by(desc(self._t.c.id)).limit(limit)
            ).mappings()
            return [dict(r) for r in rows]

    def count(self) -> int:
        from sqlalchemy import func, select

        with self._engine.connect() as conn:
            return int(conn.execute(select(func.count()).select_from(self._t)).scalar() or 0)


# ---------------------------------------------------------------------------
# Ledger facade + process-wide singleton
# ---------------------------------------------------------------------------

class SchedulerLedger:
    """Facade over the memory or Postgres backend. Never raises at boot."""

    def __init__(self, database_url: Optional[str] = None) -> None:
        self.database_url = database_url
        self.backend_name = "memory"
        if database_url:
            try:
                self._backend: Any = _PostgresLedger(database_url)
                self.backend_name = "sqlalchemy"
                logger.info("[SCHEDULER][LEDGER] Using durable SQLAlchemy run ledger")
            except Exception as e:
                logger.warning(
                    "[SCHEDULER][LEDGER] Postgres ledger unavailable "
                    "(falling back to in-memory ring buffer): %s",
                    e,
                )
                self._backend = _MemoryLedger()
        else:
            self._backend = _MemoryLedger()

    def begin_run(self, job_id: str) -> str:
        return self._backend.begin(job_id)

    def finish_run(
        self,
        run_id: str,
        state: str,
        result_summary: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        self._backend.finish(
            run_id,
            state,
            result_summary=_truncate(result_summary, RESULT_SUMMARY_MAX),
            error=_truncate(error, ERROR_MAX),
        )

    def interrupted_runs(self) -> List[Dict[str, Any]]:
        """Runs that started but never finished (i.e. process died mid-run)."""
        return self._backend.interrupted()

    def recent_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self._backend.recent(limit)

    def run_count(self) -> int:
        return self._backend.count()


_ledger: Optional[SchedulerLedger] = None
_ledger_lock = threading.Lock()


def get_ledger(database_url: Optional[str] = None) -> SchedulerLedger:
    """Process-wide ledger singleton.

    database_url=None reads DATABASE_URL from the environment (with a
    reachability probe); an explicit URL skips the probe. Any failure
    degrades to the in-memory backend — never raises.
    """
    global _ledger
    if database_url is None:
        from sincor2.scheduler_store import durable_db_url

        database_url = durable_db_url()
    with _ledger_lock:
        if _ledger is None or _ledger.database_url != database_url:
            _ledger = SchedulerLedger(database_url=database_url)
        return _ledger


def reset_ledger() -> None:
    """Drop the singleton (tests)."""
    global _ledger
    with _ledger_lock:
        _ledger = None


# ---------------------------------------------------------------------------
# Job-function wrapper
# ---------------------------------------------------------------------------

def _run_with_timeout(func: Callable[[], Any], timeout_sec: float) -> Any:
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="sched-ledger") as ex:
        future = ex.submit(func)
        try:
            return future.result(timeout=timeout_sec)
        except FuturesTimeoutError:
            raise _JobTimeout(f"job exceeded timeout_sec={timeout_sec}")


def wrap_with_ledger(
    job_id: str,
    func: Callable[[], Any],
    timeout_sec: Optional[float] = None,
) -> Callable[[], Any]:
    """Wrap a job callable so every run is recorded in the ledger.

    Keeps the existing _safe_run contract: exceptions propagate to the
    caller (scheduler.py's _safe_run catches and logs them). timeout_sec
    is reserved for the parallel timeouts/circuit-breaker stream;
    None means no timeout enforcement (today's behavior).
    """

    def _wrapped() -> Any:
        ledger = get_ledger()
        run_id = ledger.begin_run(job_id)
        try:
            result = _run_with_timeout(func, timeout_sec) if timeout_sec is not None else func()
        except _JobTimeout as e:
            ledger.finish_run(run_id, STATE_TIMEOUT, error=str(e))
            raise
        except Exception as e:
            ledger.finish_run(
                run_id, STATE_ERROR, error=f"{type(e).__name__}: {e}"
            )
            raise
        ledger.finish_run(run_id, STATE_OK, result_summary=None if result is None else str(result))
        return result

    _wrapped.__name__ = f"ledger_wrapped_{job_id}"
    _wrapped.__doc__ = f"Ledger-recorded wrapper for scheduler job {job_id!r}."
    return _wrapped
