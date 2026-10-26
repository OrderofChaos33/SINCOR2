"""Adversarial-review fix round for the x402 paid surface (branch
xioix/x402-paid-surface, on top of the R1–R4 fix commit).

Builder B's independent review of the R1–R4 fix found three residuals,
each pinned here by a hermetic test:

  F1  The app-wide JSON 405 handler dropped the `Allow` header that
      RFC 9110 §15.5.6 requires on a 405 (stock Flask sends one; the
      JSON replacement sent none — on EVERY app route). The handler now
      restores `Allow` from the exception's `valid_methods`.
  F2  The billing blueprint's CORS origin reflection covered ALL
      billing routes, including non-paid ones (payment/paypal
      webhooks, orders, platform checkout). Preflight reflection is
      now narrowed to the paid surface only: /x402/, /api/x402/,
      /api/paid/. Non-paid billing routes get NO CORS reflection.
  F4b Actual 402 responses carried Access-Control-Expose-Headers but no
      Access-Control-Allow-Origin, so a cross-origin browser buyer
      still could not read the 402. Paid-route 402s now echo the
      request Origin as Access-Control-Allow-Origin and add
      `Vary: Origin` (never Allow-Credentials).

Run: PYTHONPATH=src:. <venv>/python -m pytest tests/pytest/test_x402_paid_surface_fixes.py -q
"""
from __future__ import annotations

import pytest

WALLET = "0x" + "11" * 20
RESOURCE = "agent_task"
ORIGIN = "https://bazaar.example"
PAID_PATH = f"/api/paid/{RESOURCE}"
NON_PAID_BILLING_PATH = "/api/payment/webhook"


@pytest.fixture()
def x402_db(tmp_path, monkeypatch):
    """Point the x402 SQLite store at a throwaway DB for the test."""
    import sincor2.x402_payments as x402

    db = tmp_path / "orders.db"
    monkeypatch.setenv("ORDERS_DB_PATH", str(db))
    x402.init_x402_db()
    return db


@pytest.fixture()
def paid_client():
    """Minimal Flask app with ONLY the mvp billing blueprint registered.

    Same proven pattern as the probe's paid_client fixture:
    sincor2.mvp_app must be imported first so billing._bind_mvp() binds
    against the initializing module (import-order quirk on origin/main).
    """
    import sincor2.mvp_app  # noqa: F401  (import order matters; see docstring)
    from flask import Flask

    from sincor2.mvp_blueprints import billing as billing_mod

    app = Flask("x402_paid_surface_fixes")
    app.register_blueprint(billing_mod.bp)
    app.config.update(TESTING=True)
    return app.test_client()


@pytest.fixture()
def mixed_client():
    """Billing blueprint PLUS a plain non-billing app route.

    The JSON 405 handler is registered app-wide (via the blueprint), so
    its Allow header must also be correct on routes that have nothing
    to do with billing.
    """
    import sincor2.mvp_app  # noqa: F401  (import order matters)
    from flask import Flask

    from sincor2.mvp_blueprints import billing as billing_mod

    app = Flask("x402_paid_surface_fixes_mixed")
    app.register_blueprint(billing_mod.bp)

    @app.route("/plain-thing", methods=["GET"])
    def _plain_thing():
        return {"ok": True}

    app.config.update(TESTING=True)
    return app.test_client()


def _allow_tokens(resp):
    return {
        t.strip().upper()
        for t in resp.headers.get("Allow", "").split(",")
        if t.strip()
    }


# ---------------------------------------------------------------------------
# F1: JSON 405 carries a correct Allow header (paid + non-billing routes)
# ---------------------------------------------------------------------------

def test_405_on_paid_route_is_json_with_allow(paid_client):
    resp = paid_client.delete(PAID_PATH)
    assert resp.status_code == 405
    assert "application/json" in resp.headers.get("Content-Type", "")
    allow = _allow_tokens(resp)
    # /api/paid/<resource_id> declares GET + POST (Flask adds HEAD/OPTIONS).
    assert {"GET", "POST"} <= allow, resp.headers.get("Allow")


def test_405_on_non_billing_route_is_json_with_allow(mixed_client):
    resp = mixed_client.delete("/plain-thing")
    assert resp.status_code == 405
    assert "application/json" in resp.headers.get("Content-Type", "")
    allow = _allow_tokens(resp)
    assert "GET" in allow, resp.headers.get("Allow")
    assert "POST" not in allow, resp.headers.get("Allow")


# ---------------------------------------------------------------------------
# F2: preflight reflection on paid routes only
# ---------------------------------------------------------------------------

def _preflight(client, path):
    return client.options(
        path,
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-Payment-Token, X-Payment",
        },
    )


def test_preflight_on_paid_route_still_reflects(paid_client):
    resp = _preflight(paid_client, PAID_PATH)
    assert resp.status_code in (200, 204)
    assert resp.headers.get("Access-Control-Allow-Origin") == ORIGIN
    allowed = {
        t.strip().lower()
        for t in resp.headers.get("Access-Control-Allow-Headers", "").split(",")
        if t.strip()
    }
    assert "x-payment-token" in allowed, allowed
    assert "x-payment" in allowed, allowed


def test_preflight_on_non_paid_billing_route_no_reflection(paid_client):
    resp = _preflight(paid_client, NON_PAID_BILLING_PATH)
    assert resp.status_code in (200, 204)
    assert resp.headers.get("Access-Control-Allow-Origin") is None
    assert resp.headers.get("Access-Control-Allow-Headers") is None


# ---------------------------------------------------------------------------
# F4b: a paid-route 402 is readable cross-origin when Origin is sent
# ---------------------------------------------------------------------------

def test_402_with_origin_carries_acao_and_vary(paid_client, x402_db):
    resp = paid_client.get(
        PAID_PATH,
        query_string={"wallet": WALLET},
        headers={"Origin": ORIGIN},
    )
    assert resp.status_code == 402, resp.get_data(as_text=True)[:300]
    assert resp.headers.get("Access-Control-Allow-Origin") == ORIGIN
    vary = {
        t.strip().lower()
        for t in resp.headers.get("Vary", "").split(",")
        if t.strip()
    }
    assert "origin" in vary, resp.headers.get("Vary")
    # Never credentialed CORS on the paid surface.
    assert resp.headers.get("Access-Control-Allow-Credentials") is None


def test_402_without_origin_has_no_acao(paid_client, x402_db):
    resp = paid_client.get(PAID_PATH, query_string={"wallet": WALLET})
    assert resp.status_code == 402, resp.get_data(as_text=True)[:300]
    assert resp.headers.get("Access-Control-Allow-Origin") is None
