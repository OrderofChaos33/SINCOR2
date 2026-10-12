"""
SINCOR Outreach job-function library.

One-scheduler consolidation (2026-10-11): this module no longer owns an
APScheduler instance. The central scheduler (sincor2.scheduler) registers the
``outreach_cycle`` job exactly once; the job function below is the callable
it uses. Scheduling (cadence, enablement) is defined once in
sincor2.scheduler.SCHEDULER_DEFAULTS.
"""

import logging

logger = logging.getLogger("sincor2.outreach_scheduler")


def run_outreach_cycle():
    """One outreach cycle. Keep the _safe_run try/except pattern."""
    try:
        from sincor2.outreach_engine import get_outreach_engine
        engine = get_outreach_engine()
        result = engine.run_cycle()
        logger.info("[SCHEDULER] Outreach cycle result: %s", result)
    except Exception as e:
        logger.error("[SCHEDULER] Outreach cycle error: %s", e, exc_info=True)


# Backwards-compatible alias for the previous private name.
_run_cycle = run_outreach_cycle
