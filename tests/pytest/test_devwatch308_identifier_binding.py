"""Dev-watch 308 — RETIREMENT CONTRACT for identifier + consumption binding.

History: Builder B's re-check of the amount/token/removed repair
CONFIRMED that binding was sound when the checkout record was intact —
but proved the identifier design defeated it:

  S1  payment_id was ``CRYPTO-{second-resolution timestamp}`` and the
      persist used INSERT OR REPLACE: two checkouts in the same second
      collided and the later expectation silently replaced the earlier.
  S1b product_name was caller-supplied at verify and never bound to
      the checkout.
  S2  order_id used the same second-resolution pattern; a swallowed
      insert error let fulfillment run with NO order row, so
      re-presenting the tx fulfilled AGAIN.
  S3  payment_id was never consumed: one checkout settled two distinct
      full-payment transactions back-to-back.
  S4  ``_settled_transfer_total`` summed duplicate log entries.

The round-3 repair pinned unique unguessable ids, a plain-INSERT
persist that refuses to overwrite, persisted-product governance,
5xx-with-zero-fulfillments on order-insert failure, settlement
consuming the checkout record (second verify -> 409), and
duplicate-log dedup.

RETIREMENT CONTRACT (founder-approved 2026-11-04, Wren PR 3): the
legacy routes themselves are retired. ``POST /api/crypto/checkout``
and ``POST /api/crypto/verify-payment`` now return 410 Gone with
``{"error": "use_rail_checkout"}`` unconditionally — regardless of
BASE_PAYMENT_ADDRESS or any parameters. The route issues no
payment_ids, persists no expectations, creates no orders, fires no
fulfillments, and never consumes a checkout record.

These tests stage the SAME identifier/consumption shapes (same-second
checkouts under a frozen clock, product substitution, two genuine
payments, a forced order-insert failure, double-settlement attempts,
duplicate logs) and assert the door is closed for every one: 410,
zero orders, zero fulfillments, seeded records untouched.

The DW-308 persistence helpers remain in production code (unreachable
via HTTP); their direct behavior — in particular the plain-INSERT
refusal to overwrite — keeps its helper-level coverage below in
test_persist_refuses_to_overwrite_existing_checkout, unchanged.
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


def _checkout_retired(client, amount, currency="USDC", **extra):
    """The checkout route is retired: the create attempt itself is 410
    with the rail-redirect error body, and no payment_id is issued."""
    body = {"currency": currency, "amount": amount, "wallet": PAYER}
    body.update(extra)
    resp = client.post("/api/crypto/checkout", json=body)
    assert resp.status_code == 410, resp.get_data(as_text=True)
    assert resp.get_json() == {"error": "use_rail_checkout"}
    return resp


def _seed_checkout(crypto_env, payment_id, amount_usd, expected_atomic,
                   product_name=""):
    """Seed the expectation a pre-retirement checkout would have left
    behind, directly through the persistence helper."""
    with crypto_env["app"].app_context():
        billing._persist_crypto_checkout(
            payment_id, "USDC", USDC, expected_atomic, amount_usd,
            PAYER, product_name)


def _checkout_record(crypto_env, payment_id):
    with crypto_env["app"].app_context():
        return billing._crypto_checkout_record(payment_id)


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


def _checkout_count(db_path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='crypto_checkouts'",
        ).fetchone()
        if not exists:
            return 0
        return int(conn.execute(
            "SELECT COUNT(*) FROM crypto_checkouts").fetchone()[0])
    finally:
        conn.close()


def _assert_retired(crypto_env, resp):
    """The retirement contract on verify: 410 Gone, the exact
    rail-redirect error body, and zero side effects."""
    assert resp.status_code == 410, resp.get_data(as_text=True)
    assert resp.get_json() == {"error": "use_rail_checkout"}
    assert crypto_env["fulfillments"] == []
    assert _orders(crypto_env["db_path"]) == []


def _assert_unconsumed(crypto_env, payment_id):
    record = _checkout_record(crypto_env, payment_id)
    assert record is not None
    assert record["settled_tx_hash"] == ""  # never consumed


# ---------------------------------------------------------------------------
# S1 — payment_id collision overwrite (shape staged, route retired)
# ---------------------------------------------------------------------------

def test_same_second_checkouts_get_distinct_payment_ids_retired(
        crypto_env, frozen_time):
    """Two checkouts in the same second under a frozen clock: the S1
    collision can no longer happen because the retired route issues
    NO payment_ids at all — both creates are 410 and nothing is
    persisted. Verify attempts against the would-be colliding ids,
    with the $1 receipts staged, are likewise 410 with zero orders
    and zero fulfillments."""
    client = crypto_env["client"]
    _checkout_retired(client, 40)
    _checkout_retired(client, 1)
    assert _checkout_count(crypto_env["db_path"]) == 0

    _pay(crypto_env, TX_A, 1_000_000)
    _assert_retired(crypto_env, _verify(client, "CRYPTO-FROZEN-40", TX_A, 1))

    _pay(crypto_env, TX_B, 1_000_000)
    _assert_retired(crypto_env, _verify(client, "CRYPTO-FROZEN-1", TX_B, 1))
    assert _checkout_count(crypto_env["db_path"]) == 0


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
# S1b — the persisted product governs fulfillment (shape staged, retired)
# ---------------------------------------------------------------------------

def test_checkout_product_governs_fulfillment_retired(crypto_env):
    """A checkout record exists (seeded directly) naming "Persisted
    Product"; verify names "Caller Named Product" and presents a
    genuine full payment. Retired: 410 — no product reaches an order
    row or a fulfillment call, because neither is ever created, and
    the record stays unconsumed."""
    client = crypto_env["client"]
    _seed_checkout(crypto_env, "CRYPTO-PROD-1", 25, 25_000_000,
                   product_name="Persisted Product")
    _pay(crypto_env, TX_A, 25_000_000)
    _assert_retired(crypto_env, _verify(
        client, "CRYPTO-PROD-1", TX_A, 25,
        product_name="Caller Named Product"))
    _assert_unconsumed(crypto_env, "CRYPTO-PROD-1")


def test_recordless_verify_product_unchanged_retired(crypto_env):
    """With no checkout record, the caller-named product presentation
    that used to settle now gets 410 like everything else: zero
    orders, zero fulfillments."""
    _pay(crypto_env, TX_A, 25_000_000)
    _assert_retired(crypto_env, _verify(
        crypto_env["client"], "CRYPTO-NOREC", TX_A, 25,
        product_name="Caller Named Product"))


# ---------------------------------------------------------------------------
# S2 — order_id collision / swallowed insert error (shapes staged, retired)
# ---------------------------------------------------------------------------

def test_same_second_two_genuine_payments_each_settle_once_retired(
        crypto_env, frozen_time):
    """Two distinct genuine full payments verified in the same second:
    the double-fulfillment shape is unreachable — BOTH verifies are
    410, no order rows exist, no fulfillments fire, and re-presenting
    the second tx is another 410 (not a replay 409 against a settled
    order that was never created)."""
    client = crypto_env["client"]
    _pay(crypto_env, TX_A, 25_000_000)
    _pay(crypto_env, TX_B, 25_000_000)

    _assert_retired(crypto_env, _verify(client, "CRYPTO-NOREC-A", TX_A, 25))
    _assert_retired(crypto_env, _verify(client, "CRYPTO-NOREC-B", TX_B, 25))
    _assert_retired(crypto_env, _verify(client, "CRYPTO-NOREC-B", TX_B, 25))


def test_order_insert_failure_is_5xx_with_zero_fulfillments_retired(
        crypto_env):
    """The forced order-insert failure trigger is armed exactly as
    before — but the retired verify route never reaches the orders
    insert at all. The answer is a clean 410 (not a 5xx from a
    half-completed settlement), with zero fulfillments and zero
    orders."""
    conn = sqlite3.connect(str(crypto_env["db_path"]))
    try:
        conn.execute(
            "CREATE TRIGGER dw308_fail_orders BEFORE INSERT ON orders "
            "BEGIN SELECT RAISE(ABORT, 'forced insert failure'); END")
        conn.commit()
    finally:
        conn.close()

    _pay(crypto_env, TX_A, 25_000_000)
    _assert_retired(crypto_env, _verify(
        crypto_env["client"], "CRYPTO-NOREC", TX_A, 25))


# ---------------------------------------------------------------------------
# S3 — settlement consumes the checkout record (shape staged, retired)
# ---------------------------------------------------------------------------

def test_settlement_consumes_checkout_record_retired(crypto_env):
    """One checkout, two genuine full payments (TX_A then TX_B), plus
    a re-presentation of TX_A: consumption can never happen because
    settlement never happens — every verify is 410, and the seeded
    record's settled_tx_hash stays empty forever."""
    client = crypto_env["client"]
    _seed_checkout(crypto_env, "CRYPTO-CONSUME-1", 25, 25_000_000)

    _pay(crypto_env, TX_A, 25_000_000)
    _assert_retired(crypto_env, _verify(client, "CRYPTO-CONSUME-1", TX_A, 25))

    _pay(crypto_env, TX_B, 25_000_000)
    _assert_retired(crypto_env, _verify(client, "CRYPTO-CONSUME-1", TX_B, 25))

    _assert_retired(crypto_env, _verify(client, "CRYPTO-CONSUME-1", TX_A, 25))
    _assert_unconsumed(crypto_env, "CRYPTO-CONSUME-1")

    conn = sqlite3.connect(str(crypto_env["db_path"]))
    try:
        row = conn.execute(
            "SELECT settled_tx_hash FROM crypto_checkouts "
            "WHERE payment_id=?", ("CRYPTO-CONSUME-1",),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] in (None, "")


