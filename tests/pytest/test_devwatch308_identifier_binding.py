"""Dev-watch 308 REPAIR round 3: identifier + consumption binding for
``POST /api/crypto/checkout`` / ``POST /api/crypto/verify-payment``
(``mvp_blueprints/billing.py``).

Builder B's re-check of the amount/token/removed repair CONFIRMED that
binding is sound when the checkout record is intact — but proved the
identifier design defeats it:

  S1  payment_id was ``CRYPTO-{second-resolution timestamp}`` and the
      persist used INSERT OR REPLACE: two checkouts in the same second
      collided, the later ($1) silently replaced the earlier ($40)
      expectation, and a $1 receipt then settled the $40 checkout.
  S1b product_name was caller-supplied at verify and never bound to
      the checkout; fulfillment fired for whatever product the caller
      named.
  S2  order_id used the same second-resolution pattern; the orders
      insert's IntegrityError was swallowed and fulfillment ran
      unconditionally — a second genuine payment in the same second
      fulfilled with NO order row, so re-presenting its tx later
      fulfilled AGAIN (one payment, two fulfillments).
  S3  payment_id was never consumed: one checkout settled two distinct
      full-payment transactions back-to-back.
  S4  ``_settled_transfer_total`` summed duplicate log entries: a
      receipt listing the same log twice (identical logIndex) counted
      it twice.

Pins: payment_id / order_id are unique + unguessable (timestamp prefix
kept); the persist is a plain INSERT that refuses to overwrite; the
persisted product governs fulfillment when a checkout record exists;
an order-insert failure is a 5xx with ZERO fulfillments; settlement
consumes the checkout record (settled_tx_hash) and a second verify
against it is refused 409; duplicate (transactionHash, logIndex) logs
count once while distinct logs still sum.
"""
from __future__ import annotations

import json
import sqlite3
import urllib.request
from datetime import datetime

import pytest
from flask import Flask

import sincor2.mvp_app  # noqa: F401  (binding side effects first)
from sincor2.mvp_blueprints import billing

RECIPIENT = "0x1111111111111111111111111111111111111111"
PAYER = "0x2222222222222222222222222222222222222222"
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)
TX_A = "0x" + "aa" * 32
TX_B = "0x" + "bb" * 32


def _padded(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:]


def _transfer_log(to: str, value: int, token: str = USDC, **extra) -> dict:
    log = {
        "address": token,
        "topics": [TRANSFER_TOPIC, _padded(PAYER), _padded(to)],
        "data": hex(value),
    }
    log.update(extra)
    return log


def _receipt(tx_hash: str, logs: list) -> dict:
    return {
        "status": "0x1",
        "to": USDC,
        "from": PAYER,
        "blockNumber": "0x10",
        "transactionHash": tx_hash,
        "logs": logs,
    }


class _FakeResp:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


class _FrozenDateTime(datetime):
    """Deterministic clock: every now()/utcnow() is the same second."""

    _frozen = datetime(2026, 10, 30, 12, 0, 0)

    @classmethod
    def now(cls, tz=None):
        return cls._frozen

    @classmethod
    def utcnow(cls):
        return cls._frozen


@pytest.fixture()
def frozen_time(monkeypatch):
    monkeypatch.setattr(billing, "datetime", _FrozenDateTime)


@pytest.fixture()
def crypto_env(tmp_path, monkeypatch):
    """Billing blueprint on a bare app + isolated DB + per-tx fake RPC."""
    db_path = tmp_path / "orders.db"
    monkeypatch.setattr("sincor2.mvp_app.DB_PATH", str(db_path))
    sincor2.mvp_app.init_db()
    monkeypatch.setenv("BASE_PAYMENT_ADDRESS", RECIPIENT)
    monkeypatch.setenv("BASE_RPC_URL", "http://rpc.invalid")

    fulfillments: list = []
    monkeypatch.setattr(
        billing, "trigger_fulfillment",
        lambda *a, **k: fulfillments.append(a),
    )

    state = {"receipts": {}}

    def fake_urlopen(req, timeout=None):
        url = getattr(req, "full_url", "") or ""
        if "coingecko" in url:
            return _FakeResp({"ethereum": {"usd": 3000.0}})
        body = json.loads(req.data.decode())
        assert body["method"] == "eth_getTransactionReceipt"
        tx_hash = body["params"][0]
        return _FakeResp(
            {"jsonrpc": "2.0", "id": 1, "result": state["receipts"].get(tx_hash)})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    app = Flask(__name__)
    app.register_blueprint(billing.bp)
    app.config.update(TESTING=True)
    return {
        "app": app,
        "client": app.test_client(),
        "state": state,
        "fulfillments": fulfillments,
        "db_path": db_path,
    }


