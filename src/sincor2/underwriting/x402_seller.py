from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from .facilitator import Facilitator
from .types import AuthorizeRequest

ECHO_SKILL = "underwrite.echo"
ECHO_PRICE = "0.05"
ECHO_PAYEE = "sincor:skill:underwrite.echo"


def payment_required_body() -> dict[str, Any]:
    return {
        "scheme": "exact",
        "network": "base",
        "asset": "USDC",
        "amount": ECHO_PRICE,
        "payee": ECHO_PAYEE,
        "envelope_required": True,
    }


def demo_sign(secret: str, payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def handle_echo(
    facilitator: Facilitator,
    *,
    envelope_id: str | None,
    payment_sig: str | None,
    hmac_secret: str = "dev-only-change-me",
) -> tuple[int, dict[str, Any]]:
    if not envelope_id:
        return 402, payment_required_body()
    payload = {"envelope_id": envelope_id, "amount": ECHO_PRICE, "payee": ECHO_PAYEE}
    if payment_sig and payment_sig != demo_sign(hmac_secret, payload) and payment_sig != envelope_id:
        return 402, {"error": "bad_signature", **payment_required_body()}

    result = facilitator.authorize(
        AuthorizeRequest(
            envelope_id=envelope_id,
            payee=ECHO_PAYEE,
            amount_usd=ECHO_PRICE,
            skill_id=ECHO_SKILL,
        )
    )
    if not result.allowed:
        return 403, {"error": result.reason, "remaining_usd": result.remaining_usd}
    return 200, {
        "ok": True,
        "quote": "underwrite.echo",
        "remaining_usd": result.remaining_usd,
        "receipt_id": result.receipt_id,
        "PAYMENT-RESPONSE": "settled",
    }
