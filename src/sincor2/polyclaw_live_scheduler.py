"""Polyclaw live scheduler — runs the REAL trading loop in the web process.

Replaces ``polyclaw_scheduler`` (the legacy headline-regex scanner) as the
production job registered in ``mvp_app.py``. Every POLYCLAW_CYCLE_INTERVAL_SEC
(default 90s) it executes one full cycle of
``polyclaw_mega_aggressive_live.run_cycle()``:

    scan real Polymarket markets → forecast → Kelly-size → risk-gate →
    execute (dry-run unless POLYCLAW_LIVE=true) → resolve → shadow A/B score

Keeps the APScheduler job id ``polyclaw_scan`` so the existing
``/api/ops/schedulers`` status endpoint keeps working.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger("sincor.polyclaw.scheduler")

_scheduler: Optional[Any] = None


def start_polyclaw_live_scheduler(app: Optional[Any] = None) -> Optional[Any]:
    """Start the background scheduler. Returns the scheduler or None."""
    global _scheduler
    if os.getenv("POLYCLAW_ENABLED", "true").lower() != "true":
        logger.info("[POLYCLAW] disabled via POLYCLAW_ENABLED=false")
        return None
    if _scheduler is not None:
        return _scheduler

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning("[POLYCLAW] APScheduler not installed — live loop not started")
        return None

    from sincor2.polyclaw_mega_aggressive_live import run_cycle

    interval = int(os.getenv("POLYCLAW_CYCLE_INTERVAL_SEC", "90"))
    scheduler = BackgroundScheduler(daemon=True)

    def _job() -> None:
        try:
            result = run_cycle()
            if result.get("status") == "halted":
                logger.warning("[POLYCLAW] cycle halted: kill switch")
        except Exception:
            logger.exception("[POLYCLAW] cycle crashed")

    scheduler.add_job(
        _job,
        trigger="interval",
        seconds=interval,
        id="polyclaw_scan",
        max_instances=1,          # never overlap cycles
        coalesce=True,            # skip missed runs after downtime
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "[POLYCLAW] live scheduler started (every %ds, mode=%s)",
        interval,
        "LIVE" if os.getenv("POLYCLAW_LIVE", "false").lower() == "true" else "DRY-RUN",
    )
    return scheduler


def stop_polyclaw_live_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[POLYCLAW] live scheduler stopped")
    _scheduler = None