def _checkout(client, amount, currency="USDC", **extra):
    body = {"currency": currency, "amount": amount, "wallet": PAYER}
    body.update(extra)
    resp = client.post("/api/crypto/checkout", json=body)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["payment_id"]


def _verify(client, payment_id, tx_hash, amount, product_name="R3 Product"):
    return client.post("/api/crypto/verify-payment", json={
        "payment_id": payment_id,
        "tx_hash": tx_hash,
        "email": "buyer@example.com",
        "product_name": product_name,
        "amount": amount,
    })


def _pay(crypto_env, tx_hash, value_atomic):
    crypto_env["state"]["receipts"][tx_hash] = _receipt(
        tx_hash, [_transfer_log(RECIPIENT, value_atomic)])


def _orders(db_path) -> list:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT order_id, paypal_order_id, product_name, amount "
            "FROM orders ORDER BY paypal_order_id",
        ).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# S1 — payment_id collision overwrite
# ---------------------------------------------------------------------------

def test_same_second_checkouts_get_distinct_payment_ids(crypto_env, frozen_time):
    """Two checkouts in the same second must NEVER share a payment_id,
    and each persisted expectation stays intact: the $1 receipt settles
    the $1 checkout and is refused (402) against the $40 checkout."""
    client = crypto_env["client"]
    pid40 = _checkout(client, 40)
    pid1 = _checkout(client, 1)
    assert pid40.startswith("CRYPTO-")
    assert pid1.startswith("CRYPTO-")
    assert pid40 != pid1

    _pay(crypto_env, TX_A, 1_000_000)
    resp = _verify(client, pid40, TX_A, 1)
    assert resp.status_code == 402, resp.get_data(as_text=True)
    assert crypto_env["fulfillments"] == []
    assert _orders(crypto_env["db_path"]) == []

    _pay(crypto_env, TX_B, 1_000_000)
    resp = _verify(client, pid1, TX_B, 1)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert len(_orders(crypto_env["db_path"])) == 1
    assert _orders(crypto_env["db_path"])[0][3] == 1
    assert len(crypto_env["fulfillments"]) == 1


def test_persist_refuses_to_overwrite_existing_checkout(crypto_env):
    """The persist is a plain INSERT: re-persisting the same payment_id
    raises instead of silently replacing the recorded expectation."""
    app = crypto_env["app"]
    with app.app_context():
        billing._persist_crypto_checkout(
            "CRYPTO-DUP-TEST", "USDC", USDC, 40_000_000, 40, PAYER)
        with pytest.raises(sqlite3.IntegrityError):
            billing._persist_crypto_checkout(
                "CRYPTO-DUP-TEST", "USDC", USDC, 1_000_000, 1, PAYER)
        record = billing._crypto_checkout_record("CRYPTO-DUP-TEST")
    assert record is not None
    assert record["expected_atomic"] == 40_000_000
    assert record["amount_usd"] == 40


# ---------------------------------------------------------------------------
# S1b — the persisted product governs fulfillment
# ---------------------------------------------------------------------------

def test_checkout_product_governs_fulfillment(crypto_env):
    """Verify naming a different product than the checkout's: the
    PERSISTED product reaches the order row and the fulfillment call,
    exactly as the persisted amount already does."""
    client = crypto_env["client"]
    pid = _checkout(client, 25, product_name="Persisted Product")
    _pay(crypto_env, TX_A, 25_000_000)
    resp = _verify(client, pid, TX_A, 25, product_name="Caller Named Product")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    orders = _orders(crypto_env["db_path"])
    assert len(orders) == 1
    assert orders[0][2] == "Persisted Product"
    # trigger_fulfillment(order_id, email, product_name, amount, ...)
    assert crypto_env["fulfillments"][0][2] == "Persisted Product"


def test_recordless_verify_product_unchanged(crypto_env):
    """Preserved behavior: with no checkout record there is nothing to
    bind, so the caller's product stands (self-consistent fallback)."""
    _pay(crypto_env, TX_A, 25_000_000)
    resp = _verify(crypto_env["client"], "CRYPTO-NOREC", TX_A, 25,
                   product_name="Caller Named Product")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert crypto_env["fulfillments"][0][2] == "Caller Named Product"


# ---------------------------------------------------------------------------
# S2 — order_id collision / swallowed insert error -> double fulfillment
# ---------------------------------------------------------------------------

