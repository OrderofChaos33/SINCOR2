"""Production kill-switches — prevent accidental fund movement or secret exposure."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("sincor.safety")

_IS_PROD = os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("FLASK_ENV", "").lower() == "production"
_OVERRIDE = os.getenv("SAFETY_OVERRIDE", "").lower() == "true"
# Second confirmation key (defense in depth, C5 remediation 2026-10-08):
# SAFETY_OVERRIDE=true alone no longer bypasses production locks.
_OVERRIDE_CONFIRM = os.getenv("SAFETY_OVERRIDE_CONFIRM", "") == "I_UNDERSTAND"

_override_announced = False


class ProductionSafetyError(RuntimeError):
    """Raised at startup when production runs with a dangerous config.

    Fail-closed: the process must not serve traffic with the production
    safety locks bypassed or live execution armed without the full
    override ceremony.
    """


def safety_override_active() -> bool:
    """Two-key production override.

    Returns True only when BOTH ``SAFETY_OVERRIDE=true`` AND
    ``SAFETY_OVERRIDE_CONFIRM=I_UNDERSTAND`` are set. Every evaluation
    logs loudly: a missing second key is a CRITICAL "override ignored"
    event, and an active override is a CRITICAL "locks bypassed" event.
    """
    global _override_announced
    if not _OVERRIDE:
        return False
    if not _OVERRIDE_CONFIRM:
        if not _override_announced:
            logger.critical(
                "[SAFETY] SAFETY_OVERRIDE=true IGNORED — second confirmation "
                "missing (set SAFETY_OVERRIDE_CONFIRM=I_UNDERSTAND). "
                "Production locks remain ENGAGED.")
            _override_announced = True
        return False
    if not _override_announced:
        logger.critical(
            "[SAFETY] !!! SAFETY_OVERRIDE ACTIVE (two-key confirmed) — "
            "production on-chain write locks BYPASSED !!!")
        _override_announced = True
    return True


def onchain_writes_allowed() -> bool:
    """Treasury burns, forwarder signing, auto-trades — off in production unless override."""
    if safety_override_active():
        return True
    if _IS_PROD:
        return False
    return os.getenv("ALLOW_ONCHAIN_WRITES", "false").lower() == "true"


# Safety-critical env flags echoed at startup. Values are safe to log for
# every name here; BILLING_FORWARDER_PRIVATE_KEY is deliberately absent —
# it is logged as set/unset only (never its value).
_SAFETY_ENV_FLAGS = (
    "RAILWAY_ENVIRONMENT",       # prod detection context
    "FLASK_ENV",                 # prod detection context
    "POLYCLAW_AUTO_EXECUTE",     # dangerous when true
    "EXECUTE_LIVE",              # dangerous when 1
    "COMPLIANCE_CONFIDENTIAL",   # dangerous when false
    "COMPLIANCE_LOG_TO_STDOUT",  # dangerous when true
    "ALLOW_ONCHAIN_WRITES",      # non-prod onchain write gate
    "SAFETY_OVERRIDE",           # two-key override, part 1
)


def _flag_is_dangerous(name: str, value: str) -> bool:
    """Mirror the dangerous-state predicates used by assert_production_safety."""
    v = (value or "").strip()
    if name == "POLYCLAW_AUTO_EXECUTE":
        return v.lower() == "true"
    if name == "EXECUTE_LIVE":
        return v == "1"
    if name == "COMPLIANCE_CONFIDENTIAL":
        return v.lower() == "false"
    if name == "COMPLIANCE_LOG_TO_STDOUT":
        return v.lower() == "true"
    if name == "SAFETY_OVERRIDE":
        return v.lower() == "true"
    return False


def log_safety_env() -> None:
    """Echo safety-critical env flags at startup (2026-10-12 outage fix).

    Logs each flag's effective value at INFO on every boot, e.g.
    ``SAFETY_ENV POLYCLAW_AUTO_EXECUTE=false``. Flags in a dangerous state
    are additionally logged at WARNING. Secrets are never logged:
    BILLING_FORWARDER_PRIVATE_KEY and SAFETY_OVERRIDE_CONFIRM are reported
    as set/unset only.
    """
    dangerous: list[str] = []
    for name in _SAFETY_ENV_FLAGS:
        value = os.getenv(name, "")
        display = value if value else "<unset>"
        logger.info("[SAFETY] SAFETY_ENV %s=%s", name, display)
        if _flag_is_dangerous(name, value):
            dangerous.append(name)
    fwd = "set" if os.getenv("BILLING_FORWARDER_PRIVATE_KEY", "").strip() else "unset"
    logger.info("[SAFETY] SAFETY_ENV BILLING_FORWARDER_PRIVATE_KEY=%s", fwd)
    confirm = "set" if os.getenv("SAFETY_OVERRIDE_CONFIRM", "") else "unset"
    logger.info("[SAFETY] SAFETY_ENV SAFETY_OVERRIDE_CONFIRM=%s", confirm)
    for name in dangerous:
        logger.warning(
            "[SAFETY] SAFETY_ENV %s is in DANGEROUS state — may block production boot",
            name,
        )


def assert_production_safety() -> list[str]:
    """
    Run at app startup. Returns the warning list (logged internally).

    FAIL-CLOSED (C5/HIGH remediation, 2026-10-08): when running in
    production (RAILWAY_ENVIRONMENT set or FLASK_ENV=production) with a
    dangerous configuration, this RAISES ProductionSafetyError instead of
    merely warning — the process must not serve traffic in that state.
    Outside production it keeps the old warn-and-continue behavior.
    """
    # Echo the safety-critical env flags FIRST so the effective config is in
    # the logs even when the guard below raises (2026-10-12 outage:
    # POLYCLAW_AUTO_EXECUTE=true was invisible in startup logs).
    log_safety_env()
    warnings: list[str] = []

    if os.getenv("EXECUTE_LIVE", "0").strip() == "1" and _IS_PROD and not safety_override_active():
        warnings.append("EXECUTE_LIVE=1 is set in production — treasury agent will broadcast if a signer key is present")

    if os.getenv("POLYCLAW_AUTO_EXECUTE", "false").lower() == "true" and not onchain_writes_allowed():
        warnings.append("POLYCLAW_AUTO_EXECUTE=true blocked in production")

    if os.getenv("BILLING_FORWARDER_PRIVATE_KEY", "").strip() and not onchain_writes_allowed():
        warnings.append(
            "BILLING_FORWARDER_PRIVATE_KEY is set but forwarder signing is blocked in production"
        )

    if os.getenv("COMPLIANCE_CONFIDENTIAL", "true").lower() == "false":
        warnings.append("COMPLIANCE_CONFIDENTIAL=false — compliance data may leave the volume")

    if os.getenv("COMPLIANCE_LOG_TO_STDOUT", "false").lower() == "true":
        warnings.append("COMPLIANCE_LOG_TO_STDOUT=true — violation metadata may appear in Railway logs")

    for msg in warnings:
        logger.warning("[SAFETY] %s", msg)

    if warnings and _IS_PROD:
        raise ProductionSafetyError(
            "refusing to start: dangerous production configuration — "
            + "; ".join(warnings))

    if not warnings and _IS_PROD:
        logger.info(
            "[SAFETY] Production locks active — no auto on-chain writes, compliance confidential"
        )

    return warnings