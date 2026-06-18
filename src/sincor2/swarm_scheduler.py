"""APScheduler wrapper for operational swarm cycles."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("sincor2.swarm_scheduler")
_scheduler = None


def start_swarm_scheduler(app=None):
    global _scheduler
    if os.environ.get("SWARM_OPS_ENABLED", "true").lower() != "true":
        logger.info("[SWARM] Disabled — SWARM_OPS_ENABLED=false")
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        from datetime import datetime, timedelta
    except ImportError:
        logger.warning("[SWARM] APScheduler not installed")
        return None

    if _scheduler is not None and _scheduler.running:
        return _scheduler

    interval_hours = float(os.environ.get("SWARM_OPS_INTERVAL_HOURS", "6"))

    def _job():
        from sincor2.swarm_ops import run_swarm_cycle

        run_swarm_cycle()

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        _job,
        trigger=IntervalTrigger(hours=interval_hours),
        id="swarm_ops_cycle",
        replace_existing=True,
        max_instances=1,
    )
    if os.environ.get("SWARM_OPS_RUN_ON_START", "true").lower() == "true":
        _scheduler.add_job(
            _job,
            trigger="date",
            run_date=datetime.now() + timedelta(seconds=90),
            id="swarm_ops_boot",
            replace_existing=True,
        )
    _scheduler.start()
    logger.info("[SWARM] Scheduler started (every %sh)", interval_hours)
    return _scheduler


def stop_swarm_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None