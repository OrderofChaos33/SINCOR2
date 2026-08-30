"""
SINCOR Outreach Scheduler
Starts APScheduler. First cycle ~45s after boot, then interval.
"""

import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger("sincor2.outreach_scheduler")

_scheduler = None


def start_outreach_scheduler(app=None):
    global _scheduler

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        from apscheduler.triggers.date import DateTrigger
    except ImportError:
        logger.warning("[SCHEDULER] APScheduler not installed")
        return None

    if _scheduler is not None and _scheduler.running:
        logger.info("[SCHEDULER] Scheduler already running")
        return _scheduler

    interval_hours = float(os.environ.get("OUTREACH_INTERVAL_HOURS", "2"))

    def _run_cycle():
        try:
            from sincor2.outreach_engine import get_outreach_engine
            engine = get_outreach_engine()
            result = engine.run_cycle()
            logger.info("[SCHEDULER] Outreach cycle result: %s", result)
        except Exception as e:
            logger.error("[SCHEDULER] Outreach cycle error: %s", e, exc_info=True)

    _scheduler = BackgroundScheduler(timezone="America/Chicago")
    _scheduler.add_job(
        _run_cycle,
        trigger=IntervalTrigger(hours=interval_hours),
        id="outreach_cycle",
        name="SINCOR Outreach Cycle",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        _run_cycle,
        trigger=DateTrigger(run_date=datetime.now() + timedelta(seconds=45)),
        id="outreach_startup",
        name="SINCOR Outreach Startup Cycle",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "[SCHEDULER] Outreach armed: first cycle in 45s, then every %sh",
        interval_hours,
    )
    return _scheduler


def stop_outreach_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[SCHEDULER] Outreach scheduler stopped")
