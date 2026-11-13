"""Best-effort idempotency + intent ledger for Tier 2/3 scheduler jobs (STREAM C).

Idempotency
-----------
Before a Tier 2/3 job's effect function runs, the scheduling wrapper computes
an idempotency key = ``"<job_id>:<YYYYMMDDHH>"`` (hourly bucket of the
scheduled run window) and checks a store:

- If ``DATABASE_URL`` is set and reachable: a Postgres table
  ``scheduler_idempotency(run_key TEXT PRIMARY KEY, done_at TIMESTAMPTZ)``.
- Otherwise: a process-local in-memory dict.

Best effort, never crashes the scheduler: any DB error (missing table, no
network, bad DSN) falls back to the in-memory store, and any unexpected
exception inside this module is swallowed after logging. Keys are pruned
(keys older than 7 days in Postgres; a 10k-entry cap in memory).

Approval-mode intent records
----------------------------
When ``SCHEDULER_REQUIRE_APPROVAL=true``, Tier 2/3 jobs are draft-only: the
wrapper records an intent record (job_id, run_id, timestamp, would_have_done)
and returns without invoking the effect function. Records live in a
process-local list (Stream B owns the durable ledger — this is self-contained
by design) and are emitted as structured log lines so they survive restarts
in log aggregation.

NOTE on "pre-effect logic": Stream C wraps the scheduling layer ONLY and must
not touch send logic (outreach_engine.run_cycle internals, email senders,
polyclaw executors). True pre-effect/outbound separation requires hooks inside
the domain code and is out of scope — in approval mode the wrapper records
intent and does NOT invoke the job function at all.
"""
from __future__ import annotations

import logging
import os
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sincor2.scheduler")

_IN_MEMORY: "OrderedDict[str, float]" = OrderedDict()
_IN_MEMORY_CAP = 10_000

_INTENT_RECORDS: List[Dict[str, Any]] = []

_DB_TABLE = "scheduler_idempotency"
_KEY_RETENTION_DAYS = 7


def utcnow() -> datetime:
    """Current UTC time. Module-level so tests can monkeypatch the clock."""
    return datetime.now(timezone.utc)


def make_key(job_id: str, when: Optional[datetime] = None,
             bucket_minutes: int = 60) -> str:
    """Idempotency key: job_id + time bucket of the scheduled run window.

    Default is an hourly bucket (``bucket_minutes=60``): any duplicate firing
    of the same job within the same hour is skipped, while the next
    scheduled window gets a fresh key. Jobs that intentionally fire more
    often (e.g. a 2-minute loop) pass a smaller bucket via the scheduler
    spec so the intended cadence is not throttled — duplicate firings
    within the same bucket are still skipped.
    """
    when = when or utcnow()
    if bucket_minutes >= 60:
        return f"{job_id}:{when.strftime('%Y%m%d%H')}"
    bucket = (when.minute // bucket_minutes) * bucket_minutes
    return f"{job_id}:{when.strftime('%Y%m%d%H')}:{bucket:02d}"


# ---------------------------------------------------------------------------
# Store backend: Postgres if DATABASE_URL works, else in-memory.
# ---------------------------------------------------------------------------

def _db_conn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    try:
        import psycopg2  # type: ignore
    except ImportError:
        return None
    try:
        return psycopg2.connect(dsn, connect_timeout=5)
    except Exception:
        return None


def _db_check(key: str) -> Optional[bool]:
    """True/False from Postgres, None if the DB backend is unusable."""
    conn = _db_conn()
    if conn is None:
        return None
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f"SELECT 1 FROM {_DB_TABLE} WHERE run_key = %s", (key,))
            return cur.fetchone() is not None
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _db_mark(key: str) -> bool:
    """Record the key in Postgres (creating the table if needed)."""
    conn = _db_conn()
    if conn is None:
        return False
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {_DB_TABLE} "
                "(run_key TEXT PRIMARY KEY, done_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            cur.execute(
                f"INSERT INTO {_DB_TABLE} (run_key) VALUES (%s) ON CONFLICT DO NOTHING",
                (key,),
            )
            cur.execute(
                f"DELETE FROM {_DB_TABLE} WHERE done_at < now() - INTERVAL '%s days'",
                (_KEY_RETENTION_DAYS,),
            )
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _mem_check(key: str) -> bool:
    return key in _IN_MEMORY


def _mem_mark(key: str) -> None:
    _IN_MEMORY[key] = utcnow().timestamp()
    while len(_IN_MEMORY) > _IN_MEMORY_CAP:
        _IN_MEMORY.popitem(last=False)


# ---------------------------------------------------------------------------
# Public API — all functions are best-effort and never raise.
# ---------------------------------------------------------------------------

def already_done(key: str) -> bool:
    """True if this idempotency key was already recorded (idempotent skip)."""
    try:
        db_result = _db_check(key)
        if db_result is not None:
            return db_result
        return _mem_check(key)
    except Exception as e:  # never break the scheduler
        logger.warning("[IDEMPOTENCY] check failed for %s: %s", key, e)
        return False


def mark_done(key: str) -> None:
    """Record an idempotency key after a successful Tier 2/3 run."""
    try:
        if _db_mark(key):
            return
        _mem_mark(key)
    except Exception as e:  # never break the scheduler
        logger.warning("[IDEMPOTENCY] mark failed for %s: %s", key, e)


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Approval-mode intent records.
# ---------------------------------------------------------------------------

def record_intent(job_id: str, run_id: str, would_have_done: str) -> Dict[str, Any]:
    """Record a draft-only intent for a Tier 2/3 job held by approval mode."""
    record = {
        "job_id": job_id,
        "run_id": run_id,
        "timestamp": utcnow().isoformat(),
        "would_have_done": would_have_done,
    }
    _INTENT_RECORDS.append(record)
    logger.info(
        "[SCHEDULER] intent recorded: job=%s run=%s would_have_done=%s",
        job_id,
        run_id,
        would_have_done,
    )
    return record


def list_intent_records() -> List[Dict[str, Any]]:
    return list(_INTENT_RECORDS)


def clear_intent_records() -> None:
    _INTENT_RECORDS.clear()