def test_same_second_two_genuine_payments_each_settle_once(
        crypto_env, frozen_time):
    """Two distinct genuine full payments verified in the same second:
    BOTH get order rows and exactly one fulfillment each; re-presenting
    the second tx later is a replay refusal (409), never a third
    fulfillment."""
    client = crypto_env["client"]
    _pay(crypto_env, TX_A, 25_000_000)
    _pay(crypto_env, TX_B, 25_000_000)

    resp = _verify(client, "CRYPTO-NOREC-A", TX_A, 25)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    resp = _verify(client, "CRYPTO-NOREC-B", TX_B, 25)
    assert resp.status_code == 200, resp.get_data(as_text=True)

    orders = _orders(crypto_env["db_path"])
    assert len(orders) == 2
    assert {o[1] for o in orders} == {TX_A, TX_B}
    assert orders[0][0] != orders[1][0]  # distinct order_ids
    assert len(crypto_env["fulfillments"]) == 2

    resp = _verify(client, "CRYPTO-NOREC-B", TX_B, 25)
    assert resp.status_code == 409, resp.get_data(as_text=True)
    assert len(_orders(crypto_env["db_path"])) == 2
    assert len(crypto_env["fulfillments"]) == 2


def test_order_insert_failure_is_5xx_with_zero_fulfillments(crypto_env):
    """A forced order-insert IntegrityError must fail the request
    (5xx), log the failure, and fire ZERO fulfillments — never swallow
    the error and fulfill an order that does not exist."""
    conn = sqlite3.connect(str(crypto_env["db_path"]))
    try:
        conn.execute(
            "CREATE TRIGGER dw308_fail_orders BEFORE INSERT ON orders "
            "BEGIN SELECT RAISE(ABORT, 'forced insert failure'); END")
        conn.commit()
    finally:
        conn.close()

    _pay(crypto_env, TX_A, 25_000_000)
    resp = _verify(crypto_env["client"], "CRYPTO-NOREC", TX_A, 25)
    assert resp.status_code >= 500, resp.get_data(as_text=True)
    assert crypto_env["fulfillments"] == []
    assert _orders(crypto_env["db_path"]) == []


# ---------------------------------------------------------------------------
# S3 — settlement consumes the checkout record
# ---------------------------------------------------------------------------

def test_settlement_consumes_checkout_record(crypto_env):
    """One checkout settles exactly one transaction: after TX_A settles
    payment_id P, a second genuine full payment (TX_B) against P is
    refused 409 with no second order or fulfillment, and the record
    shows the consuming tx_hash."""
    client = crypto_env["client"]
    pid = _checkout(client, 25)

    _pay(crypto_env, TX_A, 25_000_000)
    resp = _verify(client, pid, TX_A, 25)
    assert resp.status_code == 200, resp.get_data(as_text=True)

    _pay(crypto_env, TX_B, 25_000_000)
    resp = _verify(client, pid, TX_B, 25)
    assert resp.status_code == 409, resp.get_data(as_text=True)
    assert len(_orders(crypto_env["db_path"])) == 1
    assert len(crypto_env["fulfillments"]) == 1

    # Re-presenting the settling tx itself is still a 409 refusal.
    resp = _verify(client, pid, TX_A, 25)
    assert resp.status_code == 409, resp.get_data(as_text=True)
    assert len(crypto_env["fulfillments"]) == 1

    conn = sqlite3.connect(str(crypto_env["db_path"]))
    try:
        row = conn.execute(
            "SELECT settled_tx_hash FROM crypto_checkouts "
            "WHERE payment_id=?", (pid,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == TX_A


# ---------------------------------------------------------------------------
# S4 — duplicate log entries count once
# ---------------------------------------------------------------------------

def test_duplicate_log_entry_counts_once(crypto_env):
    """The SAME log listed twice in one receipt (identical
    transactionHash + logIndex) is ONE transfer: 15M duplicated must
    not reach the 25M expectation's tolerance floor -> 402."""
    log = _transfer_log(RECIPIENT, 15_000_000,
                        transactionHash=TX_A, logIndex="0x0")
    crypto_env["state"]["receipts"][TX_A] = _receipt(TX_A, [log, dict(log)])
    resp = _verify(crypto_env["client"], "CRYPTO-NOREC", TX_A, 25)
    assert resp.status_code == 402, resp.get_data(as_text=True)
    assert crypto_env["fulfillments"] == []
    assert _orders(crypto_env["db_path"]) == []


def test_distinct_log_indexes_still_sum(crypto_env):
    """Dedup must not eat genuine split payments: two logs with
    distinct logIndex values sum to the expected amount and settle."""
    crypto_env["state"]["receipts"][TX_A] = _receipt(TX_A, [
        _transfer_log(RECIPIENT, 10_000_000,
                      transactionHash=TX_A, logIndex="0x0"),
        _transfer_log(RECIPIENT, 15_000_000,
                      transactionHash=TX_A, logIndex="0x1"),
    ])
    resp = _verify(crypto_env["client"], "CRYPTO-NOREC", TX_A, 25)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert len(_orders(crypto_env["db_path"])) == 1
    assert len(crypto_env["fulfillments"]) == 1
