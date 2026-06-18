"""Production kill-switches — prevent accidental fund movement or secret exposure."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("sincor.safety")

_IS_PROD = os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("FLASK_ENV", "").lower() == "production"
_OVERRIDE = os.getenv("SAFETY_OVERRIDE", "").lower() == "true"


def onchain_writes_allowed() -> bool:
    """Treasury burns, forwarder signing, auto-trades — off in production unless override."""
    if _OVERRIDE:
        return True
    if _IS_PROD:
        return False
    return os.getenv("ALLOW_ONCHAIN_WRITES", "false").lower() == "true"


def assert_production_safety() -> list[str]:
    """
    Run at app startup. Returns human-readable warnings (logged internally only).
    Does not raise — we fail closed on individual dangerous actions instead of crashing the app.
    """
    warnings: list[str] = []

    if os.getenv("AGENT_BURN_AUTO", "false").lower() == "true" and not onchain_writes_allowed():
        warnings.append("AGENT_BURN_AUTO=true blocked in production (set false in Railway)")

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

    if not warnings and _IS_PROD:
        logger.info(
            "[SAFETY] Production locks active — no auto on-chain writes, compliance confidential"
        )

    return warnings