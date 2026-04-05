"""
SINCOR Content Scheduler
Starts APScheduler background job that runs the content agent every 48h.

Called from mvp_app.py at startup — same pattern as outreach_scheduler.

Environment variables:
  CONTENT_AGENT_ENABLED  — set to "true" to activate (default: false)
  CONTENT_INTERVAL_HOURS — override cycle interval (default: 48)
  CONTENT_MODEL          — Claude model to use (default: claude-haiku-4-5)
  WP_API_URL             — WordPress REST API base URL (for auto-publish)
  WP_USERNAME            — WordPress username
  WP_APP_PASSWORD        — WordPress application password
"""

import logging
import os

logger = logging.getLogger("sincor2.content_scheduler")

_scheduler = None


def start_content_scheduler(app=None):
    """
    Start APScheduler background job for autonomous content publishing.
    Runs content agent cycle every 48h by default.
    """
    global _scheduler

    if os.environ.get("CONTENT_AGENT_ENABLED", "false").lower() != "true":
        logger.info("[CONTENT] Content agent disabled (set CONTENT_AGENT_ENABLED=true to activate)")
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:
        logger.warning("[CONTENT] APScheduler not installed — run: pip install apscheduler")
        return None

    if _scheduler is not None and _scheduler.running:
        logger.info("[CONTENT] Scheduler already running")
        return _scheduler

    interval_hours = float(os.environ.get("CONTENT_INTERVAL_HOURS", "48"))
    model = os.environ.get("CONTENT_MODEL", "claude-haiku-4-5")

    def _run_cycle():
        try:
            from sincor2.content_agent import run_autonomous_cycle, init_db
            init_db()
            result = run_autonomous_cycle(model=model)
            logger.info(f"[CONTENT] Cycle complete — published {result['published']} posts")
        except Exception as e:
            logger.error(f"[CONTENT] Cycle error: {e}", exc_info=True)

    _scheduler = BackgroundScheduler(timezone="America/Chicago")
    _scheduler.add_job(
        _run_cycle,
        trigger=IntervalTrigger(hours=interval_hours),
        id="content_cycle",
        name="SINCOR Content Agent Cycle",
        replace_existing=True,
        max_instances=1,  # never double-run a long generation job
    )

    # Also run once at startup (after 60s delay to let app settle)
    from apscheduler.triggers.date import DateTrigger
    from datetime import datetime, timedelta
    startup_run_at = datetime.now() + timedelta(seconds=60)
    _scheduler.add_job(
        _run_cycle,
        trigger=DateTrigger(run_date=startup_run_at),
        id="content_cycle_startup",
        name="SINCOR Content Agent Startup Run",
    )

    _scheduler.start()
    logger.info(
        f"[CONTENT] Content scheduler started (every {interval_hours}h, startup run in 60s). "
        f"Model: {model}. WordPress: {'configured' if os.environ.get('WP_API_URL') else 'not set — saving drafts'}."
    )
    return _scheduler


def stop_content_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[CONTENT] Content scheduler stopped")
