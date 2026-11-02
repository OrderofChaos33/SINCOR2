"""Dev-watch 308: settlement counts ONLY on status==1 AND the expected
Transfer event in the receipt logs.

Item 308 (external): an x402 facilitator's ``confirm_transaction``
reported success for transactions whose execution had failed — receipt
presence / confirmation alone was treated as settlement.

Our analog found by audit: ``POST /api/crypto/verify-payment``
(``mvp_blueprints/billing.py::crypto_verify_payment``) fulfilled orders
whenever a receipt existed with status 0x1 and ``receipt.to`` equal to
the payment address. It never inspected the receipt logs. A bare,
value-less call to the payment address (status 0x1, ``to`` == recipient,
NO Transfer event — nothing was paid) therefore released fulfillment,
while a genuine ERC-20 payment (whose receipt ``to`` is the *token
contract*, the recipient appearing only in the Transfer event) was
rejected. These tests pin the corrected rule:

  * receipt present + status 0            -> settlement FAILURE
  * receipt present + status 1, no
    matching Transfer event               -> settlement FAILURE
  * Transfer event to a different address -> settlement FAILURE
  * zero-value Transfer event             -> settlement FAILURE
  * status 1 + Transfer event to the
    payment recipient with value > 0      -> settlement SUCCESS
                                             (order stored + fulfillment)

No order row and no fulfillment call may occur on any failure path.
"""
from __future__ import annotations

import io
import json
import sqlite3
import urllib.request

import pytest
from flask import Flask

import sincor2.mvp_app  # noqa: F401  (binding side effects first)
from sincor2.mvp_blueprints import billing

RECIPIENT = "0x1111111111111111111111111111111111111111"
PAYER = "0x2222222222222222222222222222222222222222"
OTHER = "0x3333333333333333333333333333333333333333"
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)
TX_HASH = "0x" + "ab" * 32


def _padded(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:]


def _transfer_log(to: str, value: int, token: str = USDC) -> dict:
    return {
        "address": token,
        "topics": [TRANSFER_TOPIC, _padded(PAYER), _padded(to)],
        "data": hex(value),
    }


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
    """Billing blueprint on a bare app + isolated orders DB + fake RPC."""
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


def _post(client):
    return client.post("/api/crypto/verify-payment", json={
        "payment_id": "CRYPTO-TEST-308",
        "tx_hash": TX_HASH,
        "email": "buyer@example.com",
        "product_name": "DW308 Generic Product",
        "amount": 25,
    })


def _order_count(db_path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE paypal_order_id=?",
            (TX_HASH,),
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


def _assert_failure(crypto_env, resp):
    assert resp.status_code == 402, resp.get_data(as_text=True)
    assert resp.get_json().get("status") != "verified"
    assert crypto_env["fulfillments"] == []
    assert _order_count(crypto_env["db_path"]) == 0


# ---------------------------------------------------------------------------
# Failure shapes: a confirmed-but-failed / empty transfer is NEVER success
# ---------------------------------------------------------------------------

def test_reverted_receipt_is_settlement_failure(crypto_env):
    """Receipt present, status 0 (execution failed) -> failure."""
    crypto_env["state"]["receipt"] = _receipt("0x0", RECIPIENT, [])
    _assert_failure(crypto_env, _post(crypto_env["client"]))


def test_success_status_without_transfer_event_is_failure(crypto_env):
    """DW-308 core: status 0x1 + receipt addressed to the recipient but
    NO Transfer event in the logs (a bare value-less call) moved no
    tokens — it must not settle or fulfill anything."""
    crypto_env["state"]["receipt"] = _receipt("0x1", RECIPIENT, [])
    _assert_failure(crypto_env, _post(crypto_env["client"]))


def test_transfer_event_to_wrong_recipient_is_failure(crypto_env):
    """A Transfer event exists but pays someone else -> failure."""
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(OTHER, 25_000_000)])
    _assert_failure(crypto_env, _post(crypto_env["client"]))


def test_zero_value_transfer_event_is_failure(crypto_env):
    """A Transfer event to the recipient carrying zero value settles
    nothing -> failure."""
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(RECIPIENT, 0)])
    _assert_failure(crypto_env, _post(crypto_env["client"]))


def test_non_transfer_event_to_recipient_is_failure(crypto_env):
    """Some other event naming the recipient is not a payment."""
    log = {
        "address": USDC,
        "topics": ["0x" + "ff" * 32, _padded(PAYER), _padded(RECIPIENT)],
        "data": hex(25_000_000),
    }
    crypto_env["state"]["receipt"] = _receipt("0x1", USDC, [log])
    _assert_failure(crypto_env, _post(crypto_env["client"]))


# ---------------------------------------------------------------------------
# Positive control
# ---------------------------------------------------------------------------

def test_status1_with_matching_transfer_event_settles(crypto_env):
    """Status 0x1 + Transfer event to the payment recipient with value
    -> settled: order stored as completed and fulfillment triggered.
    (For an ERC-20 payment the receipt's `to` is the token contract;
    the recipient appears only in the Transfer event.)"""
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(RECIPIENT, 25_000_000)])
    resp = _post(crypto_env["client"])
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json().get("status") == "verified"
    assert _order_count(crypto_env["db_path"]) == 1
    assert len(crypto_env["fulfillments"]) == 1
