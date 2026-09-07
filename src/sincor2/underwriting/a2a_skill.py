"""A2A skill surface for underwrite.echo.

Mount later from a2a_inbound / app. This module is import-safe and does
not touch Flask on load.
"""

from __future__ import annotations

from typing import Any

from .runtime import UnderwriteRuntime, boot
from .x402_seller import ECHO_PAYEE, ECHO_PRICE, ECHO_SKILL, handle_echo

SKILL_ID = ECHO_SKILL
SKILL_CARD = {
    "id": SKILL_ID,
    "name": "Underwrite Echo",
    "description": "Paid echo gated by a TOA spend envelope. $0.05 USDC demo.",
    "price_usd": ECHO_PRICE,
    "payee": ECHO_PAYEE,
    "envelope_required": True,
    "x402": True,
}

_rt: UnderwriteRuntime | None = None


def runtime() -> UnderwriteRuntime:
    global _rt
    if _rt is None:
        _rt = boot()
    return _rt


def handle_skill(headers: dict[str, str] | None = None, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    env = headers.get("x-sincor-envelope") or (body or {}).get("envelope_id")
    sig = headers.get("payment-signature") or (body or {}).get("payment_signature")
    return handle_echo(runtime().facilitator, envelope_id=env, payment_sig=sig)
