"""Genesis-cohort attribution on the x402/USDC rail (rail rework of z2r2-w7).

Acceptance:
* a rail settle carrying a buyer_email matching a claimed genesis member is
  tagged at log time (rail_events.genesis_member=1, genesis_allocation set);
* a non-member settle is untagged;
* a wallet-registered member who pays with no email is tagged via the wallet
  (wallet is the secondary key; email is primary);
* the attribution columns are added migration-safely to a legacy rail_events
  table that lacks them (existing rows preserved, untagged by default);
* attribution never alters the recorded amount;
* GET /v1/ops/rail-revenue (admin, read-only) exposes the rail attribution;
* launch/GENESIS_RUNBOOK.md documents the rail launch: no Stripe-checkout
  steps, founder-gated items marked.

Hermetic: the "chain" is the same canned _rpc_call stub pattern as
test_rail_routes.py — zero network, zero keys, nothing broadcast.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

import sincor2.rail_routes as rr
import sincor2.platform_payments as pp
import sincor2.x402_payments as x402
from sincor2 import genesis_cohort
from sincor2.governance.money_gate import reset_money_boundary

TX_HASH = "0x" + "ab" * 32
WRONG_TX_HASH = "0x" + "cd" * 32
PAYER = "0x" + "11" * 20
STRANGER = "0x" + "33" * 20
GENESIS_EMAIL = "genesis.early@getsincor.com"
STRANGER_EMAIL = "stranger@example.com"


@pytest.fixture(autouse=True)
def _fresh_boundary():
    reset_money_boundary()
    yield
    reset_money_boundary()


@pytest.fixture()
def rail_env(tmp_path, monkeypatch):
    """Hermetic stores + stubbed RPC: no network, no real paths."""
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "sincor2.treasury_inflow._LEDGER_PATH", tmp_path / "inflow.jsonl")
    monkeypatch.setattr("sincor2.agent_billing._log_path",
                        lambda: tmp_path / "platform_payments.jsonl")

    treasury = x402.get_resource(
        "rail-pack-credentialing_launch")["treasury"]
    token_addr = pp.token_address("USDC")
    amount_atomic = 297_000_000  # $297.00 in 6-decimal USDC

    def _receipt(payer=PAYER):
        return {
            "status": "0x1",
            "blockNumber": hex(100),
            "logs": [{
                "address": token_addr,
                "topics": [
                    pp.TRANSFER_TOPIC,
                    "0x" + "0" * 24 + payer[2:],
                    "0x" + "0" * 24 + treasury[2:].lower(),
                ],
                "data": hex(amount_atomic),
            }],
        }

    def _fake_rpc(method, params, attempts=3):
        if method == "eth_getTransactionReceipt":
            if params == [TX_HASH]:
                return _receipt(PAYER)
            if params == [WRONG_TX_HASH]:
                return _receipt(STRANGER)
            raise AssertionError(f"unexpected tx hash {params}")
        if method == "eth_blockNumber":
            return hex(112)  # 13 confirmations — past finality depth
        raise AssertionError(f"unexpected RPC method {method}")

    monkeypatch.setattr(pp, "_rpc_call", _fake_rpc)
    x402.init_x402_db()
    return {"tx_hash": TX_HASH, "payer": PAYER, "treasury": treasury,
            "tmp": tmp_path}


@pytest.fixture()
def mock_comms(monkeypatch):
    """Capture outbound email + Celery queueing; nothing leaves the process."""
    sent = []
    queued = []

    def fake_send_email(self, to_email, to_name="", subject="",
                        html_content="", text_content=None, metadata=None):
        sent.append({
            "to": to_email, "to_name": to_name, "subject": subject,
            "html": html_content, "text": text_content,
            "metadata": metadata or {},
        })
        return {"status": "sent", "message_id": "msg-test-1"}

    class _FakeResult:
        id = "celery-job-test-1"

    def fake_apply_async(args=None, kwargs=None, queue=None, **kw):
        queued.append({"args": args, "kwargs": kwargs, "queue": queue})
        return _FakeResult()

    monkeypatch.setattr(
        "sincor2.email_sender.EmailSender.send_email", fake_send_email)
    monkeypatch.setattr(
        rr.fulfill_pack_agent, "apply_async", fake_apply_async)
    return {"sent": sent, "queued": queued,
            "fake_send_email": fake_send_email,
            "fake_apply_async": fake_apply_async}


@pytest.fixture(scope="module")
def rail_app():
    """Minimal Flask app with the real rail blueprint."""
    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = True
    rr.init_rail_routes(app)
    return app


def _claim_member(email=GENESIS_EMAIL, wallet="") -> dict:
    result = genesis_cohort.claim(email, "supersecret-pass-1", wallet=wallet)
    assert result["ok"], result
    return result


def _checkout(client, sku="credentialing_launch", **kw):
    body = {"pack_sku": sku, "payer_wallet": PAYER}
    body.update(kw)
    res = client.post("/rail/checkout", json=body)
    assert res.status_code == 402, res.get_data(as_text=True)
    return res.get_json()["challenge_id"]


def _settle(client, cid, **kw):
    body = {"challenge_id": cid, "tx_hash": TX_HASH, "payer_wallet": PAYER}
    body.update(kw)
    return client.post("/rail/settle", json=body)


# ---------------------------------------------------------------------------
# 1. member email -> tagged at log time; stranger -> untagged
# ---------------------------------------------------------------------------

def test_member_email_settle_is_tagged_at_log_time(rail_app, rail_env,
                                                   mock_comms):
    allocation = _claim_member()["allocation"]
    client = rail_app.test_client()
    cid = _checkout(client, buyer_email=GENESIS_EMAIL,
                    buyer_name="Genesis Buyer")

    res = _settle(client, cid, buyer_email=GENESIS_EMAIL,
                  buyer_name="Genesis Buyer")
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()["ok"] is True

    event = rr._get_rail_event(f"rail-{cid}")
    assert event is not None
    assert event["genesis_member"] == 1
    assert event["genesis_allocation"] == allocation
    # amount untouched by the tag
    assert event["amount_usdc"] == 297.0
    assert event["fulfillment_status"] == "delivered"


def test_non_member_settle_is_untagged(rail_app, rail_env, mock_comms):
    _claim_member()  # cohort exists; the stranger is not in it
    client = rail_app.test_client()
    cid = _checkout(client, buyer_email=STRANGER_EMAIL)

    res = _settle(client, cid, buyer_email=STRANGER_EMAIL)
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()["ok"] is True

    event = rr._get_rail_event(f"rail-{cid}")
    assert event is not None
    assert event["genesis_member"] == 0
    assert event["genesis_allocation"] in ("", None)
    assert event["amount_usdc"] == 297.0


# ---------------------------------------------------------------------------
# 2. wallet is the secondary key: no email, registered wallet -> tagged
# ---------------------------------------------------------------------------

def test_wallet_registered_member_with_no_email_tagged_via_wallet(
        rail_app, rail_env, mock_comms):
    allocation = _claim_member(wallet=PAYER)["allocation"]
    client = rail_app.test_client()
    cid = _checkout(client)  # no buyer_email at all

    res = _settle(client, cid)  # no buyer_email at all
    body = res.get_json()
    assert res.status_code == 200, res.get_data(as_text=True)
    assert body["ok"] is True
    # wallet-native buyer: provisioned, no email sent
    assert body["fulfillment"]["status"] == "delivered"
    assert len(mock_comms["sent"]) == 0

    event = rr._get_rail_event(f"rail-{cid}")
    assert event["genesis_member"] == 1
    assert event["genesis_allocation"] == allocation
    assert event["amount_usdc"] == 297.0


def test_email_takes_precedence_over_wallet_lookup(rail_app, rail_env,
                                                   mock_comms):
    """Email is the primary key: when the buyer_email matches a member, the
    wallet is not consulted (no cross-attribution from a shared wallet)."""
    email_member = genesis_cohort.claim(
        "email.member@getsincor.com", "supersecret-pass-1",
        wallet="0x" + "99" * 20)["allocation"]
    wallet_member = genesis_cohort.claim(
        "wallet.member@getsincor.com", "supersecret-pass-1",
        wallet=PAYER)["allocation"]
    assert email_member != wallet_member

    client = rail_app.test_client()
    cid = _checkout(client, buyer_email="email.member@getsincor.com")
    res = _settle(client, cid, buyer_email="email.member@getsincor.com")
    assert res.get_json()["ok"] is True

    event = rr._get_rail_event(f"rail-{cid}")
    assert event["genesis_member"] == 1
    assert event["genesis_allocation"] == email_member


def test_wallet_lookup_is_case_insensitive_and_miss_is_untagged():
    _claim_member(wallet=PAYER)
    assert genesis_cohort.get_member_by_wallet(PAYER.upper()) is not None
    assert genesis_cohort.get_member_by_wallet(
        "0x" + "22" * 20) is None
    assert genesis_cohort.get_member_by_wallet("") is None
    assert genesis_cohort.is_genesis_member("GENESIS.EARLY@getsincor.com")
    assert not genesis_cohort.is_genesis_member("nobody@example.com")


# ---------------------------------------------------------------------------
# 3. migration safety: legacy rail_events without the columns
# ---------------------------------------------------------------------------

_LEGACY_SCHEMA = """
CREATE TABLE rail_events (
    event_id           TEXT PRIMARY KEY,
    challenge_id       TEXT NOT NULL,
    pack_sku           TEXT NOT NULL,
    payer_wallet       TEXT NOT NULL DEFAULT '',
    tx_hash            TEXT NOT NULL DEFAULT '',
    amount_usdc        REAL NOT NULL DEFAULT 0,
    verified           INTEGER NOT NULL DEFAULT 0,
    finalized          INTEGER NOT NULL DEFAULT 0,
    fulfillment_status TEXT NOT NULL DEFAULT 'pending',
    intake_id          TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    details            TEXT NOT NULL DEFAULT '{}'
);
"""


def test_attribution_columns_migrated_onto_legacy_table(rail_env):
    """A rail_events table written before the attribution columns existed
    must gain them on connect — without touching existing rows."""
    db_path = rail_env["tmp"] / "revenue.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_LEGACY_SCHEMA)
    conn.execute(
        "INSERT INTO rail_events (event_id, challenge_id, pack_sku, "
        "amount_usdc, created_at) VALUES (?,?,?,?,?)",
        ("rail-legacy-1", "legacy-1", "credentialing_launch", 297.0,
         "2026-10-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    conn2 = rr._connect_rail()  # runs the migration
    try:
        cols = {row[1] for row in
                conn2.execute("PRAGMA table_info(rail_events)")}
        assert "genesis_member" in cols
        assert "genesis_allocation" in cols
        # legacy row preserved, defaulting to untagged
        row = conn2.execute(
            "SELECT amount_usdc, genesis_member, genesis_allocation "
            "FROM rail_events WHERE event_id='rail-legacy-1'").fetchone()
        assert row[0] == 297.0
        assert row[1] == 0
        assert row[2] in ("", None)
    finally:
        conn2.close()

    # second connect is a no-op (no duplicate-column error)
    conn3 = rr._connect_rail()
    conn3.close()


# ---------------------------------------------------------------------------
# 4. attribution never changes amounts
# ---------------------------------------------------------------------------

def test_attribution_never_alters_recorded_amount(rail_app, rail_env,
                                                  mock_comms):
    _claim_member(wallet=PAYER)
    client = rail_app.test_client()

    member_cid = _checkout(client, buyer_email=GENESIS_EMAIL)
    assert _settle(client, member_cid,
                   buyer_email=GENESIS_EMAIL).get_json()["ok"] is True

    stranger_cid = _checkout(client, payer_wallet=STRANGER,
                           buyer_email=STRANGER_EMAIL)
    assert _settle(client, stranger_cid, tx_hash=WRONG_TX_HASH,
                   payer_wallet=STRANGER,
                   buyer_email=STRANGER_EMAIL).get_json()["ok"] is True

    member = rr._get_rail_event(f"rail-{member_cid}")
    stranger = rr._get_rail_event(f"rail-{stranger_cid}")
    assert member["genesis_member"] == 1
    assert stranger["genesis_member"] == 0
    # identical amounts — the tag is who, not how much
    assert member["amount_usdc"] == stranger["amount_usdc"] == 297.0

    # the platform payment log (JSONL) carries the same amount for both
    log_path = rail_env["tmp"] / "platform_payments.jsonl"
    amounts = [float(__import__("json").loads(line)["amount_display"])
               for line in log_path.read_text().splitlines()]
    assert amounts == [297.0, 297.0]


# ---------------------------------------------------------------------------
# 5. read-only ops endpoint over rail records
# ---------------------------------------------------------------------------

def test_ops_rail_revenue_endpoint_exposes_genesis_attribution(
        rail_app, rail_env, mock_comms):
    allocation = _claim_member()["allocation"]
    client = rail_app.test_client()
    cid = _checkout(client, buyer_email=GENESIS_EMAIL)
    assert _settle(client, cid,
                   buyer_email=GENESIS_EMAIL).get_json()["ok"] is True

    from sincor2.mvp_app import app

    web = app.test_client()
    admin_key = os.environ["ADMIN_PASSWORD"]

    denied = web.get("/v1/ops/rail-revenue")
    assert denied.status_code == 401

    resp = web.get("/v1/ops/rail-revenue",
                   headers={"X-Admin-Key": admin_key})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is True
    summary = body["summary"]
    assert summary["transaction_count"] == 1
    assert summary["total_revenue"] == 297.0

    genesis = summary["genesis"]
    assert genesis["attributed_transactions"] == 1
    assert genesis["attributed_revenue"] == 297.0
    attributed = genesis["attributed_events"]
    assert len(attributed) == 1
    event = attributed[0]
    assert event["genesis_allocation"] == allocation
    assert event["payer_wallet"] == PAYER.lower()
    assert event["amount_usdc"] == 297.0


# ---------------------------------------------------------------------------
# 6. runbook: rail-only, founder-gated items marked
# ---------------------------------------------------------------------------

def _runbook_path() -> Path:
    return Path(__file__).resolve().parents[2] / "launch" / "GENESIS_RUNBOOK.md"


def test_runbook_is_rail_only_and_marks_founder_gated_items():
    content = _runbook_path().read_text(encoding="utf-8")
    # launch sequence is the x402/USDC rail
    assert "x402" in content
    assert "/rail/checkout" in content
    assert "/rail/settle" in content
    # ledger verification is documented against the rail records
    assert "rail_events" in content
    # founder action is explicit wherever it is required
    assert "FOUNDER-GATED" in content
    # Stripe checkout appears only as the replaced path, never as a step
    for line in content.splitlines():
        if "stripe" in line.lower():
            assert "replaced" in line.lower(), \
                f"unqualified Stripe reference: {line!r}"
