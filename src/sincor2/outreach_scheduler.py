"""
SINCOR Outreach Scheduler
Starts APScheduler background job that runs the outreach engine periodically.

Called from mvp_app.py at startup (after app init, before first request).
"""

import logging
import os

logger = logging.getLogger('sincor2.outreach_scheduler')

_scheduler = None


def start_outreach_scheduler(app=None):
    """
    Start APScheduler in background mode.
    Runs outreach cycle every 6 hours by default.
    Override with OUTREACH_INTERVAL_HOURS env var.
    """
    global _scheduler

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:
        logger.warning('[SCHEDULER] APScheduler not installed — run: pip install apscheduler')
        return None

    if _scheduler is not None and _scheduler.running:
        logger.info('[SCHEDULER] Scheduler already running')
        return _scheduler

    interval_hours = float(os.environ.get('OUTREACH_INTERVAL_HOURS', '6'))

    def _run_cycle():
        try:
            from sincor2.outreach_engine import get_outreach_engine
            engine = get_outreach_engine()
            result = engine.run_cycle()
            logger.info(f'[SCHEDULER] Outreach cycle result: {result}')
        except Exception as e:
            logger.error(f'[SCHEDULER] Outreach cycle error: {e}', exc_info=True)

    _scheduler = BackgroundScheduler(timezone='America/Chicago')
    _scheduler.add_job(
        _run_cycle,
        trigger=IntervalTrigger(hours=interval_hours),
        id='outreach_cycle',
        name='SINCOR Outreach Cycle',
        replace_existing=True,
        max_instances=1,  # never double-run
    )
    _scheduler.start()

    logger.info(f'[SCHEDULER] Outreach scheduler started (every {interval_hours}h). '
                f'Set OUTREACH_ENABLED=true in Railway to activate emails.')

    return _scheduler


def stop_outreach_scheduler():
    """Gracefully stop the scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info('[SCHEDULER] Outreach scheduler stopped')
