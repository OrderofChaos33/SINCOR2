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


if __name__ == "__main__":
    run_daily_ops()
