# src/sincor2/daily_ops_scheduler.py
# (existing file - additive integration only)

import logging
import os

try:
    from sincor2.production_logger import get_logger
    logger = get_logger(__name__)
except ImportError:
    logger = logging.getLogger(__name__)

# ============================================
# POLYCLAW SELF-PERPETUATING EARNING MACHINE
# Hooked up and super optimized - July 2026
# ============================================

try:
    from sincor2.polyclaw_earning_scheduler import run_scheduled_cycle
    POLYCLAW_EARNING_ENABLED = True
except ImportError:
    logger.warning("Polyclaw earning scheduler not available yet")
    POLYCLAW_EARNING_ENABLED = False


def run_daily_ops():
    """Existing daily operations - now includes Polyclaw earning machine."""
    logger.info("Running daily operations...")

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


_scheduler = None


def start_daily_ops_scheduler(app=None):
    """Start the daily ops background scheduler."""
    global _scheduler

    if os.environ.get("DAILY_OPS_ENABLED", "true").lower() != "true":
        logger.info("[DAILY_OPS] Scheduler disabled (set DAILY_OPS_ENABLED=true to activate)")
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("[DAILY_OPS] APScheduler not installed — daily ops loop not started")
        return None

    try:
        hour = max(0, min(23, int(os.environ.get("DAILY_OPS_HOUR", "6"))))
        minute = max(0, min(59, int(os.environ.get("DAILY_OPS_MINUTE", "0"))))
    except (ValueError, TypeError):
        logger.warning("[DAILY_OPS] Invalid DAILY_OPS_HOUR/MINUTE — using defaults 06:00")
        hour, minute = 6, 0

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        run_daily_ops,
        CronTrigger(hour=hour, minute=minute),
        id="daily_ops",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("[DAILY_OPS] Daily ops scheduler started (%02d:%02d UTC)", hour, minute)
    return _scheduler


def stop_daily_ops_scheduler():
    """Stop the daily ops background scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None


if __name__ == "__main__":
    run_daily_ops()
