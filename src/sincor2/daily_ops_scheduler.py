"""
SINCOR Daily Ops Scheduler — read-only monitoring + SADAS alpha cycles on Railway.

Environment variables:
  DAILY_OPS_ENABLED          — set to "true"
  SADAS_ENABLED              — set to "true" to enable Alternative Derivative Alpha Swarm
  SADAS_INTERVAL_MINUTES     — default 15
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta
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
            include_buyers = datetime.now().weekday() == 6

        report = run_daily(include_buyers=include_buyers)
        alerts = report.get("wallet_watch", {}).get("alerts", [])
        if alerts:
            logger.warning("[DAILY_OPS] Wallet alerts: %s", alerts)
        if report.get("errors"):
            logger.warning("[DAILY_OPS] Partial errors: %s", report["errors"])
        else:
            logger.info("[DAILY_OPS] Daily ops complete")
    except Exception as e:
        logger.error("[DAILY_OPS] Cycle error: %s", e, exc_info=True)


def _run_sadas_cycle() -> None:
    """Run one SADAS alpha discovery cycle."""
    try:
        root = str(_PROJECT_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        from sincor2.sadas_orchestrator import run_sadas_scheduled_cycle

        anomalies = run_sadas_scheduled_cycle()
        if anomalies:
            logger.info("[SADAS] Discovered %d alpha opportunities", len(anomalies))
        else:
            logger.debug("[SADAS] Cycle complete - no actionable anomalies")
    except Exception as e:
        logger.error("[SADAS] Cycle error: %s", e, exc_info=True)


def start_daily_ops_scheduler(app=None):
    global _scheduler

    daily_enabled = os.environ.get("DAILY_OPS_ENABLED", "false").lower() == "true"
    sadas_enabled = os.environ.get("SADAS_ENABLED", "true").lower() == "true"

    if not daily_enabled and not sadas_enabled:
        logger.info("[SCHEDULER] Both Daily Ops and SADAS disabled")
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        from apscheduler.triggers.date import DateTrigger
    except ImportError:
        logger.warning("[SCHEDULER] APScheduler not installed")
        return None

    if _scheduler is not None and _scheduler.running:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="America/Chicago")

    # Daily Ops job
    if daily_enabled:
        interval_hours = float(os.environ.get("DAILY_OPS_INTERVAL_HOURS", "24"))
        _scheduler.add_job(
            _run_daily_ops,
            trigger=IntervalTrigger(hours=interval_hours),
            id="daily_ops",
            name="SINCOR Daily Ops",
            replace_existing=True,
            max_instances=1,
        )
        _scheduler.add_job(
            _run_daily_ops,
            trigger=DateTrigger(run_date=datetime.now() + timedelta(seconds=120)),
            id="daily_ops_startup",
            name="Daily Ops Startup Run",
        )
        logger.info("[DAILY_OPS] Scheduler started (every %sh)", interval_hours)

    # SADAS Alpha Swarm job
    if sadas_enabled:
        sadas_interval = int(os.environ.get("SADAS_INTERVAL_MINUTES", "15"))
        _scheduler.add_job(
            _run_sadas_cycle,
            trigger=IntervalTrigger(minutes=sadas_interval),
            id="sadas_alpha_swarm",
            name="SADAS Alternative Derivative Alpha Swarm",
            replace_existing=True,
            max_instances=1,
        )
        _scheduler.add_job(
            _run_sadas_cycle,
            trigger=DateTrigger(run_date=datetime.now() + timedelta(seconds=90)),
            id="sadas_startup",
            name="SADAS Startup Run",
        )
        logger.info("[SADAS] Scheduler started (every %s minutes)", sadas_interval)

    _scheduler.start()
    return _scheduler


def stop_daily_ops_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[SCHEDULER] Stopped")