"""Human checkout: USD plans settle in Base USDC when AXM has no spot."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sincor2.onchain.constants import TREASURY, USDC_TOKEN  # noqa: E402
from sincor2 import platform_payments as pp  # noqa: E402


def test_usdc_token_metadata():
    assert pp.token_address("USDC") == USDC_TOKEN
    assert pp.token_decimals("USDC") == 6
    assert pp.display_to_atomic(297, "USDC") == 297_000_000


def test_starter_falls_back_to_usdc_when_axm_spot_missing():
    pp._spot_cache.clear()
    with patch.object(pp, "fetch_axm_spot_usd", return_value=None):
        token, display, spot, mode = pp.settle_plan_quote(pp.PLATFORM_PLANS["starter"])
    assert token == "USDC"
    assert display == 297.0
    assert spot == 1.0
    assert mode == "usdc_fallback"


def test_report_stays_axm_fixed_without_spot():
    with patch.object(pp, "fetch_axm_spot_usd", return_value=None):
        token, display, spot, mode = pp.settle_plan_quote(pp.PLATFORM_PLANS["report"])
    assert token == "AXM"
    assert display == 500.0
    assert mode == "fixed"


def test_starter_axm_when_spot_exists():
    with patch.object(pp, "fetch_axm_spot_usd", return_value=0.10):
        token, display, spot, mode = pp.settle_plan_quote(pp.PLATFORM_PLANS["starter"])
    assert token == "AXM"
    assert display == 2970.0
    assert mode == "spot"


def test_list_plans_starter_spot_available_via_usdc():
    with patch.object(pp, "fetch_axm_spot_usd", return_value=None):
        plans = {p["id"]: p for p in pp.list_plans()}
    starter = plans["starter"]
    assert starter["token"] == "USDC"
    assert starter["preferred_token"] == "AXM"
    assert starter["spot_available"] is True
    assert starter["amount_display"] == 297.0
    assert starter["amount_atomic"] == "297000000"
    assert starter["treasury"] == TREASURY
    assert starter["token_address"].lower() == USDC_TOKEN.lower()


def test_create_checkout_starter_usdc(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    pp._spot_cache.clear()
    with patch.object(pp, "fetch_axm_spot_usd", return_value=None):
        result = pp.create_checkout("starter", customer_email="ops@example.com")
    assert result["ok"] is True
    assert result["token"] == "USDC"
    assert result["amount_display"] == 297.0
    assert result["amount_atomic"] == "297000000"
    assert result["pricing_mode"] == "usdc_fallback"
    assert result["treasury"].lower() == TREASURY.lower()
    assert result["token_address"].lower() == USDC_TOKEN.lower()


def test_sinc_is_not_revived_as_fallback():
    fake = {
        "label": "Legacy",
        "product_name": "Legacy",
        "order_type": "subscription",
        "usd_reference": 297,
        "token": "SINC",
        "billing": "month",
    }
    with patch.object(pp, "fetch_sinc_spot_usd", return_value=None):
        token, display, _spot, mode = pp.settle_plan_quote(fake)
    assert token == "SINC"
    assert display == 0.0
    assert mode == "spot"
