"""
SINCOR Launch Ops job-function library.

One-scheduler consolidation (2026-10-11): this module no longer owns an
APScheduler instance. The central scheduler (sincor2.scheduler) registers the
``launch_cycle`` job exactly once; the job function below is the callable it
can use. Scheduling (cadence, enablement) is defined once in
sincor2.scheduler.SCHEDULER_DEFAULTS.

Runs launch_content_engine cycle (drafts → human review queue).
Hook keeper + buy_watcher are Windows/local daemons — use scripts/install_launch_ops_schedule.ps1.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("sincor2.launch_ops_scheduler")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _run_content_cycle() -> None:
    try:
        root = str(_PROJECT_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        from launch_content_engine.review_queue import list_drafts
        from launch_content_engine.run_cycle import run_once

        pipelines = [
            "agent_spotlight",
            "onchain_stats",
            "build_log",
            "referral_cta",
            "seo_compare",
        ]
        ids: list[str] = []
        for pipe in pipelines:
            ids.extend(run_once(pipe))
        pending = len(list_drafts("pending"))
        logger.info(
            "[LAUNCH_OPS] Content cycle complete — %s draft(s), %s pending review",
            len(ids),
            pending,
        )
        # Default OFF — operator requested no draft reminder emails.
        if os.environ.get("LAUNCH_REVIEW_REMINDER_AFTER_CYCLE", "false").lower() == "true":
            from sincor2.launch_review_notify import send_launch_review_reminder

            send_launch_review_reminder()
    except Exception as e:
        logger.error("[LAUNCH_OPS] Content cycle error: %s", e, exc_info=True)
