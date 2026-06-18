"""
SINCOR Daily Ops Scheduler — read-only monitoring on Railway.

Environment variables:
  DAILY_OPS_ENABLED          — set to "true" (default: false)
  DAILY_OPS_INTERVAL_HOURS   — default 24
  DAILY_OPS_BUYERS_WEEKLY    — if true, run buyer scan on Sundays (default: true)
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("sincor2.daily_ops_scheduler")

_scheduler = None
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _run_daily_ops() -> None:
    try:
        root = str(_PROJECT_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        from sincor2.daily_ops import run_daily

        include_buyers = False
        if os.environ.get("DAILY_OPS_BUYERS_WEEKLY", "true").lower() == "true":
            include_buyers = datetime.now().weekday() == 6  # Sunday

        report = run_daily(include_buyers=include_buyers)
        alerts = report.get("wallet_watch", {}).get("alerts", [])
        if alerts:
            logger.warning("[DAILY_OPS] Wallet alerts: %s", alerts)
        if report.get("errors"):
            logger.warning("[DAILY_OPS] Partial errors: %s", report["errors"])
        else:
            logger.info("[DAILY_OPS] Daily ops complete — logs/ops/daily_latest.json")
    except Exception as e:
        logger.error("[DAILY_OPS] Cycle error: %s", e, exc_info=True)


def start_daily_ops_scheduler(app=None):
    global _scheduler

    if os.environ.get("DAILY_OPS_ENABLED", "true").lower() != "true":
        logger.info("[DAILY_OPS] Disabled — DAILY_OPS_ENABLED=false")
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        from apscheduler.triggers.date import DateTrigger
        from datetime import timedelta
    except ImportError:
        logger.warning("[DAILY_OPS] APScheduler not installed")
        return None

    if _scheduler is not None and _scheduler.running:
        return _scheduler

    interval_hours = float(os.environ.get("DAILY_OPS_INTERVAL_HOURS", "24"))
    _scheduler = BackgroundScheduler(timezone="America/Chicago")
    _scheduler.add_job(
        _run_daily_ops,
        trigger=IntervalTrigger(hours=interval_hours),
        id="daily_ops",
        name="SINCOR Daily Ops (read-only)",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        _run_daily_ops,
        trigger=DateTrigger(run_date=datetime.now() + timedelta(seconds=120)),
        id="daily_ops_startup",
        name="SINCOR Daily Ops Startup Run",
    )
    _scheduler.start()
    logger.info("[DAILY_OPS] Scheduler started (every %sh)", interval_hours)
    return _scheduler


def stop_daily_ops_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[DAILY_OPS] Scheduler stopped")