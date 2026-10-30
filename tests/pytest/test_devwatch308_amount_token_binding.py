"""Dev-watch 308 REPAIR: amount, token-contract, and `removed` binding for
``POST /api/crypto/verify-payment`` (``mvp_blueprints/billing.py``).

Builder B's adversarial review of the original DW-308 fix proved three
surviving exploits end-to-end through the fixed handler (HTTP 200 + order
inserted + fulfillment fired):

  1. AMOUNT NEVER CHECKED — a genuine USDC Transfer of 1 atomic unit
     fulfilled an order whose body claimed amount 50000; the handler
     stored the caller-claimed amount. ``crypto_checkout`` persisted no
     expected amount server-side (payment_id was an unstored timestamp).
  2. TOKEN CONTRACT NEVER CHECKED — ``_receipt_has_payment_transfer``
     never inspected ``log['address']``; a Transfer event emitted by ANY
     contract (worthless attacker token, unrelated address) settled.
  3. `removed` FLAG IGNORED — a log with ``removed: true`` settled.

The repair pins:
  * ``crypto_checkout`` persists the expected amount + currency/token
    server-side keyed by payment_id; verify compares the SUMMED value of
    qualifying Transfer logs against it (1.5% tolerance, mirroring
    ``platform_payments.verify_treasury_transfer`` /
    ``AMOUNT_TOLERANCE_BPS``). With no persisted checkout (direct verify),
    the order's declared USD amount is the expectation.
  * Qualifying logs must be emitted by the expected token contract
    (canonical Base USDC) — via the reference parser
    ``platform_payments._parse_transfer_logs``.
  * Logs with ``removed`` truthy never qualify.

Preserved invariants from the original fix: only exact status '0x1'
passes; eventless/status-0/zero-value/wrong-recipient/non-Transfer
receipts fail with a clean 402 and zero side effects; genuine
full-amount USDC receipts (checksummed/mixed-case topics, split logs
summing to the amount) settle exactly once; native-ETH checkouts fail
closed; tx_hash replay protection still applies.

Out of scope by founder decision (unchanged): payer (`from`) binding and
confirmation-depth enforcement at this endpoint.
"""
from __future__ import annotations

import json
import sqlite3
import urllib.request

import pytest
from flask import Flask

import sincor2.mvp_app  # noqa: F401  (binding side effects first)
from sincor2.mvp_blueprints import billing

RECIPIENT = "0x1111111111111111111111111111111111111111"
RECIPIENT_MIXED = "0x" + "aA" * 20  # 40 hex chars, mixed case
PAYER = "0x2222222222222222222222222222222222222222"
OTHER = "0x3333333333333333333333333333333333333333"
FAKE_TOKEN = "0x4444444444444444444444444444444444444444"
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
USDC_CHECKSUMMED = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)
TX_HASH = "0x" + "ab" * 32

# Expected 25_000_000 atomic; reference tolerance 150 bps ->
# min acceptable = 25_000_000 * 9850 // 10000 = 24_625_000.
TOLERANCE_MIN_25 = 24_625_000


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


