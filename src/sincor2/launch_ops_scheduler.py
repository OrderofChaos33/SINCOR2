"""
SINCOR Launch Ops Scheduler
Runs launch_content_engine cycle on an interval (drafts → human review queue).

Hook keeper + buy_watcher are Windows/local daemons — use scripts/install_launch_ops_schedule.ps1.

Environment variables:
  LAUNCH_OPS_ENABLED         — set to "true" to activate (default: false)
  LAUNCH_OPS_INTERVAL_HOURS  — cycle interval (default: 24)
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("sincor2.launch_ops_scheduler")

_scheduler = None
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
            "campaign_kpi",
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
        if os.environ.get("LAUNCH_REVIEW_REMINDER_AFTER_CYCLE", "true").lower() == "true":
            from sincor2.launch_review_notify import send_launch_review_reminder

            send_launch_review_reminder()
    except Exception as e:
        logger.error("[LAUNCH_OPS] Content cycle error: %s", e, exc_info=True)


def _run_campaign_ops() -> None:
    try:
        from sincor2.launch_campaign import draft_campaign_kpi_post, run_campaign_ops

        result = run_campaign_ops()
        if os.environ.get("CAMPAIGN_KPI_DRAFT_ENABLED", "true").lower() == "true":
            title, body = draft_campaign_kpi_post()
            root = str(_PROJECT_ROOT)
            if root not in sys.path:
                sys.path.insert(0, root)
            from launch_content_engine.review_queue import enqueue

            enqueue("campaign_kpi", "twitter", body, title=title, meta={"auto": True})
        logger.info(
            "[LAUNCH_OPS] Campaign ops — new_milestones=%s tvl=$%s",
            result.get("new_milestones"),
            result.get("predeposit_usdc_total"),
        )
    except Exception as e:
        logger.error("[LAUNCH_OPS] Campaign ops error: %s", e, exc_info=True)


def start_launch_ops_scheduler(app=None):
    """Start APScheduler for launch content drafts (review queue, not auto-publish)."""
    global _scheduler

    if os.environ.get("LAUNCH_OPS_ENABLED", "true").lower() != "true":
        logger.info(
            "[LAUNCH_OPS] Disabled — LAUNCH_OPS_ENABLED=false"
        )
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        from apscheduler.triggers.date import DateTrigger
        from datetime import datetime, timedelta
    except ImportError:
        logger.warning("[LAUNCH_OPS] APScheduler not installed — pip install apscheduler")
        return None

    if _scheduler is not None and _scheduler.running:
        logger.info("[LAUNCH_OPS] Scheduler already running")
        return _scheduler

    interval_hours = float(os.environ.get("LAUNCH_OPS_INTERVAL_HOURS", "24"))

    _scheduler = BackgroundScheduler(timezone="America/Chicago")
    _scheduler.add_job(
        _run_content_cycle,
        trigger=IntervalTrigger(hours=interval_hours),
        id="launch_ops_content",
        name="SINCOR Launch Content Cycle",
        replace_existing=True,
        max_instances=1,
    )
    campaign_hours = float(os.environ.get("CAMPAIGN_OPS_INTERVAL_HOURS", "6"))
    _scheduler.add_job(
        _run_campaign_ops,
        trigger=IntervalTrigger(hours=campaign_hours),
        id="launch_ops_campaign",
        name="SINCOR Campaign KPI Ops",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        _run_content_cycle,
        trigger=DateTrigger(run_date=datetime.now() + timedelta(seconds=90)),
        id="launch_ops_startup",
        name="SINCOR Launch Content Startup Run",
    )
    _scheduler.start()
    logger.info(
        "[LAUNCH_OPS] Scheduler started (every %sh, startup run in 90s). "
        "Approve at /launch/review",
        interval_hours,
    )
    return _scheduler


def stop_launch_ops_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[LAUNCH_OPS] Scheduler stopped")