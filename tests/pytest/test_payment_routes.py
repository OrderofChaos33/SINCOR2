"""WP4 tests: Stripe legacy route disabling (D3) + x402 gate hardening (W-39).

- The 5 unauthenticated legacy Stripe routes return 403.
- The Stripe webhook (signature-verified) is NOT disabled.
- x402: whitespace/fabricated tokens are rejected by the real verifier.
"""

import pytest


# --- Stripe route disabling -------------------------------------------------


def _build_stripe_app():
    """Build a minimal Flask app with the Stripe blueprint registered."""
    from flask import Flask
    from sincor2.stripe_routes import init_stripe_routes, stripe_bp

    app = Flask(__name__)

    class _DummyProcessor:
        enabled = True

    # init_stripe_routes registers the blueprint; routes are disabled via
    # the D3 decorator regardless of processor state.
    try:
        init_stripe_routes(app, _DummyProcessor())
    except Exception:
        # Registration may fail without full app context; fall back to
        # direct blueprint registration for route-existence checks.
        app.register_blueprint(stripe_bp)
    return app


def test_stripe_checkout_disabled_403():
    app = _build_stripe_app()
    client = app.test_client()
    resp = client.post("/api/stripe/checkout", json={"plan_id": "x"})
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "legacy_stripe_disabled"


def test_stripe_portal_disabled_403():
    app = _build_stripe_app()
    client = app.test_client()
    resp = client.post("/api/stripe/portal", json={"customer_id": "cus_x"})
    assert resp.status_code == 403


def test_stripe_session_lookup_disabled_403():
    app = _build_stripe_app()
    client = app.test_client()
    resp = client.get("/api/stripe/session/sess_x")
    assert resp.status_code == 403


def test_stripe_cancel_disabled_403():
    app = _build_stripe_app()
    client = app.test_client()
    resp = client.post("/api/stripe/cancel/sub_x")
    assert resp.status_code == 403


def test_stripe_dashboard_disabled_403():
    app = _build_stripe_app()
    client = app.test_client()
    resp = client.get("/api/stripe/dashboard")
    assert resp.status_code == 403


def test_stripe_webhook_not_disabled():
    """The webhook is Stripe-signature-verified; it stays active to drain
    in-flight events. It must NOT return the D3 403."""
    app = _build_stripe_app()
    client = app.test_client()
    # No signature -> the webhook's own verification rejects (400/500),
    # but it must not be the D3 disabled-route 403.
    resp = client.post("/api/stripe/webhook", data="{}",
                       content_type="application/json")
    assert resp.status_code != 403


# --- x402 gate (W-39) -------------------------------------------------------


def test_whitespace_token_rejected():
    """W-39: a whitespace 'token' must not pass the x402 gate."""
    from sincor2.mcp_server import (
        PAID_TOOLS,
        PaymentRequired,
        _require_x402_payment,
        register_paid_tool,
    )

    register_paid_tool("wp4_test_tool", price="0.01", pay_to="0x" + "00" * 20)
    assert "wp4_test_tool" in PAID_TOOLS
    try:
        with pytest.raises(PaymentRequired):
            _require_x402_payment("wp4_test_tool", {"x402_access_token": "   "})
    finally:
        PAID_TOOLS.discard("wp4_test_tool")


def test_fabricated_token_rejected():
    """W-39: a random fabricated token must not pass (no DB row)."""
    from sincor2.mcp_server import (
        PAID_TOOLS,
        PaymentRequired,
        _require_x402_payment,
        register_paid_tool,
    )

    register_paid_tool("wp4_test_tool2", price="0.01", pay_to="0x" + "00" * 20)
    try:
        with pytest.raises(PaymentRequired):
            _require_x402_payment(
                "wp4_test_tool2", {"x402_access_token": "tok_fabricated_12345"}
            )
    finally:
        PAID_TOOLS.discard("wp4_test_tool2")


def test_missing_token_raises_challenge():
    from sincor2.mcp_server import (
        PAID_TOOLS,
        PaymentRequired,
        _require_x402_payment,
        register_paid_tool,
    )

    register_paid_tool("wp4_test_tool3", price="0.05", pay_to="0x" + "11" * 20)
    try:
        with pytest.raises(PaymentRequired) as exc_info:
            _require_x402_payment("wp4_test_tool3", {})
        assert exc_info.value.price == "0.05"
    finally:
        PAID_TOOLS.discard("wp4_test_tool3")


def test_unpaid_tool_not_gated():
    """Tools not in PAID_TOOLS pass through without a token."""
    from sincor2.mcp_server import _require_x402_payment

    # Must not raise.
    _require_x402_payment("list_tasks", {})
