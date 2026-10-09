"""
SINCOR Outreach Scheduler
Starts APScheduler. First cycle ~45s after boot, then interval.

WP2 / D1: DEFAULT OFF. The scheduler only starts when outreach is
EXPLICITLY enabled via OUTREACH_ENABLED=true. Unset, empty, or any
other value → fail closed (no scheduler, no sends).

Rationale: the previous behavior defaulted autonomous operation ON when
AUTONOMOUS_AGENTS was unset, which violates the fail-closed principle
and owner decision D1 (outreach scaffold-ready but default off).
"""

import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger("sincor2.outreach_scheduler")

_scheduler = None


def is_outreach_explicitly_enabled() -> bool:
    """Return True ONLY when OUTREACH_ENABLED=true (explicit opt-in).

    Fail-closed: unset, empty, "false", or any other value → False.
    AUTONOMOUS_AGENTS is intentionally NOT consulted here; outreach
    requires its own explicit flag (defense in depth against the old
    default-true chain).
    """
    return os.environ.get("OUTREACH_ENABLED", "").strip().lower() == "true"


def start_outreach_scheduler(app=None):
    global _scheduler

    # WP2 / D1: fail closed unless explicitly enabled.
    if not is_outreach_explicitly_enabled():
        logger.info(
            "[SCHEDULER] Outreach disabled: OUTREACH_ENABLED is not 'true' "
            "(fail closed per D1)"
        )
        return None

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
