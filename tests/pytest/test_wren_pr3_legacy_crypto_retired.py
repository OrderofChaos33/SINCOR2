"""Wren PR 3: legacy crypto checkout & verify routes are RETIRED.

``POST /api/crypto/checkout`` and ``POST /api/crypto/verify-payment``
(billing.py) were the legacy direct-payment path: checkout persisted a
server-side expectation, and verify fulfilled an order directly off a
caller-claimed tx_hash whenever ``BASE_PAYMENT_ADDRESS`` was set —
bypassing the rail settlement flow entirely. Both routes now return
410 Gone with ``{error: "use_rail_checkout"}`` unconditionally:

  * regardless of BASE_PAYMENT_ADDRESS being set or unset,
  * regardless of any query/body parameters (valid, invalid, missing),
  * without persisting a checkout expectation,
  * without creating an order row,
  * without triggering a fulfillment,
  * without ever consulting the chain (no RPC call).

A checkout expectation seeded directly through the persistence helper
(the state a pre-retirement checkout would have left behind) must also
remain unsettleable through the retired verify route: it stays
unconsumed, with zero orders and zero fulfillments.

The rail checkout is the replacement path; its end-to-end behavior is
pinned by its own suites (test_rail_pay.py / test_rail_routes.py),
re-run alongside this file.
"""
from __future__ import annotations

import sqlite3
import urllib.request

import pytest
from flask import Flask

import sincor2.mvp_app  # noqa: F401  (binding side effects first)
from sincor2.governance.action_catalog import ACTIONS
from sincor2.mvp_blueprints import billing
from sincor2.onchain.constants import USDC_TOKEN

RECIPIENT = "0x1111111111111111111111111111111111111111"
PAYER = "0x2222222222222222222222222222222222222222"
TX_HASH = "0x" + "ab" * 32


@pytest.fixture()
def retired_env(tmp_path, monkeypatch):
    """Billing blueprint on a bare app + isolated orders DB.

    ``trigger_fulfillment`` is captured, and any outbound HTTP call
    (the old handlers fetched prices / RPC receipts via urllib) is a
    tripwire: the retired routes must never reach the network.
    """
    db_path = tmp_path / "orders.db"
    monkeypatch.setattr("sincor2.mvp_app.DB_PATH", str(db_path))
    sincor2.mvp_app.init_db()

    fulfillments: list = []
    monkeypatch.setattr(
        billing, "trigger_fulfillment",
        lambda *a, **k: fulfillments.append(a),
    )

    def _tripwire(*a, **k):  # pragma: no cover - must never fire
        raise AssertionError("retired route attempted a network call")

    monkeypatch.setattr(urllib.request, "urlopen", _tripwire)

    app = Flask(__name__)
    app.register_blueprint(billing.bp)
    app.config.update(TESTING=True)
    return {
        "app": app,
        "client": app.test_client(),
        "fulfillments": fulfillments,
        "db_path": db_path,
    }


