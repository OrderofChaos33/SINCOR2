"""Terms §5 cancellation: paid-through access, not immediate revocation.

The published Terms (templates/terms.html §5, pricing FAQ, sales FAQ)
promise: "Cancellation takes effect at the end of your current billing
period. You will retain access until that date." The pre-fix
cancel_subscription() revoked access immediately — an
implementation-versus-contract mismatch.

Pinned here:
  * cancel mid-period stops renewal (outstanding renewal challenges
    are VOIDED, W-106 unchanged) but the subscriber keeps
    access/entitlement until the already-paid period ends;
  * at/after period end the subscription is canceled and access fails
    — a cancelled subscription does NOT slide into past_due/grace;
  * a pre-cancel challenge still cannot settle after the cancel
    (no resurrection), and the period is NOT extended by the attempt;
  * a genuinely fresh purchase after the cancel still settles;
  * cancel when the paid period has already ended is immediate
    (nothing paid remains to honor);
  * double-cancel is idempotent and never moves period_end or the
    original canceled_at.

Hermetic pattern mirrors test_rail_subscriptions.py: canned RPC stub,
tmp stores, zero network, zero keys.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import sincor2.rail_subscriptions as rs
import sincor2.rail_routes as rr
import sincor2.platform_payments as pp
import sincor2.x402_payments as x402
from sincor2.governance.money_gate import reset_money_boundary

PAYER = "0x" + "11" * 20
TX_STARTER = "0x" + "aa" * 32
TX_STARTER2 = "0x" + "ee" * 32

_AMOUNTS = {
    TX_STARTER: 297_000_000,
    TX_STARTER2: 297_000_000,
}


@pytest.fixture(autouse=True)
def _fresh_boundary():
    reset_money_boundary()
    yield
    reset_money_boundary()


@pytest.fixture()
def sub_env(tmp_path, monkeypatch):
    """Hermetic stores + stubbed RPC: no network, no real paths."""
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "sincor2.treasury_inflow._LEDGER_PATH", tmp_path / "inflow.jsonl")
    monkeypatch.setattr("sincor2.agent_billing._log_path",
                        lambda: tmp_path / "platform_payments.jsonl")

    treasury = x402.get_resource("rail-sub-starter")["treasury"]
    token_addr = pp.token_address("USDC")

    def _fake_rpc(method, params, attempts=3):
        if method == "eth_getTransactionReceipt":
            tx = params[0]
            assert tx in _AMOUNTS, f"unexpected tx {tx}"
            return {
                "status": "0x1",
                "blockNumber": hex(100),
                "logs": [{
                    "address": token_addr,
                    "topics": [
                        pp.TRANSFER_TOPIC,
                        "0x" + "0" * 24 + PAYER[2:],
                        "0x" + "0" * 24 + treasury[2:].lower(),
                    ],
                    "data": hex(_AMOUNTS[tx]),
                }],
            }
        if method == "eth_blockNumber":
            return hex(112)  # 13 confirmations — past finality depth
        raise AssertionError(f"unexpected RPC method {method}")

    monkeypatch.setattr(pp, "_rpc_call", _fake_rpc)
    x402.init_x402_db()
    return {"treasury": treasury, "tmp": tmp_path}


@pytest.fixture(scope="module")
def sub_app():
    """Minimal Flask app with the real rail blueprint."""
    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = True
    rr.init_rail_routes(app)
    return app


def _checkout(client, plan, wallet=PAYER):
    res = client.post("/rail/checkout", json={
        "subscription_plan": plan, "payer_wallet": wallet})
    assert res.status_code == 402, res.get_json()
    return res.get_json()["challenge_id"]


def _settle(client, challenge_id, tx_hash, wallet=PAYER):
    return client.post("/rail/settle", json={
        "challenge_id": challenge_id, "tx_hash": tx_hash,
        "payer_wallet": wallet})


def _set_period_end(wallet, dt):
    """White-box time travel: move a subscription's period_end."""
    conn = rs._connect()
    try:
        conn.execute(
            "UPDATE rail_subscriptions SET period_end=?, grace_until=? "
            "WHERE wallet=?",
            (dt.isoformat(),
             (dt + timedelta(days=rs.GRACE_DAYS)).isoformat(),
             wallet.lower()))
        conn.commit()
    finally:
        conn.close()


def _activate_starter(client):
    cid = _checkout(client, "starter")
    res = _settle(client, cid, TX_STARTER)
    assert res.status_code == 200, res.get_json()
    return res.get_json()["subscription"]


# ---------------------------------------------------------------------------
# The Terms behavior: access until the paid period ends, then expiry
# ---------------------------------------------------------------------------

def test_cancel_mid_period_retains_access_until_period_end(sub_app, sub_env):
    client = sub_app.test_client()
    sub = _activate_starter(client)
    period_end_before = sub["period_end"]

    out = rs.cancel_subscription(PAYER)
    # cancellation is recorded and renewal is stopped, but the paid
    # period is honored: still entitled, gate still allows.
    assert out["status"] == "cancel_pending"
    assert out["effective_plan"] == "starter"
    assert out["period_end"] == period_end_before
    assert out["period_index"] == 1
    assert rs.gate(PAYER, "starter_agents").allowed is True
    # the platform mirror agrees: still active for the paid window
    assert pp.get_subscription(PAYER)["status"] == "active"

    # still inside the paid period -> access continues
    _set_period_end(PAYER, datetime.now(timezone.utc) + timedelta(days=1))
    status = rs.subscription_status(PAYER)
    assert status["status"] == "cancel_pending"
    assert status["effective_plan"] == "starter"
    assert rs.gate(PAYER, "starter_agents").allowed is True

    # past the paid period end -> canceled, access fails. A cancelled
    # subscription must NOT slide into past_due/grace dunning access.
    _set_period_end(PAYER, datetime.now(timezone.utc) - timedelta(days=1))
    status = rs.subscription_status(PAYER)
    assert status["status"] == "canceled"
    assert status["effective_plan"] == "none"
    assert rs.gate(PAYER, "starter_agents").allowed is False
    assert rs.gate(PAYER, "toa_demo").allowed is True  # free tier survives