# ---------------------------------------------------------------------------
# S4 — duplicate log entries count once (shapes staged, retired route)
# ---------------------------------------------------------------------------

def test_duplicate_log_entry_counts_once_retired(crypto_env):
    """The SAME log listed twice in one receipt (identical
    transactionHash + logIndex) — the S4 double-count shape. Retired:
    the receipt is never even parsed; 410, zero side effects."""
    log = _transfer_log(RECIPIENT, 15_000_000,
                        transactionHash=TX_A, logIndex="0x0")
    crypto_env["state"]["receipts"][TX_A] = _receipt(TX_A, [log, dict(log)])
    _assert_retired(crypto_env, _verify(
        crypto_env["client"], "CRYPTO-NOREC", TX_A, 25))


def test_distinct_log_indexes_still_sum_retired(crypto_env):
    """Two logs with distinct logIndex values summing to the expected
    amount — the genuine split payment that used to settle. Retired:
    410, zero orders, zero fulfillments."""
    crypto_env["state"]["receipts"][TX_A] = _receipt(TX_A, [
        _transfer_log(RECIPIENT, 10_000_000,
                      transactionHash=TX_A, logIndex="0x0"),
        _transfer_log(RECIPIENT, 15_000_000,
                      transactionHash=TX_A, logIndex="0x1"),
    ])
    _assert_retired(crypto_env, _verify(
        crypto_env["client"], "CRYPTO-NOREC", TX_A, 25))