def _table_count(db_path, table: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            return 0
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def _assert_gone(resp):
    assert resp.status_code == 410, resp.get_data(as_text=True)
    assert resp.get_json() == {"error": "use_rail_checkout"}


def _assert_no_writes(env):
    assert env["fulfillments"] == []
    assert _table_count(env["db_path"], "orders") == 0
    assert _table_count(env["db_path"], "crypto_checkouts") == 0


def _checkout_body() -> dict:
    return {
        "currency": "USDC",
        "amount": 25,
        "wallet": PAYER,
        "product_name": "PR3 Test Product",
    }


def _verify_body() -> dict:
    return {
        "payment_id": "CRYPTO-PR3-TEST",
        "tx_hash": TX_HASH,
        "email": "buyer@example.com",
        "product_name": "PR3 Test Product",
        "amount": 25,
    }


# ---------------------------------------------------------------------------
# /api/crypto/checkout
# ---------------------------------------------------------------------------

def test_checkout_gone_when_payment_address_set(retired_env, monkeypatch):
    monkeypatch.setenv("BASE_PAYMENT_ADDRESS", RECIPIENT)
    resp = retired_env["client"].post(
        "/api/crypto/checkout", json=_checkout_body())
    _assert_gone(resp)
    _assert_no_writes(retired_env)


def test_checkout_gone_when_payment_address_unset(retired_env, monkeypatch):
    monkeypatch.delenv("BASE_PAYMENT_ADDRESS", raising=False)
    resp = retired_env["client"].post(
        "/api/crypto/checkout", json=_checkout_body())
    _assert_gone(resp)
    _assert_no_writes(retired_env)


def test_checkout_gone_regardless_of_parameters(retired_env, monkeypatch):
    """Every parameter shape that used to produce 201/400/503 is 410 now."""
    monkeypatch.setenv("BASE_PAYMENT_ADDRESS", RECIPIENT)
    client = retired_env["client"]

    _assert_gone(client.post("/api/crypto/checkout"))  # no body at all
    _assert_gone(client.post("/api/crypto/checkout", json={
        "currency": "ETH", "amount": "not-a-number", "wallet": "junk",
    }))
    _assert_gone(client.post("/api/crypto/checkout", json={
        "currency": "DOGE", "amount": -5, "wallet": PAYER,
    }))
    _assert_gone(client.post(
        "/api/crypto/checkout?amount=25&currency=USDC",
        json=_checkout_body()))
    _assert_no_writes(retired_env)


def test_checkout_never_persists_an_expectation(retired_env, monkeypatch):
    monkeypatch.setenv("BASE_PAYMENT_ADDRESS", RECIPIENT)
    client = retired_env["client"]
    for _ in range(3):
        _assert_gone(client.post("/api/crypto/checkout",
                                 json=_checkout_body()))
    _assert_no_writes(retired_env)


# ---------------------------------------------------------------------------
# /api/crypto/verify-payment
# ---------------------------------------------------------------------------

def test_verify_gone_when_payment_address_set(retired_env, monkeypatch):
    monkeypatch.setenv("BASE_PAYMENT_ADDRESS", RECIPIENT)
    resp = retired_env["client"].post(
        "/api/crypto/verify-payment", json=_verify_body())
    _assert_gone(resp)
    _assert_no_writes(retired_env)


def test_verify_gone_when_payment_address_unset(retired_env, monkeypatch):
    monkeypatch.delenv("BASE_PAYMENT_ADDRESS", raising=False)
    resp = retired_env["client"].post(
        "/api/crypto/verify-payment", json=_verify_body())
    _assert_gone(resp)
    _assert_no_writes(retired_env)


def test_verify_gone_regardless_of_parameters(retired_env, monkeypatch):
    """Bodies that used to reach validation/RPC (400/402/503) are 410."""
    monkeypatch.setenv("BASE_PAYMENT_ADDRESS", RECIPIENT)
    client = retired_env["client"]

    _assert_gone(client.post("/api/crypto/verify-payment", json={}))
    _assert_gone(client.post("/api/crypto/verify-payment", json={
        "payment_id": "X", "tx_hash": "not-a-hash",
    }))
    _assert_gone(client.post(
        "/api/crypto/verify-payment?payment_id=CRYPTO-PR3-TEST",
        json=_verify_body()))
    _assert_no_writes(retired_env)


def test_verify_cannot_settle_a_preseeded_checkout(retired_env, monkeypatch):
    """Full loop: a genuine pre-retirement checkout expectation exists in
    the store; the retired verify route must leave it unconsumed and
    produce no order and no fulfillment."""
    monkeypatch.setenv("BASE_PAYMENT_ADDRESS", RECIPIENT)
    with retired_env["app"].app_context():
        billing._persist_crypto_checkout(
            "CRYPTO-PR3-SEEDED", "USDC", USDC_TOKEN, 25_000_000, 25.0,
            PAYER, "PR3 Seeded Product",
        )
    assert _table_count(retired_env["db_path"], "crypto_checkouts") == 1

    body = _verify_body()
    body["payment_id"] = "CRYPTO-PR3-SEEDED"
    body["product_name"] = "PR3 Seeded Product"
    resp = retired_env["client"].post("/api/crypto/verify-payment",
                                      json=body)
    _assert_gone(resp)

    with retired_env["app"].app_context():
        record = billing._crypto_checkout_record("CRYPTO-PR3-SEEDED")
    assert record is not None
    assert record["settled_tx_hash"] == ""  # never consumed
    assert _table_count(retired_env["db_path"], "orders") == 0
    assert retired_env["fulfillments"] == []


# ---------------------------------------------------------------------------
# Governance catalog truthfulness
# ---------------------------------------------------------------------------

def test_action_catalog_marks_legacy_crypto_routes_retired():
    for key in ("crypto_create_checkout", "crypto_verify_payment"):
        entry = ACTIONS[key]
        assert entry["side_effects"] == "none", key
        assert entry["risk_tier"] == "low", key
        assert "410" in entry["notes"], key
        assert "use_rail_checkout" in entry["notes"], key
