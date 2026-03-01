#!/usr/bin/env python3
"""
Tests for paypal_checkout module and checkout routes.
Verifies that the checkout page loads correctly for all plans.
Run: pytest tests/test_paypal_checkout.py -v
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestPaypalCheckoutModule:
    """Test the paypal_checkout module."""

    def test_plans_dict_exists(self):
        """PLANS dict must be importable."""
        from paypal_checkout import PLANS
        assert isinstance(PLANS, dict)

    def test_all_plans_present(self):
        """starter, professional, and enterprise plans must be defined."""
        from paypal_checkout import PLANS
        assert "starter" in PLANS
        assert "professional" in PLANS
        assert "enterprise" in PLANS

    def test_plan_has_required_keys(self):
        """Each plan must have name, price, features, and paypal_plan_id."""
        from paypal_checkout import PLANS
        for plan_id, plan in PLANS.items():
            assert "name" in plan, f"{plan_id} missing 'name'"
            assert "price" in plan, f"{plan_id} missing 'price'"
            assert "features" in plan, f"{plan_id} missing 'features'"
            assert "paypal_plan_id" in plan, f"{plan_id} missing 'paypal_plan_id'"

    def test_plan_prices_are_numeric(self):
        """Plan prices must be positive numbers."""
        from paypal_checkout import PLANS
        for plan_id, plan in PLANS.items():
            assert isinstance(plan["price"], (int, float)), f"{plan_id} price not numeric"
            assert plan["price"] > 0, f"{plan_id} price must be positive"

    def test_plan_features_are_lists(self):
        """Plan features must be non-empty lists."""
        from paypal_checkout import PLANS
        for plan_id, plan in PLANS.items():
            assert isinstance(plan["features"], list), f"{plan_id} features not a list"
            assert len(plan["features"]) > 0, f"{plan_id} has no features"

    def test_missing_plan_ids_log_warning(self, monkeypatch, caplog):
        """Missing PAYPAL_PLAN_ID_* env vars should emit a warning at import."""
        import logging
        monkeypatch.delenv("PAYPAL_PLAN_ID_STARTER", raising=False)
        monkeypatch.delenv("PAYPAL_PLAN_ID_PROFESSIONAL", raising=False)
        monkeypatch.delenv("PAYPAL_PLAN_ID_ENTERPRISE", raising=False)

        import importlib
        import paypal_checkout
        with caplog.at_level(logging.WARNING, logger="paypal_checkout"):
            importlib.reload(paypal_checkout)

        assert any("not configured" in record.message for record in caplog.records)
        """paypal_plan_id should reflect PAYPAL_PLAN_ID_* environment variables."""
        monkeypatch.setenv("PAYPAL_PLAN_ID_STARTER", "P-TESTSTARTERPLAN")
        monkeypatch.setenv("PAYPAL_PLAN_ID_PROFESSIONAL", "P-TESTPROPLAN")
        monkeypatch.setenv("PAYPAL_PLAN_ID_ENTERPRISE", "P-TESTENTPLAN")

        # Re-import to pick up new env vars
        import importlib
        import paypal_checkout
        importlib.reload(paypal_checkout)

        assert paypal_checkout.PLANS["starter"]["paypal_plan_id"] == "P-TESTSTARTERPLAN"
        assert paypal_checkout.PLANS["professional"]["paypal_plan_id"] == "P-TESTPROPLAN"
        assert paypal_checkout.PLANS["enterprise"]["paypal_plan_id"] == "P-TESTENTPLAN"


class TestCheckoutRoutes:
    """Test the /checkout/<plan_id> routes in sincor_app."""

    @pytest.fixture
    def client(self):
        from sincor_app import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_checkout_starter_returns_200(self, client):
        """GET /checkout/starter should return 200."""
        response = client.get("/checkout/starter")
        assert response.status_code == 200

    def test_checkout_professional_returns_200(self, client):
        """GET /checkout/professional should return 200."""
        response = client.get("/checkout/professional")
        assert response.status_code == 200

    def test_checkout_enterprise_returns_200(self, client):
        """GET /checkout/enterprise should return 200."""
        response = client.get("/checkout/enterprise")
        assert response.status_code == 200

    def test_checkout_invalid_plan_returns_404(self, client):
        """GET /checkout/nonexistent should return 404."""
        response = client.get("/checkout/nonexistent")
        assert response.status_code == 404

    def test_checkout_page_contains_plan_name(self, client):
        """Checkout page should display the plan name."""
        response = client.get("/checkout/starter")
        assert b"SINCOR Starter Plan" in response.data

    def test_checkout_page_contains_price(self, client):
        """Checkout page should display the plan price."""
        response = client.get("/checkout/starter")
        assert b"297" in response.data

    def test_checkout_page_uses_env_plan_id(self, monkeypatch):
        """Checkout page should embed the PayPal plan ID from environment."""
        monkeypatch.setenv("PAYPAL_PLAN_ID_PROFESSIONAL", "P-ENV-PROFESSIONAL-ID")

        import importlib
        import paypal_checkout
        importlib.reload(paypal_checkout)

        from sincor_app import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            response = c.get("/checkout/professional")
            assert response.status_code == 200
            assert b"P-ENV-PROFESSIONAL-ID" in response.data
