"""Dev-watch 308 — RETIREMENT CONTRACT for amount/token/`removed` binding.

History: Builder B's adversarial review of the original DW-308 fix
proved three surviving exploits end-to-end through the then-live
``POST /api/crypto/verify-payment`` handler (HTTP 200 + order inserted
+ fulfillment fired):

  1. AMOUNT NEVER CHECKED — a genuine USDC Transfer of 1 atomic unit
     fulfilled an order whose body claimed amount 50000.
  2. TOKEN CONTRACT NEVER CHECKED — a Transfer event emitted by ANY
     contract (worthless attacker token) settled.
  3. `removed` FLAG IGNORED — a log with ``removed: true`` settled.

The repair bound verify to the server-side persisted checkout
expectation (amount + token contract, summed qualifying Transfer logs,
1.5% tolerance) and this suite pinned that binding: underpayments,
wrong-token transfers, and removed logs failed with 402 and zero side
effects; genuine full-amount USDC receipts (checksummed/mixed-case
topics, split logs) settled exactly once.

RETIREMENT CONTRACT (founder-approved 2026-11-04, Wren PR 3): the
legacy routes themselves are retired. ``POST /api/crypto/checkout``
and ``POST /api/crypto/verify-payment`` now return 410 Gone with
``{"error": "use_rail_checkout"}`` unconditionally — regardless of
BASE_PAYMENT_ADDRESS or any parameters. No checkout expectation is
persisted by the route, no order row is created, no fulfillment fires,
and a checkout expectation seeded directly through the persistence
helper (the state a pre-retirement checkout would have left behind)
is never consumed.

These tests stage the SAME attack/setup shapes — the 1-atomic-unit
claim, the tolerance-boundary underpayment, the wrong-token transfer,
the removed log, the split payment, the genuine full payment — and
assert the door is closed for every one: 410, zero orders, zero
fulfillments, seeded expectation untouched. The DW-308 persistence
helpers remain in production code (unreachable via HTTP) and keep
their direct helper-level coverage in
test_devwatch308_identifier_binding.py.
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

# Expected 25_000_000 atomic; the old reference tolerance was 150 bps ->
# min acceptable was 25_000_000 * 9850 // 10000 = 24_625_000. Kept so the
# staged receipts below are byte-identical to the pre-retirement suite.
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
        "app": app,
        "client": app.test_client(),
        "state": state,
        "fulfillments": fulfillments,
        "db_path": db_path,
    }


def _checkout_retired(client, amount, currency="USDC"):
    """The checkout route itself is retired: every create attempt is
    410 and persists no expectation (asserted by the callers via
    _checkout_count)."""
    resp = client.post("/api/crypto/checkout", json={
        "currency": currency,
        "amount": amount,
        "wallet": PAYER,
    })
    assert resp.status_code == 410, resp.get_data(as_text=True)
    assert resp.get_json() == {"error": "use_rail_checkout"}
    return resp


def _seed_checkout(crypto_env, payment_id, amount_usd, expected_atomic,
                   currency="USDC", token_address=USDC,
                   product_name="DW308 Repair Product"):
    """Seed the expectation a pre-retirement checkout would have left
    behind, directly through the persistence helper (the route no
    longer persists anything)."""
    with crypto_env["app"].app_context():
        billing._persist_crypto_checkout(
            payment_id, currency, token_address, expected_atomic,
            amount_usd, PAYER, product_name)


def _checkout_record(crypto_env, payment_id):
    with crypto_env["app"].app_context():
        return billing._crypto_checkout_record(payment_id)


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
# EXPLOIT 1 — amount never checked (staged shapes, retired route)
# ---------------------------------------------------------------------------

def test_one_atomic_unit_does_not_fulfill_large_claim_retired(crypto_env):
    """B probe 1: a genuine USDC Transfer of 1 atomic unit presented
    against a claim of amount 50000. It used to be refused 402 by the
    amount binding; now the route itself is retired -> 410."""
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(RECIPIENT, 1)])
    _assert_retired(crypto_env, _verify(crypto_env["client"], "CRYPTO-X", 50000))


def test_one_unit_below_tolerance_boundary_fails_retired(crypto_env):
    """One atomic unit below the old tolerance floor: the boundary math
    no longer runs at all — the staged receipt gets 410."""
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(RECIPIENT, TOLERANCE_MIN_25 - 1)])
    _assert_retired(crypto_env, _verify(crypto_env["client"], "CRYPTO-X", 25))


def test_checkout_persisted_amount_governs_verify_retired(crypto_env):
    """The persisted-amount shape: a $40 expectation exists (seeded
    directly — the checkout route now answers 410 and persists
    nothing) and verify presents a $1 payment. The record is never
    consulted and never consumed: 410, record intact, zero orders."""
    client = crypto_env["client"]
    _checkout_retired(client, 40)
    assert _checkout_count(crypto_env["db_path"]) == 0
    _seed_checkout(crypto_env, "CRYPTO-SEEDED-40", 40, 40_000_000)

    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(RECIPIENT, 1_000_000)])
    _assert_retired(crypto_env, _verify(client, "CRYPTO-SEEDED-40", 1))
    _assert_unconsumed(crypto_env, "CRYPTO-SEEDED-40")
    record = _checkout_record(crypto_env, "CRYPTO-SEEDED-40")
    assert record["expected_atomic"] == 40_000_000


def test_underpayment_against_persisted_checkout_fails_retired(crypto_env):
    """Checkout $40 seeded; a 39_000_000 transfer (2.5% under) is
    presented. Retired: 410, the seeded record stays unconsumed."""
    client = crypto_env["client"]
    _checkout_retired(client, 40)
    _seed_checkout(crypto_env, "CRYPTO-SEEDED-40", 40, 40_000_000)

    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(RECIPIENT, 39_000_000)])
    _assert_retired(crypto_env, _verify(client, "CRYPTO-SEEDED-40", 40))
    _assert_unconsumed(crypto_env, "CRYPTO-SEEDED-40")


def test_persisted_amount_is_what_the_order_records_retired(crypto_env):
    """The exact-$40 payment against the seeded $40 expectation, with
    the verify body lying high ($99999): this USED to settle and record
    the persisted $40. Now nothing is recorded at all — 410, zero
    orders, zero fulfillments, record unconsumed."""
    client = crypto_env["client"]
    _checkout_retired(client, 40)
    _seed_checkout(crypto_env, "CRYPTO-SEEDED-40", 40, 40_000_000)

    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(RECIPIENT, 40_000_000)])
    _assert_retired(crypto_env, _verify(client, "CRYPTO-SEEDED-40", 99999))
    _assert_unconsumed(crypto_env, "CRYPTO-SEEDED-40")


# ---------------------------------------------------------------------------
# EXPLOIT 2 — token contract never checked (staged shapes, retired route)
# ---------------------------------------------------------------------------

def test_wrong_token_transfer_does_not_settle_retired(crypto_env):
    """A full-amount Transfer emitted by a worthless attacker token:
    410 retired, zero side effects."""
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", FAKE_TOKEN, [_transfer_log(RECIPIENT, 25_000_000, token=FAKE_TOKEN)])
    _assert_retired(crypto_env, _verify(crypto_env["client"], "CRYPTO-X", 25))


def test_unrelated_emitter_transfer_shaped_log_does_not_settle_retired(
        crypto_env):
    """A Transfer-shaped log emitted by a completely unrelated address
    (not a token contract at all): 410 retired."""
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", OTHER, [_transfer_log(RECIPIENT, 25_000_000, token=OTHER)])
    _assert_retired(crypto_env, _verify(crypto_env["client"], "CRYPTO-X", 25))


# ---------------------------------------------------------------------------
# EXPLOIT 3 — `removed` flag ignored (staged shape, retired route)
# ---------------------------------------------------------------------------

def test_removed_log_does_not_settle_retired(crypto_env):
    """A genuine USDC Transfer log flagged removed (reorged out):
    410 retired."""
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(RECIPIENT, 25_000_000, removed=True)])
    _assert_retired(crypto_env, _verify(crypto_env["client"], "CRYPTO-X", 25))


# ---------------------------------------------------------------------------
# Genuine-payment shapes: these USED to settle exactly once; the retired
# route settles nothing, ever — that is the contract now.
# ---------------------------------------------------------------------------

def test_split_payment_across_two_logs_settles_retired(crypto_env):
    """Two qualifying USDC logs summing to the expected amount used to
    settle the order. Retired: 410, zero orders, zero fulfillments."""
    crypto_env["state"]["receipt"] = _receipt("0x1", USDC, [
        _transfer_log(RECIPIENT, 10_000_000),
        _transfer_log(RECIPIENT, 15_000_000),
    ])
    _assert_retired(crypto_env, _verify(crypto_env["client"], "CRYPTO-X", 25))


def test_tolerance_boundary_exact_min_settles_retired(crypto_env):
    """A sum exactly at the old tolerance floor used to settle.
    Retired: 410."""
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(RECIPIENT, TOLERANCE_MIN_25)])
    _assert_retired(crypto_env, _verify(crypto_env["client"], "CRYPTO-X", 25))


def test_checksummed_token_and_mixed_case_topics_settle_retired(
        crypto_env, monkeypatch):
    """Checksummed token address and mixed-case padded topics — the
    same genuine payment that used to settle. Retired: 410."""
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
    _assert_retired(crypto_env, _verify(crypto_env["client"], "CRYPTO-X", 25))


def test_replay_protection_still_applies_retired(crypto_env):
    """A genuine full payment presented twice: there is nothing to
    replay against anymore — BOTH presentations get 410 and the totals
    stay at zero orders / zero fulfillments."""
    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(RECIPIENT, 25_000_000)])
    _assert_retired(crypto_env, _verify(crypto_env["client"], "CRYPTO-X", 25))
    _assert_retired(crypto_env, _verify(crypto_env["client"], "CRYPTO-X", 25))


def test_eth_checkout_not_settled_by_usdc_transfer_retired(crypto_env):
    """The native-ETH checkout shape: the checkout route answers 410,
    and even with an ETH expectation seeded directly, a genuine
    full-amount USDC transfer presented to verify gets 410 and the
    record stays unconsumed."""
    client = crypto_env["client"]
    _checkout_retired(client, 25, currency="ETH")
    assert _checkout_count(crypto_env["db_path"]) == 0
    # A pre-retirement ETH expectation: no ERC-20 token was bound (the
    # record's contents are never consulted by the retired route).
    _seed_checkout(crypto_env, "CRYPTO-SEEDED-ETH", 25, 0,
                   currency="ETH", token_address="")

    crypto_env["state"]["receipt"] = _receipt(
        "0x1", USDC, [_transfer_log(RECIPIENT, 25_000_000)])
    _assert_retired(crypto_env, _verify(client, "CRYPTO-SEEDED-ETH", 25))
    _assert_unconsumed(crypto_env, "CRYPTO-SEEDED-ETH")