def _receipt(status: str, to: str, logs: list) -> dict:
    return {
        "status": status,
        "to": to,
        "from": PAYER,
        "blockNumber": "0x10",
        "transactionHash": TX_HASH,
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


@pytest.fixture()
def crypto_env(tmp_path, monkeypatch):
    """Billing blueprint on a bare app + isolated DB + fake RPC/price."""
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

    state = {"receipt": None}

    def fake_urlopen(req, timeout=None):
        url = getattr(req, "full_url", "") or ""
        if "coingecko" in url:
            return _FakeResp({"ethereum": {"usd": 3000.0}})
        body = json.loads(req.data.decode())
        assert body["method"] == "eth_getTransactionReceipt"
        assert body["params"] == [TX_HASH]
        return _FakeResp({"jsonrpc": "2.0", "id": 1, "result": state["receipt"]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    app = Flask(__name__)
    app.register_blueprint(billing.bp)
    app.config.update(TESTING=True)
    return {
        "client": app.test_client(),
        "state": state,
        "fulfillments": fulfillments,
        "db_path": db_path,
    }


def _checkout(client, amount, currency="USDC"):
    resp = client.post("/api/crypto/checkout", json={
        "currency": currency,
        "amount": amount,
        "wallet": PAYER,
    })
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["payment_id"]


def _verify(client, payment_id, amount):
    return client.post("/api/crypto/verify-payment", json={
        "payment_id": payment_id,
        "tx_hash": TX_HASH,
        "email": "buyer@example.com",
        "product_name": "DW308 Repair Product",
        "amount": amount,
    })


def _orders(db_path) -> list:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT order_id, amount FROM orders WHERE paypal_order_id=?",
            (TX_HASH,),
        ).fetchall()
    finally:
        conn.close()


def _assert_failure(crypto_env, resp):
    assert resp.status_code == 402, resp.get_data(as_text=True)
    assert resp.get_json().get("status") != "verified"
    assert crypto_env["fulfillments"] == []
    assert _orders(crypto_env["db_path"]) == []


def _assert_settled(crypto_env, resp):
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json().get("status") == "verified"
    assert len(_orders(crypto_env["db_path"])) == 1
    assert len(crypto_env["fulfillments"]) == 1


# ---------------------------------------------------------------------------
# EXPLOIT 1 — amount never checked
# ---------------------------------------------------------------------------

def test_one_atomic_unit_does_not_fulfill_large_claim(crypto_env):
    """B probe 1: a genuine USDC Transfer of 1 atomic unit must NOT
    fulfill an order claiming amount 50000."""
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(RECIPIENT, 1)])
    _assert_failure(crypto_env, _verify(crypto_env["client"], "CRYPTO-X", 50000))


def test_one_unit_below_tolerance_boundary_fails(crypto_env):
    """1.5% tolerance (reference AMOUNT_TOLERANCE_BPS): one atomic unit
    below the minimum acceptable sum is an underpayment -> 402."""
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(RECIPIENT, TOLERANCE_MIN_25 - 1)])
    _assert_failure(crypto_env, _verify(crypto_env["client"], "CRYPTO-X", 25))


def test_checkout_persisted_amount_governs_verify(crypto_env):
    """The expected amount is the one persisted at checkout, not the one
    claimed at verify: checkout $40, verify body lies low ($1) and pays
    $1 worth -> 402, zero side effects."""
    pid = _checkout(crypto_env["client"], 40)
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(RECIPIENT, 1_000_000)])
    _assert_failure(crypto_env, _verify(crypto_env["client"], pid, 1))


def test_underpayment_against_persisted_checkout_fails(crypto_env):
    """Checkout $40 (tolerance floor 39_400_000); a 39_000_000 transfer is
    2.5% under -> 402."""
    pid = _checkout(crypto_env["client"], 40)
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(RECIPIENT, 39_000_000)])
    _assert_failure(crypto_env, _verify(crypto_env["client"], pid, 40))


def test_persisted_amount_is_what_the_order_records(crypto_env):
    """Checkout $40, verify body lies high ($99999) but pays exactly $40
    -> settles, and the stored order + fulfillment carry the persisted
    $40, never the caller-claimed figure."""
    pid = _checkout(crypto_env["client"], 40)
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(RECIPIENT, 40_000_000)])
    resp = _verify(crypto_env["client"], pid, 99999)
    _assert_settled(crypto_env, resp)
    assert _orders(crypto_env["db_path"])[0][1] == 40
    # trigger_fulfillment(order_id, email, product_name, amount, ...)
    assert crypto_env["fulfillments"][0][3] == 40


# ---------------------------------------------------------------------------
# EXPLOIT 2 — token contract never checked
# ---------------------------------------------------------------------------

