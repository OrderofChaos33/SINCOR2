"""Effect-tier classification for the SINCOR2 central scheduler (one-scheduler, STREAM C).

Every canonical scheduler job is classified by the *kind of effect* its run can
produce. Tiers drive scheduling-layer policy only:

- Tier 0 (read-only):          observe/scan/aggregate; never emit externally.
- Tier 1 (draft/internal):      creates internal artifacts (drafts, reports);
                                no outbound effect.
- Tier 2 (outbound):           sends messages / publishes to people or channels.
- Tier 3 (money):              can move funds or place trades.

Policy knobs (read live from env on every evaluation — founder-toggleable,
no restarts needed):

- SCHEDULER_EFFECTS_PAUSED=true   -> skip all Tier 2/3 jobs at the wrapper
                                     level. Tier 0/1 unaffected.
- SCHEDULER_REQUIRE_APPROVAL=true -> Tier 2/3 jobs run in draft-only mode:
                                     the wrapped job does NOT invoke its
                                     effect function; an intent record
                                     (job_id, run_id, timestamp,
                                     would_have_done) is recorded instead.

This module only CLASSIFIES. Enforcement lives in the job wrapper in
``sincor2.scheduler`` and the idempotency/intent helpers in
``sincor2.scheduler_idempotency``.
"""
from __future__ import annotations

import os

TIER_READ_ONLY = 0
TIER_DRAFT_INTERNAL = 1
TIER_OUTBOUND = 2
TIER_MONEY = 3

TIER_NAMES = {
    TIER_READ_ONLY: "read-only",
    TIER_DRAFT_INTERNAL: "draft/internal",
    TIER_OUTBOUND: "outbound",
    TIER_MONEY: "money",
}

EFFECT_TIERS = {
    TIER_READ_ONLY: {
        "name": "read-only",
        "jobs": ("sadas_scan", "compliance_checks", "daily_ops"),
    },
    TIER_DRAFT_INTERNAL: {
        "name": "draft/internal",
        "jobs": ("content_cycle", "launch_cycle", "recursive_optimizer"),
    },
    TIER_OUTBOUND: {
        "name": "outbound",
        "jobs": (
            "outreach_cycle",
            "subscription_reminders",
            "launch_review_notify",
            "partner_notify",
            # AXM activity engine: posts sealed auctions and bids to the
            # marketplace (outbound publishing). Ledger-only and gasless —
            # no broadcasts, no fund movement — so Tier 2, not Tier 3.
            "activity_engine_cycle",
        ),
    },
    TIER_MONEY: {
        "name": "money",
        "jobs": ("polyclaw_scan",),
    },
}

_JOB_TIER = {
    job_id: tier
    for tier, spec in EFFECT_TIERS.items()
    for job_id in spec["jobs"]
}


def job_tier(job_id: str) -> int:
    """Return the effect tier (0-3) for a canonical scheduler job id.

    Raises KeyError for unknown job ids — every job must be explicitly
    classified; silently defaulting an outbound/money job to Tier 0 would
    bypass the kill-switch.
    """
    return _JOB_TIER[job_id]


def tier_name(tier: int) -> str:
    return TIER_NAMES[tier]


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "false").strip().lower() == "true"


def effects_paused() -> bool:
    """Global kill-switch: when true, Tier 2/3 jobs are skipped."""
    return _truthy_env("SCHEDULER_EFFECTS_PAUSED")


def require_approval() -> bool:
    """Approval mode: when true, Tier 2/3 jobs record intent instead of acting."""
    return _truthy_env("SCHEDULER_REQUIRE_APPROVAL")
