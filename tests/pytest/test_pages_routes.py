"""Tests for the pages blueprint routes added/fixed in PR #52."""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Routes that should render a template and return 200
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/sinc",
    "/mvp",
    "/signup",
    "/buy",
    "/pricing",
    "/login",
    "/terms",
    "/privacy",
    "/axiom",
    "/dashboard",
    "/my-orders",
    "/payment/success",
    "/payment/cancel",
    "/onboarding",
    "/security",
])
def test_page_route_ok(client, path):
    """Each page route must respond 200 and return HTML."""
    response = client.get(path)
    assert response.status_code == 200
    assert b"<!DOCTYPE html>" in response.data or b"<html" in response.data


# ---------------------------------------------------------------------------
# Routes that redirect
# ---------------------------------------------------------------------------

def test_wallet_connect_redirects_to_sinc(client):
    """/wallet-connect should redirect to /sinc (302)."""
    response = client.get("/wallet-connect")
    assert response.status_code == 302
    assert "/sinc" in response.headers["Location"]


def test_billing_redirects_to_dashboard(client):
    """/billing should redirect to /dashboard (302)."""
    response = client.get("/billing")
    assert response.status_code == 302
    assert "/dashboard" in response.headers["Location"]


# ---------------------------------------------------------------------------
# /payment/success passes session_id query param to the template
# ---------------------------------------------------------------------------

def test_payment_success_accepts_session_id(client):
    """/payment/success must accept the session_id query param without error."""
    response = client.get("/payment/success?session_id=cs_test_abc123")
    assert response.status_code == 200
