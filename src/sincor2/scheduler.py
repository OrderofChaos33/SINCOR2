"""SINCOR2 Central Scheduler — the single owner of all in-app APScheduler jobs.

Consolidated (2026-10-11, one-scheduler): this module is the ONLY place that
creates an APScheduler instance. The 8 standalone ``*_scheduler.py`` modules are
job-function libraries now — they no longer create schedulers of their own.

Canonical defaults live in SCHEDULER_DEFAULTS below, defined once. They equal
the previously effective production behavior, with one deliberate fix:
outreach is registered exactly ONCE (the old setup ran it 2x per 2h).

STREAM C (2026-10-12): effect-tier classification lives in
``sincor2.scheduler_tiers``; Tier 2/3 jobs are wrapped at the scheduling layer
only (kill-switch SCHEDULER_EFFECTS_PAUSED, draft mode
SCHEDULER_REQUIRE_APPROVAL, hourly idempotency keys via
``sincor2.scheduler_idempotency``). Send logic itself is never touched.
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
    from apscheduler.triggers.cron import CronTrigger
    HAS_APSCHEDULER = True
except ImportError:
    HAS_APSCHEDULER = False
    BackgroundScheduler = None  # type: ignore

_scheduler: Optional[BackgroundScheduler] = None
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Canonical scheduler defaults — defined once.
#
# These equal the previously EFFECTIVE production behavior, with one deliberate
# fix: outreach runs exactly 1x per 2h (the old setup double-fired outreach).
# Do not change a default here without a founder decision — these gate live
# outbound (outreach/subscription emails), trading (polyclaw), and audits.
# ---------------------------------------------------------------------------
SCHEDULER_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "daily_ops": {
        "name": "SINCOR Daily Ops",
        "env_enabled": "DAILY_OPS_ENABLED",
        "enabled": True,
        "kind": "interval",
        "env_interval_hours": "DAILY_OPS_INTERVAL_HOURS",
        "interval_hours": 24,
        "startup_delay_sec": 120,
        "max_instances": 1,
    },
    "sadas_scan": {
        "name": "SADAS",
        "env_enabled": "SADAS_ENABLED",
        "enabled": True,
        "kind": "interval",
        "env_interval_minutes": "SADAS_INTERVAL_MINUTES",
        "interval_minutes": 15,
        "startup_delay_sec": 90,
        "max_instances": 1,
    },
    "outreach_cycle": {
        "name": "SINCOR Outreach Cycle",
        "env_enabled": "OUTREACH_ENABLED",
        "enabled": True,
        # Canonical: ON, exactly 1x per 2h. This entry is the single owner of
        # outreach — it replaces the old outreach_scheduler.py standalone
        # scheduler, which double-fired the same cycle.
        "kind": "interval",
        "env_interval_hours": "OUTREACH_INTERVAL_HOURS",
        "interval_hours": 2,
        "startup_delay_sec": 45,
        "max_instances": 1,
    },
    "content_cycle": {
        "name": "SINCOR Content Agent",
        "env_enabled": "CONTENT_AGENT_ENABLED",
        "enabled": True,
        "kind": "interval",
        "env_interval_hours": "CONTENT_AGENT_INTERVAL_HOURS",
        "interval_hours": 48,
        "max_instances": 1,
    },
    "launch_cycle": {
        "name": "SINCOR Launch Content",
        "env_enabled": "LAUNCH_OPS_ENABLED",
        "enabled": True,
        "kind": "interval",
        "env_interval_hours": "LAUNCH_OPS_INTERVAL_HOURS",
        "interval_hours": 24,
        "max_instances": 1,
    },
    "recursive_optimizer": {
        "name": "SINCOR2 Recursive Self-Optimizer",
        "env_enabled": "RECURSIVE_OPTIMIZER_ENABLED",
        "enabled": True,
        # ON-but-gated: the job is registered and fires, but is a no-op
        # unless the optimizer itself is enabled (see _recursive_optimizer_job).
        "kind": "interval",
        "env_interval_hours": "RECURSIVE_OPT_INTERVAL_HOURS",
        "interval_hours": 6,
        "max_instances": 1,
    },
    "subscription_reminders": {
        "name": "SINCOR Subscription Renewal Reminders",
        "env_enabled": "SUBSCRIPTION_REMINDER_ENABLED",
        "enabled": True,
        "kind": "cron",
        "env_hour": "SUBSCRIPTION_REMINDER_HOUR",
        "env_minute": "SUBSCRIPTION_REMINDER_MINUTE",
        "hour": 10,
        "minute": 0,
        "timezone": "UTC",
        "max_instances": 1,
    },
    "polyclaw_scan": {
        "name": "Polyclaw Trading Scan",
        "env_enabled": "POLYCLAW_ENABLED",
        "enabled": True,
        # Dry-run posture by default: POLYCLAW_LIVE stays default false;
        # private-key handling is untouched by this consolidation.
        "kind": "interval",
        "env_interval_seconds": "POLYCLAW_CYCLE_INTERVAL_SEC",
        "interval_seconds": 120,
        "coalesce": True,
        "max_instances": 1,
    },
    "compliance_checks": {
        "name": "SINCOR Compliance Monitor",
        "env_enabled": "COMPLIANCE_MONITOR_ENABLED",
        "enabled": False,
        "kind": "interval",
        "interval_minutes": 30,
        "misfire_grace_time": 300,
        "max_instances": 1,
    },
    "launch_review_notify": {
        "name": "SINCOR Launch Review Daily Reminder",
        "env_enabled": "LAUNCH_REVIEW_REMINDER_ENABLED",
        "enabled": False,
        # Additionally hard-disabled at module level in launch_review_notify.py
        # (_HARD_DISABLE_REMINDER_EMAILS) per operator request.
        "kind": "cron",
        "env_hour": "LAUNCH_REVIEW_REMINDER_HOUR",
        "env_minute": "LAUNCH_REVIEW_REMINDER_MINUTE",
        "hour": 9,
        "minute": 15,
        "timezone_env": "LAUNCH_OPS_TIMEZONE",
        "timezone": "America/Chicago",
        "max_instances": 1,
    },
    "partner_notify": {
        "name": "SINCOR Partner Outreach Reminder",
        "env_enabled": "PARTNER_OUTREACH_ENABLED",
        "enabled": False,
        "kind": "cron",
        "env_hour": "PARTNER_OUTREACH_REMINDER_HOUR",
        "env_minute": "PARTNER_OUTREACH_REMINDER_MINUTE",
        "hour": 8,
        "minute": 30,
        "timezone": "America/Chicago",
        "max_instances": 1,
    },
}

CANONICAL_JOB_IDS: List[str] = list(SCHEDULER_DEFAULTS.keys())


def _ensure_path() -> None:
    root = str(_PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _safe_run(name: str, func: Callable[[], Any]) -> None:
    try:
        _ensure_path()
        result = func()
        if result is not None:
            logger.info("[SCHEDULER] %s completed: %s", name, result)
    except Exception as e:
        logger.error("[SCHEDULER] %s failed: %s", name, e, exc_info=True)


def _is_enabled(job_id: str) -> bool:
    spec = SCHEDULER_DEFAULTS[job_id]
    default = "true" if spec["enabled"] else "false"
    return os.environ.get(spec["env_enabled"], default).lower() == "true"


# ---------------------------------------------------------------------------
# Tier 2/3 scheduling-layer guard (STREAM C).
#
# Wraps ONLY the scheduling layer: kill-switch, require_approval draft mode,
# and idempotency check-before-act. The underlying send logic
# (outreach_engine.run_cycle, email senders, polyclaw executors) is untouched
# — the wrapper either invokes the job function unchanged or skips it
# entirely. Tier 0/1 jobs are returned unwrapped.
# ---------------------------------------------------------------------------

def _effect_guard(job_id: str, func: Callable[[], None]) -> Callable[[], None]:
    from sincor2 import scheduler_idempotency as idem
    from sincor2 import scheduler_tiers

    tier = scheduler_tiers.job_tier(job_id)
    if tier < scheduler_tiers.TIER_OUTBOUND:
        return func  # Tier 0/1: no guard needed

    tier_label = scheduler_tiers.tier_name(tier)

    def guarded() -> None:
        run_id = idem.new_run_id()
        now = idem.utcnow()

        if scheduler_tiers.effects_paused():
            logger.warning(
                "[SCHEDULER] %s (tier %s) skipped: SCHEDULER_EFFECTS_PAUSED=true",
                job_id,
                tier_label,
            )
            return

        if scheduler_tiers.require_approval():
            idem.record_intent(
                job_id,
                run_id,
                f"{SCHEDULER_DEFAULTS[job_id]['name']} cycle invoked "
                f"{func.__name__}() — outbound effects withheld pending approval",
            )
            logger.info(
                "[SCHEDULER] %s (tier %s) draft-only: SCHEDULER_REQUIRE_APPROVAL=true, "
                "intent recorded, effect function not invoked",
                job_id,
                tier_label,
            )
            return

        key = idem.make_key(job_id, now)
        if idem.already_done(key):
            logger.info("[SCHEDULER] %s idempotent skip (key %s)", job_id, key)
            return

        func()
        idem.mark_done(key)

    guarded.__name__ = f"guarded_{func.__name__}"
    return guarded


def _wrap_job(job_id: str, func: Callable[[], None]) -> Callable[[], None]:
    """Apply the Stream C scheduling-layer guard (Tier 2/3 only)."""
    return _effect_guard(job_id, func)


# ---------------------------------------------------------------------------
# Job functions (scheduling-owned wrappers). Domain logic lives in the
# per-domain modules; imports stay lazy so registration never pulls heavy
# deps at boot.
# ---------------------------------------------------------------------------

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


def _content_agent_job() -> None:
    from sincor2.content_agent import run_autonomous_cycle
    run_autonomous_cycle()


def _launch_content_job() -> None:
    from launch_content_engine.run_cycle import run_once
    from launch_content_engine.review_queue import list_drafts
    pipelines = ["agent_spotlight", "onchain_stats", "build_log", "referral_cta", "seo_compare"]
    ids: List[str] = []
    for pipe in pipelines:
        ids.extend(run_once(pipe))
    pending = len(list_drafts("pending"))
    logger.info("[LAUNCH_OPS] Content cycle: %d new drafts, %d pending review", len(ids), pending)
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


def _subscription_reminders_job() -> None:
    from sincor2.subscription_scheduler import _send_renewal_reminders
    _send_renewal_reminders()


def _polyclaw_scan_job() -> None:
    from sincor2.polyclaw_scheduler import run_polyclaw_cycle
    run_polyclaw_cycle()


def _compliance_checks_job() -> None:
    from sincor2.compliance_monitor import run_all_checks
    run_all_checks()


def _launch_review_notify_job() -> None:
    from sincor2.launch_review_notify import send_launch_review_reminder
    send_launch_review_reminder()


def _partner_notify_job() -> None:
    from sincor2.partner_outreach_notify import send_partner_outreach_reminder
    send_partner_outreach_reminder()


_JOB_FUNCS: Dict[str, Callable[[], None]] = {
    "daily_ops": _daily_ops_job,
    "sadas_scan": _sadas_job,
    "outreach_cycle": _outreach_job,
    "content_cycle": _content_agent_job,
    "launch_cycle": _launch_content_job,
    "recursive_optimizer": _recursive_optimizer_job,
    "subscription_reminders": _subscription_reminders_job,
    "polyclaw_scan": _polyclaw_scan_job,
    "compliance_checks": _compliance_checks_job,
    "launch_review_notify": _launch_review_notify_job,
    "partner_notify": _partner_notify_job,
}


def _add_job(
    sched: BackgroundScheduler,
    job_id: str,
    registered: List[str],
) -> None:
    """Register one canonical job (interval or cron) from SCHEDULER_DEFAULTS."""
    spec = SCHEDULER_DEFAULTS[job_id]
    func = _wrap_job(job_id, _JOB_FUNCS[job_id])
    name = spec["name"]
    kwargs: Dict[str, Any] = {
        "id": job_id,
        "name": name,
        "replace_existing": True,
        "max_instances": spec.get("max_instances", 1),
    }
    if spec["kind"] == "interval":
        if "interval_hours" in spec:
            hours = float(os.environ.get(spec["env_interval_hours"], str(spec["interval_hours"])))
            trigger = IntervalTrigger(hours=hours)
        elif "interval_minutes" in spec:
            env_key = spec.get("env_interval_minutes")
            raw = os.environ.get(env_key, str(spec["interval_minutes"])) if env_key else str(spec["interval_minutes"])
            trigger = IntervalTrigger(minutes=int(raw))
        else:
            seconds = int(os.environ.get(spec["env_interval_seconds"], str(spec["interval_seconds"])))
            trigger = IntervalTrigger(seconds=seconds)
        if spec.get("coalesce"):
            kwargs["coalesce"] = True
        if spec.get("misfire_grace_time"):
            kwargs["misfire_grace_time"] = spec["misfire_grace_time"]
    else:  # cron
        hour = int(os.environ.get(spec["env_hour"], str(spec["hour"])))
        minute = int(os.environ.get(spec["env_minute"], str(spec["minute"])))
        tz = os.environ.get(spec.get("timezone_env", ""), "") or spec["timezone"]
        trigger = CronTrigger(hour=hour, minute=minute, timezone=tz)
    sched.add_job(lambda: _safe_run(name, func), trigger=trigger, **kwargs)

    startup_delay = spec.get("startup_delay_sec")
    if startup_delay:
        sched.add_job(
            lambda: _safe_run(f"{name} (startup)", func),
            trigger=DateTrigger(run_date=datetime.now() + timedelta(seconds=startup_delay)),
            id=f"{job_id}_startup",
            replace_existing=True,
        )
    registered.append(job_id)


def _register_jobs(sched: BackgroundScheduler) -> List[str]:
    jobs_registered: List[str] = []
    for job_id in CANONICAL_JOB_IDS:
        if _is_enabled(job_id):
            _add_job(sched, job_id, jobs_registered)
    outreach_hours = float(os.environ.get("OUTREACH_INTERVAL_HOURS", "2"))
    if "outreach_cycle" in jobs_registered:
        logger.info(
            "[SCHEDULER] Outreach armed every %sh + 45s boot cycle (single owner)",
            outreach_hours,
        )
    return jobs_registered


def start_central_scheduler(app: Any = None) -> Optional[BackgroundScheduler]:
    """Boot the single in-app scheduler. Idempotent."""
    global _scheduler
    if not HAS_APSCHEDULER:
        logger.warning("[SCHEDULER] APScheduler not available")
        return None
    if _scheduler is not None and _scheduler.running:
        return _scheduler
    _scheduler = BackgroundScheduler(timezone="America/Chicago")
    registered = _register_jobs(_scheduler)
    if not registered:
        logger.info("[SCHEDULER] No automations enabled")
        _scheduler = None
        return None
    _scheduler.start()
    logger.info("[SCHEDULER] Started jobs: %s", registered)
    return _scheduler


def stop_central_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def get_scheduler_status() -> Dict[str, Any]:
    if not _scheduler:
        return {"running": False, "jobs": []}
    jobs = []
    for job in _scheduler.get_jobs():
        base_id = job.id[:-len("_startup")] if job.id.endswith("_startup") else job.id
        try:
            from sincor2 import scheduler_tiers
            tier = scheduler_tiers.job_tier(base_id)
        except Exception:
            tier = None
        jobs.append({"id": job.id, "name": job.name, "tier": tier,
                     "next_run": str(job.next_run_time) if job.next_run_time else None})
    return {"running": _scheduler.running, "job_count": len(jobs), "jobs": jobs}


# Backwards-compatible aliases for the daily_ops_scheduler re-export chain.
def start_daily_ops_scheduler(app=None):
    if _scheduler is None or not _scheduler.running:
        return start_central_scheduler(app)
    return _scheduler


def stop_daily_ops_scheduler():
    stop_central_scheduler()
