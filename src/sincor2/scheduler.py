"""
SINCOR2 Centralized Scheduler (Master Automation Orchestrator)

Single source of truth for all background/cron-style automations.

All jobs remain feature-flagged. Backward compatible shims provided for existing start_xxx_scheduler() calls.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("sincor2.scheduler")

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.date import DateTrigger
    HAS_APSCHEDULER = True
except ImportError:
    HAS_APSCHEDULER = False
    BackgroundScheduler = None  # type: ignore

_scheduler: Optional[BackgroundScheduler] = None
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _ensure_path() -> None:
    root = str(_PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _safe_run(name: str, func: Callable[[], Any]) -> None:
    """Run any job with standardized error handling and logging."""
    try:
        _ensure_path()
        result = func()
        if result is not None:
            logger.info("[SCHEDULER] %s completed: %s", name, result)
        else:
            logger.debug("[SCHEDULER] %s completed", name)
    except Exception as e:
        logger.error("[SCHEDULER] %s failed: %s", name, e, exc_info=True)


def _daily_ops_job() -> None:
    from sincor2.daily_ops import run_daily

    include_buyers = (
        os.environ.get("DAILY_OPS_BUYERS_WEEKLY", "true").lower() == "true"
        and datetime.now().weekday() == 6
    )
    report = run_daily(include_buyers=include_buyers)
    alerts = report.get("wallet_watch", {}).get("alerts", [])
    if alerts:
        logger.warning("[DAILY_OPS] Wallet alerts: %s", alerts)


def _sadas_job() -> None:
    from sincor2.sadas_orchestrator import run_sadas_scheduled_cycle

    anomalies = run_sadas_scheduled_cycle()
    if anomalies:
        logger.info("[SADAS] Discovered %d alpha opportunities", len(anomalies))


def _outreach_job() -> None:
    from sincor2.outreach_engine import get_outreach_engine

    engine = get_outreach_engine()
    result = engine.run_cycle()
    logger.info("[OUTREACH] Cycle result: %s", result)


def _launch_content_job() -> None:
    from launch_content_engine.run_cycle import run_once
    from launch_content_engine.review_queue import list_drafts

    pipelines = ["agent_spotlight", "onchain_stats", "build_log", "referral_cta", "seo_compare"]
    ids: List[str] = []
    for pipe in pipelines:
        ids.extend(run_once(pipe))
    pending = len(list_drafts("pending"))
    logger.info("[LAUNCH_OPS] Content cycle: %d new drafts, %d pending review", len(ids), pending)

    # Default OFF — operator requested no draft reminder emails.
    if os.environ.get("LAUNCH_REVIEW_REMINDER_AFTER_CYCLE", "false").lower() == "true":
        from sincor2.launch_review_notify import send_launch_review_reminder

        send_launch_review_reminder()


def _recursive_optimizer_job() -> None:
    from sincor2.recursive_optimizer import get_recursive_optimizer

    opt = get_recursive_optimizer()
    if opt.enabled:
        opt.run_optimization_cycle()
    else:
        logger.debug("[RECURSIVE_OPT] Skipped (disabled)")


def _subscription_check_job() -> None:
    logger.debug("[SUBSCRIPTION] Recurring check placeholder (extend as needed)")


def _register_jobs(sched: BackgroundScheduler) -> List[str]:
    """Register all sensible automations. Returns list of registered job IDs."""
    jobs_registered: List[str] = []

    if os.environ.get("DAILY_OPS_ENABLED", "false").lower() == "true":
        hours = float(os.environ.get("DAILY_OPS_INTERVAL_HOURS", "24"))
        sched.add_job(
            lambda: _safe_run("Daily Ops", _daily_ops_job),
            trigger=IntervalTrigger(hours=hours),
            id="daily_ops",
            name="SINCOR Daily Ops",
            replace_existing=True,
            max_instances=1,
        )
        sched.add_job(
            lambda: _safe_run("Daily Ops (startup)", _daily_ops_job),
            trigger=DateTrigger(run_date=datetime.now() + timedelta(seconds=120)),
            id="daily_ops_startup",
            replace_existing=True,
        )
        jobs_registered.append("daily_ops")
        logger.info("[SCHEDULER] Registered Daily Ops (every %sh)", hours)

    if os.environ.get("SADAS_ENABLED", "true").lower() == "true":
        minutes = int(os.environ.get("SADAS_INTERVAL_MINUTES", "15"))
        sched.add_job(
            lambda: _safe_run("SADAS", _sadas_job),
            trigger=IntervalTrigger(minutes=minutes),
            id="sadas_alpha_swarm",
            name="SADAS Alternative Derivative Alpha Swarm",
            replace_existing=True,
            max_instances=1,
        )
        sched.add_job(
            lambda: _safe_run("SADAS (startup)", _sadas_job),
            trigger=DateTrigger(run_date=datetime.now() + timedelta(seconds=90)),
            id="sadas_startup",
            replace_existing=True,
        )
        jobs_registered.append("sadas_alpha_swarm")
        logger.info("[SCHEDULER] Registered SADAS (every %s min)", minutes)

    if os.environ.get("OUTREACH_ENABLED", "false").lower() == "true":
        hours = float(os.environ.get("OUTREACH_INTERVAL_HOURS", "6"))
        sched.add_job(
            lambda: _safe_run("Outreach", _outreach_job),
            trigger=IntervalTrigger(hours=hours),
            id="outreach_cycle",
            name="SINCOR Outreach Cycle",
            replace_existing=True,
            max_instances=1,
        )
        jobs_registered.append("outreach_cycle")
        logger.info("[SCHEDULER] Registered Outreach (every %sh)", hours)

    if os.environ.get("LAUNCH_OPS_ENABLED", "false").lower() == "true":
        hours = float(os.environ.get("LAUNCH_OPS_INTERVAL_HOURS", "24"))
        sched.add_job(
            lambda: _safe_run("Launch Content", _launch_content_job),
            trigger=IntervalTrigger(hours=hours),
            id="launch_content_cycle",
            name="SINCOR Launch Content Review",
            replace_existing=True,
            max_instances=1,
        )
        sched.add_job(
            lambda: _safe_run("Launch Content (startup)", _launch_content_job),
            trigger=DateTrigger(run_date=datetime.now() + timedelta(seconds=90)),
            id="launch_content_startup",
            replace_existing=True,
        )
        jobs_registered.append("launch_content_cycle")
        logger.info("[SCHEDULER] Registered Launch Content (every %sh)", hours)

    if os.environ.get("RECURSIVE_OPTIMIZER_ENABLED", "false").lower() == "true":
        hours = float(os.environ.get("RECURSIVE_OPT_INTERVAL_HOURS", "6"))
        sched.add_job(
            lambda: _safe_run("Recursive Optimizer", _recursive_optimizer_job),
            trigger=IntervalTrigger(hours=hours),
            id="recursive_optimizer",
            name="SINCOR2 Recursive Self-Optimizer",
            replace_existing=True,
            max_instances=1,
        )
        jobs_registered.append("recursive_optimizer")
        logger.info("[SCHEDULER] Registered Recursive Optimizer (every %sh) — self-improvement active", hours)

    if os.environ.get("SUBSCRIPTION_CHECK_ENABLED", "false").lower() == "true":
        hours = float(os.environ.get("SUBSCRIPTION_CHECK_INTERVAL_HOURS", "12"))
        sched.add_job(
            lambda: _safe_run("Subscription Check", _subscription_check_job),
            trigger=IntervalTrigger(hours=hours),
            id="subscription_check",
            name="SINCOR Subscription Reconciliation",
            replace_existing=True,
            max_instances=1,
        )
        jobs_registered.append("subscription_check")
        logger.info("[SCHEDULER] Registered Subscription Check (every %sh)", hours)

    return jobs_registered


def start_central_scheduler(app: Any = None) -> Optional[BackgroundScheduler]:
    """Start the single master scheduler. Call this once from startup/app factory."""
    global _scheduler

    if not HAS_APSCHEDULER:
        logger.warning("[SCHEDULER] APScheduler not available — install it for production automations")
        return None

    if _scheduler is not None and _scheduler.running:
        logger.info("[SCHEDULER] Central scheduler already running")
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="America/Chicago")

    registered = _register_jobs(_scheduler)

    if not registered:
        logger.info("[SCHEDULER] No automations enabled (all feature flags off)")
        _scheduler = None
        return None

    _scheduler.start()
    logger.info("[SCHEDULER] Centralized SINCOR2 scheduler started with jobs: %s", registered)
    return _scheduler


def stop_central_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[SCHEDULER] Centralized scheduler stopped")
        _scheduler = None


def get_scheduler_status() -> Dict[str, Any]:
    """For monitoring dashboard and recursive_optimizer introspection."""
    if not _scheduler:
        return {"running": False, "jobs": []}
    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
        })
    return {
        "running": _scheduler.running,
        "job_count": len(jobs),
        "jobs": jobs,
        "timezone": str(_scheduler.timezone),
    }


def start_daily_ops_scheduler(app=None):
    logger.info("[SCHEDULER] start_daily_ops_scheduler() called — centralized scheduler manages this job")
    if _scheduler is None or not _scheduler.running:
        return start_central_scheduler(app)
    return _scheduler


def start_outreach_scheduler(app=None):
    logger.info("[SCHEDULER] start_outreach_scheduler() called — centralized scheduler manages this job")
    if _scheduler is None or not _scheduler.running:
        return start_central_scheduler(app)
    return _scheduler


def start_launch_ops_scheduler(app=None):
    logger.info("[SCHEDULER] start_launch_ops_scheduler() called — centralized scheduler manages this job")
    if _scheduler is None or not _scheduler.running:
        return start_central_scheduler(app)
    return _scheduler


def start_recursive_optimizer(app=None):
    logger.info("[SCHEDULER] start_recursive_optimizer() called")
    if _scheduler is None or not _scheduler.running:
        return start_central_scheduler(app)
    return _scheduler


def stop_daily_ops_scheduler():
    stop_central_scheduler()

def stop_outreach_scheduler():
    stop_central_scheduler()

def stop_launch_ops_scheduler():
    stop_central_scheduler()