def test_wrong_token_transfer_does_not_settle(crypto_env):
    """A full-amount Transfer emitted by a worthless attacker token is
    not a USDC payment -> 402."""
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", FAKE_TOKEN, [_transfer_log(RECIPIENT, 25_000_000, token=FAKE_TOKEN)])
    _assert_failure(crypto_env, _verify(crypto_env["client"], "CRYPTO-X", 25))


def test_unrelated_emitter_transfer_shaped_log_does_not_settle(crypto_env):
    """A Transfer-shaped log emitted by a completely unrelated address
    (not a token contract at all) must not settle."""
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", OTHER, [_transfer_log(RECIPIENT, 25_000_000, token=OTHER)])
    _assert_failure(crypto_env, _verify(crypto_env["client"], "CRYPTO-X", 25))


# ---------------------------------------------------------------------------
# EXPLOIT 3 — `removed` flag ignored
# ---------------------------------------------------------------------------

def test_removed_log_does_not_settle(crypto_env):
    """A genuine USDC Transfer log flagged removed (reorged out) is not
    settlement -> 402."""
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(RECIPIENT, 25_000_000, removed=True)])
    _assert_failure(crypto_env, _verify(crypto_env["client"], "CRYPTO-X", 25))


# ---------------------------------------------------------------------------
# Invariants preserved: genuine payments still settle exactly once
# ---------------------------------------------------------------------------

def test_split_payment_across_two_logs_settles(crypto_env):
    """Two qualifying USDC logs whose values SUM to the expected amount
    settle the order."""
    crypto_env["state"]["receipt"] = _receipt("0x1", USDC, [
        _transfer_log(RECIPIENT, 10_000_000),
        _transfer_log(RECIPIENT, 15_000_000),
    ])
    _assert_settled(crypto_env, _verify(crypto_env["client"], "CRYPTO-X", 25))


def test_tolerance_boundary_exact_min_settles(crypto_env):
    """A sum exactly at the 1.5%-tolerance floor settles (mirrors the
    reference's >= min_amount comparison)."""
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(RECIPIENT, TOLERANCE_MIN_25)])
    _assert_settled(crypto_env, _verify(crypto_env["client"], "CRYPTO-X", 25))


def test_checksummed_token_and_mixed_case_topics_settle(crypto_env, monkeypatch):
    """Checksummed token address and mixed-case padded topics are the
    same payment: address/topic comparison is case-insensitive."""
    monkeypatch.setenv("BASE_PAYMENT_ADDRESS", RECIPIENT_MIXED)
    log = {
        "address": USDC_CHECKSUMMED,
        "topics": [
            TRANSFER_TOPIC.upper().replace("0X", "0x"),
            _padded(PAYER),
            _padded(RECIPIENT_MIXED),
        ],
        "data": hex(25_000_000),
    }
    crypto_env["state"]["receipt"] = _receipt("0x1", USDC_CHECKSUMMED, [log])
    _assert_settled(crypto_env, _verify(crypto_env["client"], "CRYPTO-X", 25))


def test_replay_protection_still_applies(crypto_env):
    """A genuine full payment settles once; re-presenting the same
    tx_hash is refused 409 with no second order or fulfillment."""
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(RECIPIENT, 25_000_000)])
    _assert_settled(crypto_env, _verify(crypto_env["client"], "CRYPTO-X", 25))
    resp = _verify(crypto_env["client"], "CRYPTO-X", 25)
    assert resp.status_code == 409, resp.get_data(as_text=True)
    assert len(_orders(crypto_env["db_path"])) == 1
    assert len(crypto_env["fulfillments"]) == 1


def test_eth_checkout_not_settled_by_usdc_transfer(crypto_env):
    """A native-ETH checkout binds no ERC-20 token: even a genuine
    full-amount USDC transfer cannot settle it — the endpoint fails
    closed for native value, as before."""
    pid = _checkout(crypto_env["client"], 25, currency="ETH")
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(RECIPIENT, 25_000_000)])
    _assert_failure(crypto_env, _verify(crypto_env["client"], pid, 25))
