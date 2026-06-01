from __future__ import annotations

from decimal import Decimal, InvalidOperation
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, request

from sincor2.error_handling import ApiError
from sincor2.validation_models import PaymentCreateRequest, validate_request

payments_bp = Blueprint("payments", __name__, url_prefix="/api/payment")


def _format_decimal_amount(amount: Decimal) -> str:
    """Normalize decimal amounts to two fixed digits for API responses."""
    return str(amount.quantize(Decimal("0.01")))


@payments_bp.post("/stripe/create-checkout")
def stripe_checkout():
    payload = request.get_json(silent=True) or {}
    validated, error = validate_request(PaymentCreateRequest, payload)
    if error:
        raise ApiError("invalid_payment_request", error, status=400)

    stripe_processor = current_app.extensions.get("stripe_checkout")
    if not stripe_processor:
        raise ApiError("stripe_unavailable", "Stripe is not configured", status=503)

    result = stripe_processor.create_checkout_session(
        product_name=validated["description"],
        price_cents=int(round(validated["amount"] * 100)),
        customer_email=validated.get("customer_email"),
        is_subscription=False,
    )
    if not result.get("success"):
        raise ApiError("stripe_error", result.get("error", "Failed to create checkout"), status=502)
    return jsonify(result), 201


@payments_bp.post("/paypal/create-order")
def paypal_create_order():
    payload = request.get_json(silent=True) or {}
    amount_raw = payload.get("amount")
    try:
        amount = Decimal(str(amount_raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ApiError("invalid_amount", "Amount must be numeric", status=400) from exc

    if amount <= 0:
        raise ApiError("invalid_amount", "Amount must be greater than zero", status=400)

    currency = str(payload.get("currency", "USD")).upper()[:3] or "USD"
    order_id = f"PAY-{uuid4().hex[:8].upper()}"
    return (
        jsonify(
            {
                "success": True,
                "order_id": order_id,
                "amount": _format_decimal_amount(amount),
                "currency": currency,
                "approval_url": f"https://www.paypal.com/checkoutnow?token={order_id}",
            }
        ),
        201,
    )
