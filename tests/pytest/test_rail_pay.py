"""Tests for the human x402 payment page (GET /rail/pay/<sku>) and /rail/onramp.

The parallel builder's /rail/checkout + /rail/settle endpoints are not in
this tree; the JS contract they must honor is asserted here against the real
x402 challenge shape (server-side), per the build spec.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES = REPO_ROOT / "templates"


@pytest.fixture()
def rail_app():
    from sincor2.blueprints.rail_pay import rail_pay_bp

    app = Flask(__name__, template_folder=str(TEMPLATES))
    app.register_blueprint(rail_pay_bp)
    app.config["TESTING"] = True
    return app


def test_routes_registered_on_test_app(rail_app):
    rules = {str(r) for r in rail_app.url_map.iter_rules()}
    assert "/rail/pay/<resource_id>" in rules
    assert "/rail/onramp" in rules


def test_pay_page_returns_200_with_bootstrap(rail_app, monkeypatch):
    for var in ("CDP_API_KEY", "CDP_API_KEY_ID", "CDP_API_KEY_SECRET"):
        monkeypatch.delenv(var, raising=False)
    client = rail_app.test_client()
    resp = client.get("/rail/pay/healthcare-credential-check")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # Bootstrap carries the sku for the checkout call; no secret material.
    assert '"resource_id": "healthcare-credential-check"' in html
    assert '"onramp_configured": false' in html
    # JS contract: the page must hit exactly these endpoints.
    assert "/rail/checkout" in html
    assert "/rail/settle" in html
    assert "/rail/onramp" in html
    # JS contract: wallet + ERC-20 payment primitives.
    assert "eth_sendTransaction" in html
    assert "eth_requestAccounts" in html
    assert "a9059cbb" in html  # ERC-20 transfer selector
    assert "0x2105" in html  # Base chain id (8453)


def test_pay_page_rejects_bad_resource_format(rail_app):
    client = rail_app.test_client()
    resp = client.get("/rail/pay/..%2F..%2Fetc")
    assert resp.status_code == 404
    resp = client.get("/rail/pay/" + "x" * 200)
    assert resp.status_code == 404


def test_checkout_payload_has_all_js_fields(monkeypatch, tmp_path):
    """The real challenge shape carries every field the page JS needs."""
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    from sincor2.platform_payments import token_address
    from sincor2.x402_payments import create_challenge

    challenge = create_challenge("healthcare-credential-check")
    assert challenge["error"] == "ownership_proof_required"
    assert challenge["challenge_id"].startswith("x402-")

    payment = challenge["payment"]
    for field in ("challenge_id", "payTo", "asset", "maxAmountRequired", "chainId"):
        assert field in challenge or field in payment, field

    assert payment["chainId"] == 8453
    assert payment["payTo"] == "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
    assert payment["asset"] == token_address("USDC")
    # 5.0 USDC at 6 decimals
    assert payment["maxAmountRequired"] == "5000000"
    assert payment["extra"]["token"] == "USDC"
    assert payment["extra"]["amount_display"] == 5.0
    # Bind flow the page must complete before the USDC transfer.
    assert challenge["bind"]["bind_url"] == "/api/x402/challenge-bind"
    assert challenge["bind"]["sign_message_format"].startswith(
        "SINCOR-X402-CHALLENGE-BIND|"
    )


def test_usdc_token_address_matches_platform_payments():
    from sincor2.platform_payments import token_address, token_decimals

    assert token_address("USDC") == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    assert token_decimals("USDC") == 6


def test_onramp_route_fail_closed_without_keys(rail_app, monkeypatch):
    for var in ("CDP_API_KEY", "CDP_API_KEY_ID", "CDP_API_KEY_SECRET"):
        monkeypatch.delenv(var, raising=False)
    client = rail_app.test_client()
    resp = client.get("/rail/onramp?amount_usd=5")
    assert resp.status_code == 503
    body = resp.get_json()
    assert body == {"ok": False, "error": "onramp_unconfigured"}
    assert "url" not in body


def test_onramp_route_tampered_amount_rejected(rail_app, monkeypatch):
    monkeypatch.setenv("CDP_API_KEY", "k")
    client = rail_app.test_client()
    for bad in ("abc", "-5", "1e999999999999", ""):
        resp = client.get(f"/rail/onramp?amount_usd={bad}")
        assert resp.status_code == 400, bad
        assert resp.get_json() == {"ok": False, "error": "invalid_amount"}


def test_onramp_route_amount_is_prefill_only(rail_app, monkeypatch):
    """Server derives nothing from the query amount; it only prefills the widget."""
    monkeypatch.setenv("CDP_API_KEY", "k")

    from sincor2 import onramp

    monkeypatch.setattr(
        onramp,
        "_http_post_json",
        lambda url, payload, headers, timeout=15: {"token": "sess_route_1"},
    )
    client = rail_app.test_client()
    resp = client.get("/rail/onramp?amount_usd=25.5")
    assert resp.status_code == 200
    body = resp.get_json()
    # No price authority leaves the server: only ok + url.
    assert set(body.keys()) == {"ok", "url"}
    assert body["ok"] is True
    assert "presetFiatAmount=25.50" in body["url"]


def test_onramp_route_validates_destination_address(rail_app, monkeypatch):
    monkeypatch.setenv("CDP_API_KEY", "k")
    client = rail_app.test_client()
    resp = client.get("/rail/onramp?destination_address=not-an-address")
    assert resp.status_code == 400
    assert resp.get_json() == {"ok": False, "error": "invalid_destination_address"}
