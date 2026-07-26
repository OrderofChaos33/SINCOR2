# src/sincor2/daily_ops_scheduler.py
# (existing file - additive integration only)

import logging

try:
    from src.sincor2.production_logger import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# ============================================
# POLYCLAW SELF-PERPETUATING EARNING MACHINE
# Hooked up and super optimized - July 2026
# ============================================

try:
    from src.sincor2.polyclaw_earning_scheduler import run_scheduled_cycle
    POLYCLAW_EARNING_ENABLED = True
except ImportError:
    logger.warning("Polyclaw earning scheduler not available yet")
    POLYCLAW_EARNING_ENABLED = False

_scheduler = None


def run_daily_ops():
    """Existing daily operations - now includes Polyclaw earning machine."""
    logger.info("Running daily operations...")

    # === Existing ops here (unchanged) ===
    # ... your previous daily tasks ...

    # === NEW: Polyclaw Self-Perpetuating Earning Machine ===
    if POLYCLAW_EARNING_ENABLED:
        try:
            logger.info("Triggering Polyclaw earning cycle (TOA + Renegade + self-funding)")
            result = run_scheduled_cycle()
            logger.info(f"Polyclaw cycle result: {result.get('status', 'unknown')}")
        except Exception as e:
            logger.exception(f"Polyclaw earning cycle failed: {e}")
    else:
        logger.info("Polyclaw earning machine not yet enabled")

    logger.info("Daily operations complete")


def start_daily_ops_scheduler(app=None):
    """Start the daily ops background scheduler.

    Returns a BackgroundScheduler instance (or None if APScheduler is absent).
    """
    global _scheduler

    if _scheduler is not None:
        return _scheduler

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("[DAILY_OPS] APScheduler not installed — daily ops disabled")
        return None

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        run_daily_ops,
        trigger=CronTrigger(hour=6, minute=0),  # 06:00 UTC daily
        id="daily_ops",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("[DAILY_OPS] Daily ops scheduler started (06:00 UTC)")
    return scheduler


def stop_daily_ops_scheduler():
    """Stop the daily ops scheduler if running."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[DAILY_OPS] Daily ops scheduler stopped")
    _scheduler = None


if __name__ == "__main__":
    run_daily_ops()
