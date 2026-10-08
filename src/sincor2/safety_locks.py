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


def assert_production_safety() -> list[str]:
    """
    Run at app startup. Returns the warning list (logged internally).

    FAIL-CLOSED (C5/HIGH remediation, 2026-10-08): when running in
    production (RAILWAY_ENVIRONMENT set or FLASK_ENV=production) with a
    dangerous configuration, this RAISES ProductionSafetyError instead of
    merely warning — the process must not serve traffic in that state.
    Outside production it keeps the old warn-and-continue behavior.
    """
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