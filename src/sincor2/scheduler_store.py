"""Durable job-store factory for the central scheduler (one-scheduler, STREAM B).

SHADOW MODE: this changes nothing about scheduling behavior. When DATABASE_URL
is set and the database answers a probe, the central scheduler persists its job
definitions to Postgres via APScheduler's SQLAlchemyJobStore; in every other
case it silently and automatically falls back to the in-memory MemoryJobStore.
Boot NEVER fails because of a missing or unreachable database.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("sincor2.scheduler_store")


def _normalize_url(url: str) -> str:
    url = url.strip()
    # SQLAlchemy 2.x wants postgresql://, not the Heroku-style postgres://
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _database_reachable(url: str) -> bool:
    """True only if the DB answers a quick probe query.

    Short connect timeout so a configured-but-dead DB delays boot by seconds,
    not minutes, before we fall back to memory.
    """
    try:
        from sqlalchemy import create_engine, text

        kwargs: Dict[str, Any] = {"pool_pre_ping": True}
        if url.startswith("postgresql"):
            kwargs["connect_args"] = {"connect_timeout": 5}
        engine = create_engine(url, **kwargs)
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        finally:
            engine.dispose()
        return True
    except Exception as e:
        logger.warning(
            "[SCHEDULER][STORE] DATABASE_URL configured but unreachable "
            "(falling back to in-memory job store): %s",
            e,
        )
        return False


# Memoized probe results keyed by the raw env value: boot resolves the store,
# the ledger, and recovery through this, and we probe a dead DB only once.
_probe_cache: Dict[str, Optional[str]] = {}


def durable_db_url() -> Optional[str]:
    """Normalized DATABASE_URL if set AND the DB answers a probe, else None."""
    raw = os.environ.get("DATABASE_URL", "").strip()
    if not raw:
        return None
    if raw in _probe_cache:
        return _probe_cache[raw]
    url = _normalize_url(raw)
    result = url if _database_reachable(url) else None
    _probe_cache[raw] = result
    return result


def build_jobstore(db_url: Optional[str] = None) -> Tuple[Dict[str, Any], str]:
    """Build the APScheduler jobstores dict for the central scheduler.

    Returns (jobstores, backend_name) where backend_name is "sqlalchemy" when
    db_url points at a reachable database, else "memory". Passing db_url=None
    reads DATABASE_URL from the environment (with probe); pass an explicit
    URL to skip the probe.
    """
    if db_url is None:
        db_url = durable_db_url()
    if db_url:
        try:
            from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

            store = SQLAlchemyJobStore(url=db_url)
            logger.info("[SCHEDULER][STORE] Using durable SQLAlchemyJobStore")
            return {"default": store}, "sqlalchemy"
        except Exception as e:
            logger.warning(
                "[SCHEDULER][STORE] SQLAlchemyJobStore init failed "
                "(falling back to in-memory job store): %s",
                e,
            )
    from apscheduler.jobstores.memory import MemoryJobStore

    return {"default": MemoryJobStore()}, "memory"
