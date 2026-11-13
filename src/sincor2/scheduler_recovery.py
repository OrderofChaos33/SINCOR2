"""Boot crash-recovery for the central scheduler (one-scheduler, STREAM B).

On scheduler start, reads the run ledger for INTERRUPTED runs (started but
never finished — i.e. the process died mid-run):

- Tier 0/1 jobs (read-only/draft work, safe to rerun): requeued as a one-shot
  DateTrigger run shortly after boot.
- Tier 2/3 jobs (outbound/trading side effects): HELD for operator review —
  logged LOUDLY, never auto-run.

Tier mapping is provisional (Stream C owns tiers/idempotency); until then we
classify by job id per the consolidation spec:
  tier 2 = outreach_cycle, subscription_reminders, launch_review_notify, partner_notify
  tier 3 = polyclaw_scan
  everything else = tier 1 (safe to rerun)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sincor2.scheduler_recovery")

# Provisional tier map (Stream C owns the canonical tier/idempotency model).
TIER2_JOB_IDS = frozenset({
    "outreach_cycle",
    "subscription_reminders",
    "launch_review_notify",
    "partner_notify",
})
TIER3_JOB_IDS = frozenset({
    "polyclaw_scan",
})

REQUEUE_DELAY_SEC = 30


def job_tier(job_id: str) -> int:
    """Provisional tier classification by job id (Stream C owns the real one)."""
    if job_id in TIER3_JOB_IDS:
        return 3
    if job_id in TIER2_JOB_IDS:
        return 2
    return 1


_last_recovery: Dict[str, List[str]] = {"requeued": [], "held": []}


def get_last_recovery() -> Dict[str, List[str]]:
    """Result of the most recent boot-recovery pass (copies)."""
    return {"requeued": list(_last_recovery["requeued"]), "held": list(_last_recovery["held"])}


def run_boot_recovery(
    sched: Any,
    ledger: Any = None,
    database_url: Optional[str] = None,
    requeue_delay_sec: int = REQUEUE_DELAY_SEC,
) -> Dict[str, List[str]]:
    """Requeue interrupted Tier 0/1 runs; HOLD Tier 2/3 for review.

    Returns {"requeued": [job_ids], "held": [job_ids]}. Never raises —
    recovery must not break boot.
    """
    global _last_recovery
    result: Dict[str, List[str]] = {"requeued": [], "held": []}
    try:
        from sincor2 import scheduler as central
        from sincor2.scheduler_ledger import get_ledger

        ledger = ledger or get_ledger(database_url)
        interrupted = ledger.interrupted_runs()
    except Exception as e:
        logger.warning("[SCHEDULER][RECOVERY] ledger unreadable, skipping recovery: %s", e)
        _last_recovery = {"requeued": [], "held": []}
        return result

    if not interrupted:
        _last_recovery = {"requeued": [], "held": []}
        return result

    seen = set()
    for run in interrupted:
        job_id = run.get("job_id")
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)
        if job_id not in central.SCHEDULER_DEFAULTS:
            logger.warning("[SCHEDULER][RECOVERY] unknown job_id %r in ledger, ignoring", job_id)
            continue
        if not central._is_enabled(job_id):
            logger.info("[SCHEDULER][RECOVERY] interrupted run for disabled job %s, skipping", job_id)
            continue
        tier = job_tier(job_id)
        if tier >= 2:
            result["held"].append(job_id)
            continue
        # Tier 0/1: safe to rerun — one-shot, shortly after boot.
        try:
            from apscheduler.triggers.date import DateTrigger

            sched.add_job(
                central._dispatch_job,
                args=(job_id,),
                trigger=DateTrigger(
                    run_date=datetime.now(timezone.utc) + timedelta(seconds=requeue_delay_sec)
                ),
                id=f"{job_id}_recovery",
                name=f"{central.SCHEDULER_DEFAULTS[job_id]['name']} (crash recovery)",
                replace_existing=True,
                max_instances=1,
            )
            result["requeued"].append(job_id)
            logger.info(
                "[SCHEDULER][RECOVERY] requeued interrupted tier-%d job %s",
                tier,
                job_id,
            )
        except Exception as e:
            logger.warning("[SCHEDULER][RECOVERY] failed to requeue %s: %s", job_id, e)

    if result["held"]:
        held = sorted(set(result["held"]))
        result["held"] = held
        logger.warning(
            "!!! [SCHEDULER][RECOVERY] HELD %d interrupted Tier2/3 job(s) for "
            "operator review — NOT auto-rerun: %s",
            len(held),
            ", ".join(held),
        )
    _last_recovery = {"requeued": list(result["requeued"]), "held": list(result["held"])}
    return result
