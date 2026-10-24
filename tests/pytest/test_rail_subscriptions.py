"""x402/USDC rail subscriptions — periodic re-auth, no pull payments.

The x402 rail has no Stripe-Billing equivalent (no pull primitive), so a
subscription is a chain of explicit per-period re-authorizations: each
billing period the subscriber settles one fresh x402 challenge for the
plan's server-derived price. The server never charges — it only sends
renewal reminders carrying a pay link (dunning without charging).

Coverage:
  plan allowlist / server prices (ported hardening): unknown plan -> 400
      fail-closed at checkout; client-supplied prices ignored; SUB_PLANS
      agrees with the rail-sub-* yaml resources; price drift -> 500
  full loop (WIRED gate): checkout -> challenge -> settle on the stubbed
      RPC -> subscription active with period_end ~= now+30d -> the
      platform_payments subscription record is synced
  state machine: active -> past_due -> grace -> canceled pinned by tests;
      past_due keeps access (dunning), lapse revokes it via plan_gate ranks
  idempotency: double-settle of one challenge extends exactly one period
  renewal: mid-period stacks from period_end; in-grace renews continuously;
      after lapse a new period starts at payment time (no backfill)
  proration: upgrade mid-period takes effect immediately with no credit for
      unused time; downgrade mid-period is refused (downgrade_at_renewal)
      and completes on re-settle once the renewal window opens
  wallet binding: settling with a different wallet than the challenge was
      issued for fails closed; a pack challenge is refused as a subscription
  money gate: a tripped treasury halt blocks the period extension (payment
      still finalized — money moved onchain) and the retry completes it
  reminders: record_renewal_reminder returns a pay link, never a charge

The "chain" is a canned _rpc_call stub (the fork-sim analog): zero network,
zero keys, nothing broadcast. Treasury / token address / decimals come from
the repo's own modules — no addresses are invented.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import sincor2.rail_subscriptions as rs
import sincor2.rail_routes as rr
import sincor2.platform_payments as pp
import sincor2.x402_payments as x402
import sincor2.plan_gate as pg
from sincor2 import payment_ledger as pl
from sincor2.governance.money_gate import reset_money_boundary

# Canned chain values. Well-formed 0x hashes/addresses; treasury, token
# address and decimals come from the repo's own modules, never invented.
PAYER = "0x" + "11" * 20
OTHER = "0x" + "22" * 20
TX_STARTER = "0x" + "aa" * 32
TX_STARTER2 = "0x" + "ee" * 32  # second $297 payment (x402: one tx per challenge)
TX_PRO = "0x" + "bb" * 32
TX_ENT = "0x" + "cc" * 32
TX_PACK = "0x" + "dd" * 32

# tx_hash -> USDC atomic (6 decimals) the stubbed receipt "pays".
_AMOUNTS = {
    TX_STARTER: 297_000_000,
    TX_STARTER2: 297_000_000,
    TX_PRO: 997_000_000,
    TX_ENT: 2_997_000_000,
    TX_PACK: 297_000_000,
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


# --- helpers ---------------------------------------------------------------

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


def _parse(ts):
    return datetime.fromisoformat(ts)


def _approx_days(dt, days, tol_seconds=180):
    delta = abs((dt - datetime.now(timezone.utc)).total_seconds()
                - days * 86400)
    assert delta < tol_seconds, f"{dt} not ~{days}d from now"


# ---------------------------------------------------------------------------
# pricing config: SUB_PLANS agrees with config/x402_pricing.yaml
# ---------------------------------------------------------------------------

def test_sub_resources_match_sub_plans():
    assert set(rs.SUB_PLANS) == {"starter", "professional", "enterprise"}
    for plan_id, plan in rs.SUB_PLANS.items():
        resource = x402.get_resource(f"rail-sub-{plan_id}")
        assert resource is not None, f"resource missing for {plan_id}"
        assert resource["token"] == "USDC"
        expected_usdc = plan["price_cents"] / 100
        assert abs(resource["amount_usdc"] - expected_usdc) < 0.001, (
            f"{plan_id}: SUB_PLANS=${expected_usdc} != yaml "
            f"${resource['amount_usdc']}")
        # single source of truth: prices come from plan_gate's canon table
        assert plan["price_cents"] == pg.plan_price_cents(plan_id)
        assert plan["period_days"] == 30


# ---------------------------------------------------------------------------
# plan_gate hardening port (VALID_PLANS / fail-closed ranks / server prices)
# ---------------------------------------------------------------------------

def test_plan_allowlist_rejects_unknown():
    assert pg.is_valid_plan("starter") is True
    assert pg.is_valid_plan("none") is True
    assert pg.is_valid_plan("enterprise") is True
    assert pg.is_valid_plan("admin") is False
    assert pg.is_valid_plan("pro") is False
    assert pg.is_valid_plan("") is False
    assert pg.is_valid_plan(None) is False


def test_tier_ranks_fail_closed_on_legacy_aliases():
    # Legacy alias strings no longer grant paid tiers — they rank 0.
    assert pg.rank("admin") == 0
    assert pg.rank("operator") == 0
    assert pg.rank("pro") == 0
    assert pg.rank("member") == 0
    assert pg.rank("bogus") == 0
    assert pg.rank(None) == 0
    assert pg.rank("starter") == 1
    assert pg.rank("professional") == 2
    assert pg.rank("enterprise") == 3


def test_plan_prices_server_derived():
    assert pg.plan_price_cents("starter") == 29700
    assert pg.plan_price_cents("professional") == 99700
    assert pg.plan_price_cents("enterprise") == 299700
    assert pg.plan_price_cents("none") is None
    assert pg.plan_price_cents("bogus") is None


def test_plan_limits_fail_closed():
    assert pg.roster_cap("enterprise") is None  # unlimited
    assert pg.roster_cap("starter") == 10
    # unknown plans get the LOWEST tier's limits — never unlimited
    assert pg.roster_cap("bogus") == 5
    assert pg.roster_cap("admin") == 5


# ---------------------------------------------------------------------------
# checkout: subscription SKU class
# ---------------------------------------------------------------------------

def test_checkout_subscription_requires_wallet(sub_app, sub_env):
    """A subscription must attach to a wallet — fail closed without one."""
    client = sub_app.test_client()
    res = client.post("/rail/checkout", json={"subscription_plan": "starter"})
    assert res.status_code == 400
    assert res.get_json()["error"] == "subscription_wallet_required"
    res = client.post("/rail/checkout", json={
        "subscription_plan": "starter", "payer_wallet": "not-an-address"})
    assert res.status_code == 400
    assert res.get_json()["error"] == "invalid_payer_wallet"


def test_checkout_unknown_plan_400(sub_app, sub_env):
    client = sub_app.test_client()
    res = client.post("/rail/checkout",
                      json={"subscription_plan": "not_a_plan"})
    assert res.status_code == 400
    assert res.get_json()["error"].startswith("Unknown subscription_plan")


def test_checkout_pack_and_plan_mutually_exclusive_400(sub_app, sub_env):
    client = sub_app.test_client()
    res = client.post("/rail/checkout", json={
        "pack_sku": "credentialing_launch",
        "subscription_plan": "starter"})
    assert res.status_code == 400


def test_checkout_mints_challenge_with_server_price(sub_app, sub_env):
    client = sub_app.test_client()
    # A client-supplied price must be ignored: the server derives it.
    res = client.post("/rail/checkout", json={
        "subscription_plan": "starter",
        "price_cents": 1,  # tampered price — must be ignored
        "amount_usdc": 0.01,
        "payer_wallet": PAYER,
    })
    assert res.status_code == 402
    body = res.get_json()
    assert body["plan_id"] == "starter"
    assert body["plan_name"] == "SINCOR Starter"
    assert body["amount_usdc"] == 297.0
    assert body["challenge_id"]
    assert body["payment"]  # the x402 payment payload
    assert body["resource_id"] == "rail-sub-starter"
    assert body["payer_wallet"] == PAYER.lower()


def test_checkout_price_drift_fails_closed(sub_app, sub_env, monkeypatch):
    """SUB_PLANS vs YAML disagreement refuses to mint (never mis-charge)."""
    monkeypatch.setitem(rs.SUB_PLANS["starter"], "price_cents", 1)
    client = sub_app.test_client()
    res = client.post("/rail/checkout",
                      json={"subscription_plan": "starter",
                            "payer_wallet": PAYER})
    assert res.status_code == 500
    assert res.get_json()["error"] == "subscription_price_mismatch"


def test_issue_challenge_rejects_unknown_plan_and_bad_wallet(sub_env):
    with pytest.raises(ValueError, match="unknown_plan"):
        rs.issue_renewal_challenge(PAYER, "not_a_plan")
    with pytest.raises(ValueError, match="invalid_wallet"):
        rs.issue_renewal_challenge("not-an-address", "starter")


# ---------------------------------------------------------------------------
# settle: full loop (checkout -> challenge -> settle -> active subscription)
# ---------------------------------------------------------------------------

def test_full_loop_subscribe_settle_active(sub_app, sub_env):
    """WIRED gate: subscribe end to end on the stubbed chain."""
    client = sub_app.test_client()
    cid = _checkout(client, "starter")

    res = _settle(client, cid, TX_STARTER)
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["status"] == "renewed"
    assert body["plan_id"] == "starter"

    sub = body["subscription"]
    assert sub["wallet"] == PAYER.lower()
    assert sub["plan_id"] == "starter"
    assert sub["status"] == "active"
    assert sub["period_index"] == 1
    assert sub["effective_plan"] == "starter"
    _approx_days(_parse(sub["period_end"]), 30)

    # the module-level status view agrees
    status = rs.subscription_status(PAYER)
    assert status["status"] == "active"
    assert status["plan_id"] == "starter"

    # the platform-wide subscription record is synced (build on
    # platform_payments.activate_subscription)
    synced = pp.get_subscription(PAYER)
    assert synced is not None
    assert synced["plan_id"] == "starter"
    assert synced["status"] == "active"

    # the payment was finalized exactly once
    log_lines = (sub_env["tmp"] / "platform_payments.jsonl"
                 ).read_text().splitlines()
    assert len(log_lines) == 1

    # access is granted through the plan_gate rank checks
    assert rs.gate(PAYER, "starter_agents").allowed is True
    assert rs.gate(PAYER, "enterprise_agents").allowed is False


def test_double_settle_same_challenge_extends_once(sub_app, sub_env):
    """Idempotent renewal: same period paid twice -> extended once."""
    client = sub_app.test_client()
    cid = _checkout(client, "starter")
    first = _settle(client, cid, TX_STARTER)
    assert first.get_json()["ok"] is True
    first_end = first.get_json()["subscription"]["period_end"]

    second = _settle(client, cid, TX_STARTER)
    body = second.get_json()
    assert second.status_code == 200
    assert body["ok"] is True
    assert body["status"] == "already_renewed"
    assert body["duplicate"] is True
    assert body["subscription"]["period_end"] == first_end
    assert body["subscription"]["period_index"] == 1

    # still exactly one payment finalized — no double charge recorded
    log_lines = (sub_env["tmp"] / "platform_payments.jsonl"
                 ).read_text().splitlines()
    assert len(log_lines) == 1


def test_renew_mid_period_stacks_from_period_end(sub_app, sub_env):
    client = sub_app.test_client()
    cid1 = _checkout(client, "starter")
    first = _settle(client, cid1, TX_STARTER)
    first_end = _parse(first.get_json()["subscription"]["period_end"])

    # underpayment fails closed: a $297 receipt against a $997 challenge
    # settles nothing and leaves the existing subscription untouched
    cid_pro = _checkout(client, "professional")
    under = _settle(client, cid_pro, TX_STARTER)
    assert under.get_json()["ok"] is False
    assert under.get_json()["error"] == "insufficient_amount"
    assert rs.subscription_status(PAYER)["plan_id"] == "starter"

    cid2 = _checkout(client, "starter")
    second = _settle(client, cid2, TX_STARTER2)
    assert second.status_code == 200, second.get_json()
    sub = second.get_json()["subscription"]
    assert sub["period_index"] == 2
    # stacked: second period starts where the first ended
    second_end = _parse(sub["period_end"])
    assert abs((second_end - first_end).total_seconds()
               - 30 * 86400) < 180


def test_settle_wrong_wallet_fails_closed(sub_app, sub_env):
    """Wallet<->challenge binding: another wallet cannot claim the period."""
    client = sub_app.test_client()
    cid = _checkout(client, "starter", wallet=PAYER)
    res = _settle(client, cid, TX_STARTER, wallet=OTHER)
    body = res.get_json()
    assert body["ok"] is False
    assert body["error"] == "wallet_mismatch"
    assert rs.subscription_status(PAYER)["status"] == "none"
    assert rs.subscription_status(OTHER)["status"] == "none"


def test_settle_pack_challenge_refused_as_subscription(sub_app, sub_env):
    """A challenge for a non-subscription resource is refused, not applied.

    Fail-closed at the issuance binding: the pack challenge was never
    issued as a subscription, so settle_renewal refuses before any chain
    call and no subscription row is created.
    """
    client = sub_app.test_client()
    res = client.post("/rail/checkout", json={"pack_sku": "credentialing_launch",
                                              "payer_wallet": PAYER})
    cid = res.get_json()["challenge_id"]
    result = rs.settle_renewal(cid, TX_PACK, PAYER)
    assert result["ok"] is False
    assert result["error"] == "challenge_not_issued"
    assert rs.subscription_status(PAYER)["status"] == "none"


def test_settle_unknown_challenge_fails_closed(sub_app, sub_env):
    result = rs.settle_renewal("x402-nope", TX_STARTER, PAYER)
    assert result["ok"] is False
    assert result["error"] == "challenge_not_issued"


# ---------------------------------------------------------------------------
# state machine: active -> past_due -> grace -> canceled
# ---------------------------------------------------------------------------

def test_past_due_keeps_access_dunning_only(sub_app, sub_env):
    client = sub_app.test_client()
    cid = _checkout(client, "starter")
    assert _settle(client, cid, TX_STARTER).status_code == 200

    _set_period_end(PAYER, datetime.now(timezone.utc) - timedelta(days=1))
    status = rs.subscription_status(PAYER)
    assert status["status"] == "past_due"
    # dunning: access continues, nothing is charged automatically
    assert status["effective_plan"] == "starter"
    assert rs.gate(PAYER, "starter_agents").allowed is True


def test_grace_keeps_access(sub_app, sub_env):
    client = sub_app.test_client()
    cid = _checkout(client, "starter")
    assert _settle(client, cid, TX_STARTER).status_code == 200

    _set_period_end(PAYER, datetime.now(timezone.utc) - timedelta(days=5))
    status = rs.subscription_status(PAYER)
    assert status["status"] == "grace"
    assert rs.gate(PAYER, "starter_agents").allowed is True


def test_lapse_past_grace_revokes_access(sub_app, sub_env):
    client = sub_app.test_client()
    cid = _checkout(client, "starter")
    assert _settle(client, cid, TX_STARTER).status_code == 200

    _set_period_end(PAYER, datetime.now(timezone.utc) - timedelta(days=8))
    status = rs.subscription_status(PAYER)
    assert status["status"] == "canceled"
    assert status["effective_plan"] == "none"
    # access revoked through the plan_gate rank checks
    assert rs.gate(PAYER, "starter_agents").allowed is False
    assert rs.gate(PAYER, "toa_demo").allowed is True  # free tier survives


def test_renew_in_grace_no_backfill(sub_app, sub_env):
    """Renewing inside grace extends from payment time (no backfill for the
    lapsed gap) — the same max(period_end, now) rule activate_subscription
    uses, so the rail record and the platform record can never diverge."""
    client = sub_app.test_client()
    cid = _checkout(client, "starter")
    assert _settle(client, cid, TX_STARTER).status_code == 200

    old_end = datetime.now(timezone.utc) - timedelta(days=5)
    _set_period_end(PAYER, old_end)
    assert rs.subscription_status(PAYER)["status"] == "grace"

    cid2 = _checkout(client, "starter")
    res = _settle(client, cid2, TX_STARTER2)
    assert res.status_code == 200, res.get_json()
    sub = res.get_json()["subscription"]
    assert sub["status"] == "active"
    assert sub["period_index"] == 2
    _approx_days(_parse(sub["period_end"]), 30)


def test_renew_after_lapse_starts_new_period(sub_app, sub_env):
    """After lapse there is no backfill: the new period starts at payment."""
    client = sub_app.test_client()
    cid = _checkout(client, "starter")
    assert _settle(client, cid, TX_STARTER).status_code == 200

    _set_period_end(PAYER, datetime.now(timezone.utc) - timedelta(days=30))
    assert rs.subscription_status(PAYER)["status"] == "canceled"

    cid2 = _checkout(client, "starter")
    res = _settle(client, cid2, TX_STARTER2)
    assert res.status_code == 200, res.get_json()
    sub = res.get_json()["subscription"]
    assert sub["status"] == "active"
    _approx_days(_parse(sub["period_end"]), 30)


def test_explicit_cancel_revokes_immediately(sub_app, sub_env):
    client = sub_app.test_client()
    cid = _checkout(client, "starter")
    assert _settle(client, cid, TX_STARTER).status_code == 200

    out = rs.cancel_subscription(PAYER)
    assert out["status"] == "canceled"
    assert rs.subscription_status(PAYER)["effective_plan"] == "none"
    assert rs.gate(PAYER, "starter_agents").allowed is False
    # idempotent
    assert rs.cancel_subscription(PAYER)["status"] == "canceled"
    # the platform record is cancelled too
    synced = pp.get_subscription(PAYER)
    assert synced["status"] == "cancelled"


# ---------------------------------------------------------------------------
# proration: upgrades immediate, downgrades at renewal
# ---------------------------------------------------------------------------

def test_upgrade_mid_period_immediate_no_credit(sub_app, sub_env):
    client = sub_app.test_client()
    cid = _checkout(client, "starter")
    assert _settle(client, cid, TX_STARTER).status_code == 200
    # burn 10 days of the starter period
    _set_period_end(PAYER, datetime.now(timezone.utc) + timedelta(days=20))

    cid2 = _checkout(client, "professional")
    res = _settle(client, cid2, TX_PRO)
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["status"] == "upgraded"
    sub = body["subscription"]
    assert sub["plan_id"] == "professional"
    # no credit for the unused 20 starter days: new 30d period from NOW,
    # not stacked onto the old period_end
    _approx_days(_parse(sub["period_end"]), 30)
    assert rs.gate(PAYER, "professional_agents").allowed is True
    # platform record synced to the new plan
    synced = pp.get_subscription(PAYER)
    assert synced["plan_id"] == "professional"
    assert synced["status"] == "active"


def test_downgrade_mid_period_refused_until_renewal(sub_app, sub_env):
    client = sub_app.test_client()
    cid = _checkout(client, "professional")
    assert _settle(client, cid, TX_PRO).status_code == 200

    cid2 = _checkout(client, "starter")
    res = _settle(client, cid2, TX_STARTER)
    body = res.get_json()
    assert body["ok"] is False
    assert body["error"] == "downgrade_at_renewal"
    # nothing applied: still professional, still active
    status = rs.subscription_status(PAYER)
    assert status["plan_id"] == "professional"
    assert status["status"] == "active"

    # renewal window opens (<=7d before period_end): re-settling the SAME
    # challenge now completes the downgrade — no second payment needed.
    # Like upgrades, a plan change starts a fresh period at payment time
    # (no proration credit in either direction).
    _set_period_end(PAYER, datetime.now(timezone.utc) + timedelta(days=3))
    retry = _settle(client, cid2, TX_STARTER)
    assert retry.status_code == 200, retry.get_json()
    sub = retry.get_json()["subscription"]
    assert sub["plan_id"] == "starter"
    assert sub["status"] == "active"
    _approx_days(_parse(sub["period_end"]), 30)


# ---------------------------------------------------------------------------
# money gate: tripped halt blocks the extension, retry completes it
# ---------------------------------------------------------------------------

def test_money_gate_denied_blocks_extension(sub_app, sub_env, monkeypatch):
    import sincor2.governance.money_gate as mg

    monkeypatch.setattr(mg, "treasury_halt_tripped", lambda: True)
    client = sub_app.test_client()
    cid = _checkout(client, "starter")
    res = _settle(client, cid, TX_STARTER)
    body = res.get_json()
    assert body["ok"] is False
    assert body["error"] == "money_gate_denied"
    # payment WAS finalized (money moved onchain) but no period granted
    assert rs.subscription_status(PAYER)["status"] == "none"
    alerts = pl.list_alerts(sub_env["tmp"] / "revenue.db")
    assert any(a.kind == "subscription_renewal_gate_denied" for a in alerts)

    # halt clears: re-settling the same challenge completes the renewal
    monkeypatch.setattr(mg, "treasury_halt_tripped", lambda: False)
    retry = _settle(client, cid, TX_STARTER)
    assert retry.status_code == 200
    assert retry.get_json()["subscription"]["status"] == "active"
    log_lines = (sub_env["tmp"] / "platform_payments.jsonl"
                 ).read_text().splitlines()
    assert len(log_lines) == 1  # still exactly one payment record


# ---------------------------------------------------------------------------
# renewal reminders: dunning without charging
# ---------------------------------------------------------------------------

def test_renewal_reminder_is_a_pay_link_not_a_charge(sub_app, sub_env):
    client = sub_app.test_client()
    cid = _checkout(client, "starter")
    assert _settle(client, cid, TX_STARTER).status_code == 200
    before = rs.subscription_status(PAYER)

    reminder = rs.record_renewal_reminder(PAYER)
    assert reminder["wallet"] == PAYER.lower()
    assert reminder["plan_id"] == "starter"
    assert reminder["amount_usdc"] == 297.0
    assert reminder["reminder_no"] == 1
    pay = reminder["pay"]
    assert pay["method"] == "POST"
    assert pay["route"] == "/rail/checkout"
    assert pay["body"] == {"subscription_plan": "starter",
                           "payer_wallet": PAYER.lower()}

    # a reminder never charges and never moves the period
    after = rs.subscription_status(PAYER)
    assert after["period_end"] == before["period_end"]
    assert after["reminder_count"] == 1
    second = rs.record_renewal_reminder(PAYER)
    assert second["reminder_no"] == 2
    assert rs.record_renewal_reminder(OTHER) is None  # no subscription


def test_subscriptions_needing_reminder(sub_app, sub_env):
    client = sub_app.test_client()
    cid = _checkout(client, "starter")
    assert _settle(client, cid, TX_STARTER).status_code == 200
    # far from expiry: no reminder needed
    assert rs.subscriptions_needing_reminder() == []
    # inside the renewal window: reminder due
    _set_period_end(PAYER, datetime.now(timezone.utc) + timedelta(days=3))
    assert rs.subscriptions_needing_reminder() == [PAYER.lower()]
    # just reminded: suppressed for 24h
    rs.record_renewal_reminder(PAYER)
    assert rs.subscriptions_needing_reminder() == []


# ---------------------------------------------------------------------------
# route registration: subscription SKU class rides the rail blueprint
# ---------------------------------------------------------------------------

def test_subscription_routes_on_rail_blueprint(sub_app, sub_env):
    rules = {str(r) for r in sub_app.url_map.iter_rules()}
    assert "/rail/checkout" in rules
    assert "/rail/settle" in rules
    client = sub_app.test_client()
    res = client.post("/rail/checkout", json={"subscription_plan": "starter",
                                              "payer_wallet": PAYER})
    assert res.status_code == 402
    assert res.get_json()["resource_id"] == "rail-sub-starter"