def test_cancel_voids_challenges_and_pre_cancel_settle_refused(
        sub_app, sub_env):
    """W-106 preserved exactly, now combined with paid-through access."""
    client = sub_app.test_client()
    sub = _activate_starter(client)
    period_end_before = sub["period_end"]

    # a still-pending renewal challenge issued BEFORE the cancel
    cid2 = _checkout(client, "starter")

    rs.cancel_subscription(PAYER)
    # the cancel voided the outstanding challenge (white-box)
    assert rs._get_issued(cid2)["voided_at"]

    res = _settle(client, cid2, TX_STARTER2)
    body = res.get_json()
    assert body["ok"] is False
    assert body["error"] == "challenge_voided_by_cancel"

    # no resurrection and no extension — but paid access remains
    status = rs.subscription_status(PAYER)
    assert status["period_end"] == period_end_before
    assert status["period_index"] == 1
    assert status["effective_plan"] == "starter"


def test_fresh_purchase_after_cancel_still_settles(sub_app, sub_env):
    client = sub_app.test_client()
    sub = _activate_starter(client)
    first_end = sub["period_end"]

    rs.cancel_subscription(PAYER)
    assert rs.subscription_status(PAYER)["status"] == "cancel_pending"

    # genuinely fresh purchase: NEW challenge issued AFTER the cancel
    cid2 = _checkout(client, "starter")
    res = _settle(client, cid2, TX_STARTER2)
    assert res.status_code == 200, res.get_json()
    status = rs.subscription_status(PAYER)
    assert status["status"] == "active"
    assert status["effective_plan"] == "starter"
    assert status["period_end"] > first_end


def test_double_cancel_idempotent_keeps_first_cancel_stamp(
        sub_app, sub_env):
    client = sub_app.test_client()
    sub = _activate_starter(client)
    period_end_before = sub["period_end"]

    first = rs.cancel_subscription(PAYER)
    first_stamp = rs._get_row(PAYER)["canceled_at"]
    assert first_stamp

    second = rs.cancel_subscription(PAYER)
    assert second["status"] == first["status"] == "cancel_pending"
    assert second["period_end"] == period_end_before
    # the original cancel time stands — a re-cancel must not move the
    # W-106 fresh-purchase dividing line
    assert rs._get_row(PAYER)["canceled_at"] == first_stamp


def test_cancel_after_paid_period_ended_is_immediate(sub_app, sub_env):
    """Nothing paid remains: cancel in the past_due window ends access
    at once (the paid period already ended)."""
    client = sub_app.test_client()
    _activate_starter(client)
    _set_period_end(PAYER, datetime.now(timezone.utc) - timedelta(days=1))
    assert rs.subscription_status(PAYER)["status"] == "past_due"

    out = rs.cancel_subscription(PAYER)
    assert out["status"] == "canceled"
    assert out["effective_plan"] == "none"
    assert rs.gate(PAYER, "starter_agents").allowed is False


def test_cancel_unknown_wallet_is_noop(sub_app, sub_env):
    out = rs.cancel_subscription(PAYER)
    assert out["status"] == "none"
    assert rs._get_row(PAYER) is None


def test_pre_cancel_challenge_still_refused_after_period_end(
        sub_app, sub_env):
    """The void does not expire with the paid period: a pre-cancel
    challenge settled after period end is still refused, and the
    subscription stays canceled (no late resurrection)."""
    client = sub_app.test_client()
    _activate_starter(client)
    cid2 = _checkout(client, "starter")
    rs.cancel_subscription(PAYER)

    _set_period_end(PAYER, datetime.now(timezone.utc) - timedelta(days=1))
    assert rs.subscription_status(PAYER)["status"] == "canceled"

    res = _settle(client, cid2, TX_STARTER2)
    assert res.get_json()["error"] == "challenge_voided_by_cancel"
    status = rs.subscription_status(PAYER)
    assert status["status"] == "canceled"
    assert status["period_index"] == 1


def test_recancel_voids_challenge_issued_after_first_cancel(
        sub_app, sub_env):
    """A fresh challenge issued during the paid-through window is
    live — until a re-cancel voids it like any other outstanding
    challenge."""
    client = sub_app.test_client()
    _activate_starter(client)
    rs.cancel_subscription(PAYER)

    cid2 = _checkout(client, "starter")
    assert not rs._get_issued(cid2)["voided_at"]

    rs.cancel_subscription(PAYER)
    assert rs._get_issued(cid2)["voided_at"]
    res = _settle(client, cid2, TX_STARTER2)
    assert res.get_json()["error"] == "challenge_voided_by_cancel"


def test_cancel_pending_gets_no_renewal_reminders(sub_app, sub_env):
    """Renewal is stopped: a cancel_pending wallet inside the renewal
    window is NOT due a renewal reminder."""
    client = sub_app.test_client()
    _activate_starter(client)
    _set_period_end(PAYER, datetime.now(timezone.utc) + timedelta(days=3))
    assert PAYER in rs.subscriptions_needing_reminder()

    rs.cancel_subscription(PAYER)
    assert PAYER not in rs.subscriptions_needing_reminder()
