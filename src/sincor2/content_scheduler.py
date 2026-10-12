"""
SINCOR Content job-function library.

One-scheduler consolidation (2026-10-11): this module no longer owns an
APScheduler instance. The central scheduler (sincor2.scheduler) registers the
``content_cycle`` job exactly once; the job function below is the callable it
can use. Scheduling (cadence, enablement) is defined once in
sincor2.scheduler.SCHEDULER_DEFAULTS.

Environment variables (consumed by the job function, not by scheduling):
  CONTENT_MODEL          — Claude model to use (default: claude-haiku-4-5)
  WP_API_URL             — WordPress REST API base URL (for auto-publish)
  WP_USERNAME            — WordPress username
  WP_APP_PASSWORD        — WordPress application password
"""

import logging
import os

logger = logging.getLogger("sincor2.content_scheduler")


def run_content_cycle():
    """One content-agent cycle (WP publish/drafts). Keep the try/except pattern."""
    try:
        from sincor2.content_agent import run_autonomous_cycle, init_db
        init_db()
        model = os.environ.get("CONTENT_MODEL", "claude-haiku-4-5")
        result = run_autonomous_cycle(model=model)
        logger.info(f"[CONTENT] Cycle complete — published {result['published']} posts")
    except Exception as e:
        logger.error(f"[CONTENT] Cycle error: {e}", exc_info=True)


# Backwards-compatible alias for the previous private name.
_run_cycle = run_content_cycle
