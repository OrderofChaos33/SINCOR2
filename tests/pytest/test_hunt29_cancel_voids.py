"""Hunt-29 W-106 regression: cancel must void outstanding challenges.

Bug (CONFIRMED, red-team hunt-29): cancel_subscription() flipped the
rail subscription row to "canceled" but never voided outstanding
rail_sub_challenges rows, and settle_renewal never checked subscription
status. A challenge issued BEFORE the cancel could settle AFTER it and
_apply_period's upsert forced status='active' — a canceled subscription
was resurrected with no new payment.

  Variant A: settle starter -> issue a second (pending) starter
      challenge -> cancel -> settle the pre-cancel challenge ->
      previously "renewed", status back to active, period extended.
  Variant B (sharper): enterprise active -> starter downgrade refused
      mid-period (downgrade_at_renewal, money already moved on-chain)
      -> cancel -> re-settle the same challenge -> previously
      "downgraded", status active as starter.

Fix pinned here: cancel voids outstanding challenges and settle refuses
pre-cancel challenges against a canceled row, while legitimate flows
keep working — normal renewals, and a genuinely fresh post-cancel
purchase (new challenge issued after the cancel, new payment).

These tests exercise the real settle path end to end (HTTP checkout ->
HTTP settle -> cancel -> settle attempt) on the stubbed RPC, mirroring
tests/pytest/test_rail_subscriptions.py's hermetic fixture style. No
imports from red-team scratch.
"""
from __future__ import annotations

import threading

import pytest

import sincor2.rail_subscriptions as rs
import sincor2.rail_routes as rr
import sincor2.platform_payments as pp
import sincor2.x402_payments as x402
from sincor2.governance.money_gate import reset_money_boundary

PAYER = "0x" + "11" * 20
TX_STARTER = "0x" + "aa" * 32
TX_STARTER2 = "0x" + "ee" * 32  # second $297 payment (one tx per challenge)
TX_ENT = "0x" + "cc" * 32

_AMOUNTS = {
    TX_STARTER: 297_000_000,
    TX_STARTER2: 297_000_000,
    TX_ENT: 2_997_000_000,
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


# ---------------------------------------------------------------------------
# Variant A: pending pre-cancel challenge must not resurrect after cancel
# ---------------------------------------------------------------------------

def test_variant_a_pre_cancel_challenge_cannot_settle_after_cancel(
        sub_app, sub_env):
    # STALE update (founder-authorized 2026-11-08): the post-cancel
    # assertions pinned the old immediate-revocation behavior (status
    # "canceled", effective_plan "none", gate denied at once). Under
    # the Terms §5 paid-through fix (9018cbb3) a mid-period cancel
    # records "cancel_pending" and the paid plan runs to period_end.
    # The W-106 invariant is unchanged and still asserted in full:
    # the pre-cancel challenge is voided, its settle is refused with
    # challenge_voided_by_cancel, and the period is NOT extended.
    client = sub_app.test_client()
    cid1 = _checkout(client, "starter")
    first = _settle(client, cid1, TX_STARTER)
    assert first.status_code == 200, first.get_json()
    assert first.get_json()["status"] == "renewed"
    period_end_before = first.get_json()["subscription"]["period_end"]

    # a second, still-pending renewal challenge issued BEFORE the cancel
    cid2 = _checkout(client, "starter")

    out = rs.cancel_subscription(PAYER)
    assert out["status"] == "cancel_pending"
    # the cancel voided the outstanding challenge (white-box)
    assert rs._get_issued(cid2)["voided_at"]

    res = _settle(client, cid2, TX_STARTER2)
    body = res.get_json()
    assert body["ok"] is False
    assert body["error"] == "challenge_voided_by_cancel"

    # no resurrection: the voided settle did not reactivate the row or
    # extend the period — the subscription stays in its paid-through
    # cancel_pending state, with the originally paid access intact
    status = rs.subscription_status(PAYER)
    assert status["status"] == "cancel_pending"
    assert status["effective_plan"] == "starter"
    assert status["period_end"] == period_end_before
    assert status["period_index"] == 1
    assert rs.gate(PAYER, "starter_agents").allowed is True


# ---------------------------------------------------------------------------
# Variant B: refused downgrade must not complete after a cancel
# ---------------------------------------------------------------------------

def test_variant_b_refused_downgrade_cannot_complete_after_cancel(
        sub_app, sub_env):
    # STALE update (founder-authorized 2026-11-08): the post-cancel
    # assertions pinned the old immediate-revocation behavior (status
    # "canceled", effective_plan "none"). Under the Terms §5
    # paid-through fix (9018cbb3) a mid-period cancel records
    # "cancel_pending" and the originally paid plan (enterprise) runs
    # to period_end. The W-106 invariant is unchanged: re-settling the
    # refused downgrade challenge is still refused with
    # challenge_voided_by_cancel and no downgrade is applied.
    client = sub_app.test_client()
    cid_ent = _checkout(client, "enterprise")
    first = _settle(client, cid_ent, TX_ENT)
    assert first.status_code == 200, first.get_json()
    period_end_before = first.get_json()["subscription"]["period_end"]

    # mid-period downgrade: refused, but the payment already moved
    # on-chain and the settle stays claimed-but-incomplete
    cid_down = _checkout(client, "starter")
    refused = _settle(client, cid_down, TX_STARTER)
    assert refused.get_json()["ok"] is False
    assert refused.get_json()["error"] == "downgrade_at_renewal"
    assert rs.subscription_status(PAYER)["plan_id"] == "enterprise"

    rs.cancel_subscription(PAYER)
    assert rs.subscription_status(PAYER)["status"] == "cancel_pending"

    # re-settling the same challenge after the cancel must NOT complete
    # the downgrade and must NOT resurrect the subscription
    retry = _settle(client, cid_down, TX_STARTER)
    body = retry.get_json()
    assert body["ok"] is False
    assert body["error"] == "challenge_voided_by_cancel"

    status = rs.subscription_status(PAYER)
    assert status["status"] == "cancel_pending"
    assert status["plan_id"] == "enterprise"  # no downgrade applied
    assert status["effective_plan"] == "enterprise"  # paid plan honored
    assert status["period_end"] == period_end_before


# ---------------------------------------------------------------------------
# Legitimate flows keep working
# ---------------------------------------------------------------------------

def test_normal_renewal_without_cancel_still_settles(sub_app, sub_env):
    client = sub_app.test_client()
    cid1 = _checkout(client, "starter")
    assert _settle(client, cid1, TX_STARTER).status_code == 200

    cid2 = _checkout(client, "starter")
    res = _settle(client, cid2, TX_STARTER2)
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["ok"] is True
    assert body["status"] == "renewed"
    assert body["subscription"]["status"] == "active"
    assert body["subscription"]["period_index"] == 2
    # the renewal challenge was never voided
    assert not rs._get_issued(cid2)["voided_at"]


def test_fresh_purchase_after_cancel_settles_and_activates(
        sub_app, sub_env):
    # STALE update (founder-authorized 2026-11-08): the intermediate
    # assertion pinned the old immediate-revocation status "canceled"
    # after a mid-period cancel. Under the Terms §5 paid-through fix
    # (9018cbb3) that intermediate state is "cancel_pending". The
    # legitimate-flow invariant is unchanged: a genuinely fresh
    # purchase (new challenge issued AFTER the cancel) still settles
    # and reactivates the subscription, asserted in full below.
    client = sub_app.test_client()
    cid1 = _checkout(client, "starter")
    first = _settle(client, cid1, TX_STARTER)
    assert first.status_code == 200, first.get_json()
    canceled_end = first.get_json()["subscription"]["period_end"]

    rs.cancel_subscription(PAYER)
    assert rs.subscription_status(PAYER)["status"] == "cancel_pending"

    # genuinely fresh purchase: NEW challenge issued AFTER the cancel,
    # settled with a NEW payment — must work and re-activate
    cid2 = _checkout(client, "starter")
    assert not rs._get_issued(cid2)["voided_at"]
    res = _settle(client, cid2, TX_STARTER2)
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["ok"] is True
    assert body["status"] == "renewed"

    status = rs.subscription_status(PAYER)
    assert status["status"] == "active"
    assert status["effective_plan"] == "starter"
    assert status["period_index"] == 2
    # same-plan rule: the new period stacks from the prior period_end
    # (renewing never loses paid time) — it moved, it did not reset
    assert status["period_end"] > canceled_end
    assert rs.gate(PAYER, "starter_agents").allowed is True


def test_module_level_settle_of_pre_cancel_challenge_refused(
        sub_app, sub_env):
    """Same W-106 path without HTTP: issue -> settle -> cancel -> settle."""
    # STALE update (founder-authorized 2026-11-08): the final assertion
    # pinned the old immediate-revocation status "canceled". Under the
    # Terms §5 paid-through fix (9018cbb3) a mid-period cancel records
    # "cancel_pending". The refusal invariant is unchanged: the
    # pre-cancel challenge is still refused with
    # challenge_voided_by_cancel, and the paid period is not extended.
    ch = rs.issue_renewal_challenge(PAYER, "starter")
    r = rs.settle_renewal(ch["challenge_id"], TX_STARTER, PAYER)
    assert r["ok"] is True, r
    period_end_before = r["subscription"]["period_end"]

    ch2 = rs.issue_renewal_challenge(PAYER, "starter")
    rs.cancel_subscription(PAYER)

    r2 = rs.settle_renewal(ch2["challenge_id"], TX_STARTER2, PAYER)
    assert r2["ok"] is False
    assert r2["error"] == "challenge_voided_by_cancel"
    status = rs.subscription_status(PAYER)
    assert status["status"] == "cancel_pending"
    assert status["effective_plan"] == "starter"
    assert status["period_end"] == period_end_before
    assert status["period_index"] == 1


# ---------------------------------------------------------------------------
# TOCTOU (Fix Round 2): cancel landing DURING an in-flight settle
#
# Builder B rejected abb71aed because settle_renewal ran the W-106
# void/cancel check only once, at the top, before verify_challenge
# (on-chain RPC), finalize, and the money gate — and never re-checked
# before _apply_period. A cancel landing in that window voided the
# challenge in the DB, yet the in-flight settle still applied the
# period and overwrote the subscription to active (resurrection).
# The fix re-validates the void/cancel state immediately before
# _apply_period (after the money gate). These orchestrated tests pin
# the two deterministic reproductions (R1 / R2).
# ---------------------------------------------------------------------------

def test_race_r1_cancel_during_verify_blocks_settle(
        sub_app, sub_env, monkeypatch):
    """R1: settle blocked inside verify_challenge while cancel lands."""
    # STALE update (founder-authorized 2026-11-08): the cancel-return
    # and final assertions pinned the old immediate-revocation
    # behavior ("canceled" / effective "none" / gate denied). Under
    # the Terms §5 paid-through fix (9018cbb3) the mid-period cancel
    # records "cancel_pending" and the paid plan runs to period_end.
    # The TOCTOU invariant is unchanged: the in-flight settle is still
    # blocked with challenge_voided_by_cancel and the period is NOT
    # extended.
    ch = rs.issue_renewal_challenge(PAYER, "starter")
    r = rs.settle_renewal(ch["challenge_id"], TX_STARTER, PAYER)
    assert r["ok"] is True, r
    period_end_before = r["subscription"]["period_end"]

    ch2 = rs.issue_renewal_challenge(PAYER, "starter")

    orig_verify = x402.verify_challenge
    entered = threading.Event()
    release = threading.Event()

    def _blocking_verify(cid, tx, **kwargs):
        entered.set()
        assert release.wait(timeout=10), "test orchestration timed out"
        return orig_verify(cid, tx, **kwargs)

    monkeypatch.setattr(x402, "verify_challenge", _blocking_verify)

    holder = {}

    def _run_settle():
        holder["result"] = rs.settle_renewal(
            ch2["challenge_id"], TX_STARTER2, PAYER)

    thread = threading.Thread(target=_run_settle)
    thread.start()
    try:
        assert entered.wait(timeout=10), "settle never reached verify"
        # The cancel lands while the settle is in flight (post top-check,
        # blocked at the on-chain verify seam).
        out = rs.cancel_subscription(PAYER)
        assert out["status"] == "cancel_pending"
        assert rs._get_issued(ch2["challenge_id"])["voided_at"]
    finally:
        release.set()
    thread.join(timeout=10)
    assert not thread.is_alive(), "settle thread did not finish"

    result = holder["result"]
    assert result["ok"] is False, result
    assert result["error"] == "challenge_voided_by_cancel"

    # no resurrection: the blocked settle did not reactivate the row
    # or extend the period — still paid-through cancel_pending
    status = rs.subscription_status(PAYER)
    assert status["status"] == "cancel_pending"
    assert status["effective_plan"] == "starter"
    assert status["period_end"] == period_end_before
    assert status["period_index"] == 1


def test_race_r2_cancel_at_money_gate_blocks_settle(
        sub_app, sub_env, monkeypatch):
    """R2: cancel lands at the money gate, immediately before apply."""
    # STALE update (founder-authorized 2026-11-08): the embedded cancel
    # assertion inside the money-gate callback and the final assertions
    # pinned the old immediate-revocation behavior ("canceled" /
    # effective "none"). Under the Terms §5 paid-through fix
    # (9018cbb3) the mid-period cancel records "cancel_pending". The
    # TOCTOU invariant is unchanged: the re-check before _apply_period
    # still blocks the settle with challenge_voided_by_cancel and the
    # period is NOT extended.
    ch = rs.issue_renewal_challenge(PAYER, "starter")
    r = rs.settle_renewal(ch["challenge_id"], TX_STARTER, PAYER)
    assert r["ok"] is True, r
    period_end_before = r["subscription"]["period_end"]

    ch2 = rs.issue_renewal_challenge(PAYER, "starter")

    def _cancel_at_gate(**kwargs):
        # The last step before _apply_period: a cancel arriving here
        # must still be honored by a re-check before the period applies.
        out = rs.cancel_subscription(PAYER)
        assert out["status"] == "cancel_pending"
        return True

    monkeypatch.setattr(rs, "_sub_money_gate_allows", _cancel_at_gate)

    result = rs.settle_renewal(ch2["challenge_id"], TX_STARTER2, PAYER)
    assert result["ok"] is False, result
    assert result["error"] == "challenge_voided_by_cancel"

    # no resurrection: the blocked settle did not reactivate the row
    # or extend the period — still paid-through cancel_pending
    status = rs.subscription_status(PAYER)
    assert status["status"] == "cancel_pending"
    assert status["effective_plan"] == "starter"
    assert status["period_end"] == period_end_before
    assert status["period_index"] == 1


def test_already_renewed_ack_survives_cancel_no_resurrection(
        sub_app, sub_env):
    """P11 preserved: re-settling an already-extended challenge after a
    cancel is an ack-only duplicate — ok/already_renewed, no period
    applied, no resurrection."""
    # STALE update (founder-authorized 2026-11-08): the status
    # assertions pinned the old immediate-revocation "canceled" after a
    # mid-period cancel. Under the Terms §5 paid-through fix
    # (9018cbb3) that state is "cancel_pending". The ack carve-out
    # invariant is unchanged: the duplicate settle is still ack-only
    # (ok / already_renewed / duplicate), applies no period, and does
    # not reactivate the row.
    ch = rs.issue_renewal_challenge(PAYER, "starter")
    r = rs.settle_renewal(ch["challenge_id"], TX_STARTER, PAYER)
    assert r["ok"] is True, r
    period_end_before = r["subscription"]["period_end"]

    rs.cancel_subscription(PAYER)
    assert rs.subscription_status(PAYER)["status"] == "cancel_pending"

    dup = rs.settle_renewal(ch["challenge_id"], TX_STARTER, PAYER)
    assert dup["ok"] is True, dup
    assert dup["status"] == "already_renewed"
    assert dup["duplicate"] is True

    status = rs.subscription_status(PAYER)
    assert status["status"] == "cancel_pending"
    assert status["effective_plan"] == "starter"
    assert status["period_index"] == 1
    assert status["period_end"] == period_end_before


def test_settle_refuses_when_stored_status_is_messy_canceled(
        sub_app, sub_env):
    """Hardening: the settle-side canceled check is case/whitespace
    normalized — a stored ' Canceled ' must still refuse a pre-cancel
    challenge."""
    ch = rs.issue_renewal_challenge(PAYER, "starter")
    r = rs.settle_renewal(ch["challenge_id"], TX_STARTER, PAYER)
    assert r["ok"] is True, r
    period_end_before = r["subscription"]["period_end"]

    ch2 = rs.issue_renewal_challenge(PAYER, "starter")

    # White-box messy cancel: non-canonical stored status spelling,
    # stamped after ch2 was issued, and ch2 deliberately NOT voided
    # (legacy-row belt-and-braces path).
    conn = rs._connect()
    try:
        conn.execute(
            "UPDATE rail_subscriptions SET status=' Canceled ', "
            "canceled_at=? WHERE wallet=?",
            (rs._utc_now_iso(), PAYER.lower()))
        conn.commit()
    finally:
        conn.close()

    result = rs.settle_renewal(ch2["challenge_id"], TX_STARTER2, PAYER)
    assert result["ok"] is False, result
    assert result["error"] == "challenge_voided_by_cancel"
    row = rs._get_row(PAYER)
    assert row["period_index"] == 1
    assert row["period_end"] == period_end_before


# ---------------------------------------------------------------------------
# Platform-mirror divergence (delta re-review probe N1): cancel landing
# WHILE _sync_platform is in flight. The rail row (authoritative) and the
# gate stay correct, but the released sync called activate_subscription
# AFTER the cancel had cancelled the platform row, inserting a NEW
# active platform row that nothing reconciled — the platform mirror
# showed an active subscription for a canceled rail subscription.
# Fix pinned here: _sync_platform re-reads the rail state immediately
# before activating (skip when canceled) and again after the sync
# completes (re-apply the platform cancel when now canceled).
# ---------------------------------------------------------------------------

def _platform_active_rows():
    return [s for s in pp.list_subscriptions(PAYER)
            if s["status"] == "active"]


def test_race_n1_cancel_during_platform_sync_reconciles_mirror(
        sub_app, sub_env, monkeypatch):
    """N1: settle blocked inside the platform sync while cancel lands."""
    # STALE semantics rewrite (founder-authorized 2026-11-08): this
    # test pinned the old immediate-revocation interleave — cancel
    # during the sync cancelled the rail row AND the platform mirror,
    # and the post-sync reconciliation had to re-cancel the mirror so
    # NO active platform row survived. That is no longer the invariant
    # for a mid-period cancel: under the Terms §5 paid-through fix
    # (9018cbb3) the renewal period was already applied and paid when
    # the cancel lands, so the cancel records "cancel_pending" and the
    # invariant is rail/mirror AGREEMENT — both stores paid-through
    # (active plan) to the NEW period end. The reconciliation duty is
    # unchanged in kind: the released sync must finish mirroring the
    # applied renewal exactly once, and the post-sync re-check must
    # NOT cancel a mirror that agrees with a cancel_pending rail row
    # (the terminal-"canceled" skip/re-cancel paths in _sync_platform
    # key off the stored terminal status only).
    ch = rs.issue_renewal_challenge(PAYER, "starter")
    r = rs.settle_renewal(ch["challenge_id"], TX_STARTER, PAYER)
    assert r["ok"] is True, r
    assert len(_platform_active_rows()) == 1
    period_end_before = r["subscription"]["period_end"]

    ch2 = rs.issue_renewal_challenge(PAYER, "starter")

    # Hook re-pointed 2026-10-26 (founder-approved): _sync_platform now
    # routes through activate_subscription_idempotent (B3's per-payment
    # journal entry point) and never calls activate_subscription, so the
    # race is orchestrated at the idempotent entry. Assertions unchanged.
    orig_activate = pp.activate_subscription_idempotent
    entered = threading.Event()
    release = threading.Event()

    def _blocking_activate(**kwargs):
        entered.set()
        assert release.wait(timeout=10), "test orchestration timed out"
        return orig_activate(**kwargs)

    monkeypatch.setattr(pp, "activate_subscription_idempotent",
                        _blocking_activate)

    holder = {}

    def _run_settle():
        holder["result"] = rs.settle_renewal(
            ch2["challenge_id"], TX_STARTER2, PAYER)

    thread = threading.Thread(target=_run_settle)
    thread.start()
    try:
        assert entered.wait(timeout=10), "settle never reached the sync"
        # The cancel lands while the sync is in flight. The renewal
        # period was already applied (_apply_period commits before the
        # sync), so this is a mid-period cancel of the NEW period:
        # rail row -> cancel_pending, and the platform mirror is NOT
        # cancelled — the existing active row stays for the paid window.
        out = rs.cancel_subscription(PAYER)
        assert out["status"] == "cancel_pending"
        assert len(_platform_active_rows()) == 1
    finally:
        release.set()
    thread.join(timeout=10)
    assert not thread.is_alive(), "settle thread did not finish"
    assert holder["result"]["ok"] is True, holder["result"]

    # The released sync completed the mirror for the renewal payment,
    # and the post-sync reconciliation left it in AGREEMENT with the
    # rail row: exactly one active platform row, activated by the
    # renewal challenge (not a stale row, not a cancelled one).
    active = _platform_active_rows()
    assert len(active) == 1
    assert active[0]["last_payment_id"] == ch2["challenge_id"]
    for row in pp.list_subscriptions(PAYER):
        assert row["status"] == "active"

    # Authoritative store: paid-through to the NEW (renewal) period
    # end — the period the cancel honored — not the pre-renewal end.
    status = rs.subscription_status(PAYER)
    assert status["status"] == "cancel_pending"
    assert status["effective_plan"] == "starter"
    assert status["period_index"] == 2
    assert status["period_end"] > period_end_before
    assert rs.gate(PAYER, "starter_agents").allowed is True


def test_cancel_before_platform_sync_still_mirrors_paid_through(
        sub_app, sub_env, monkeypatch):
    """Simpler ordering: cancel completes BEFORE the sync step begins."""
    # STALE semantics rewrite (founder-authorized 2026-11-08): this
    # test pinned the old immediate-revocation interleave — the cancel
    # cancelled both stores and the sync, seeing a canceled rail row,
    # skipped activation entirely. Under the Terms §5 paid-through fix
    # (9018cbb3) the renewal period was already applied and paid when
    # the cancel completes, so the cancel records "cancel_pending" and
    # the invariant is rail/mirror AGREEMENT: the sync must NOT skip —
    # it mirrors the applied renewal so the platform row is active for
    # exactly the paid-through window the rail row honors. (The
    # terminal-"canceled" skip in _sync_platform keys off the stored
    # terminal status only and does not fire for cancel_pending.)
    # The activation spy is also re-pointed: the sync routes through
    # activate_subscription_idempotent and never calls
    # activate_subscription (see the N1 hook note above), so the old
    # spy could not observe the sync at all.
    ch = rs.issue_renewal_challenge(PAYER, "starter")
    r = rs.settle_renewal(ch["challenge_id"], TX_STARTER, PAYER)
    assert r["ok"] is True, r
    assert len(_platform_active_rows()) == 1

    ch2 = rs.issue_renewal_challenge(PAYER, "starter")

    orig_sync = rs._sync_platform
    entered = threading.Event()
    release = threading.Event()

    def _blocking_sync(**kwargs):
        entered.set()
        assert release.wait(timeout=10), "test orchestration timed out"
        return orig_sync(**kwargs)

    monkeypatch.setattr(rs, "_sync_platform", _blocking_sync)

    activate_calls = []
    orig_activate = pp.activate_subscription_idempotent

    def _spy_activate(**kwargs):
        activate_calls.append(kwargs)
        return orig_activate(**kwargs)

    monkeypatch.setattr(pp, "activate_subscription_idempotent",
                        _spy_activate)

    holder = {}

    def _run_settle():
        holder["result"] = rs.settle_renewal(
            ch2["challenge_id"], TX_STARTER2, PAYER)

    thread = threading.Thread(target=_run_settle)
    thread.start()
    try:
        assert entered.wait(timeout=10), "settle never reached the sync"
        # Cancel fully completes before the sync step runs: rail row
        # -> cancel_pending; the platform mirror is NOT cancelled.
        out = rs.cancel_subscription(PAYER)
        assert out["status"] == "cancel_pending"
        assert len(_platform_active_rows()) == 1
    finally:
        release.set()
    thread.join(timeout=10)
    assert not thread.is_alive(), "settle thread did not finish"
    assert holder["result"]["ok"] is True, holder["result"]

    # The sync step saw a cancel_pending (not terminal-canceled) rail
    # row and mirrored the applied renewal exactly once, for the
    # renewal payment — the mirror agrees with the rail store.
    assert len(activate_calls) == 1
    assert activate_calls[0]["payment_id"] == ch2["challenge_id"]
    active = _platform_active_rows()
    assert len(active) == 1
    assert active[0]["last_payment_id"] == ch2["challenge_id"]

    status = rs.subscription_status(PAYER)
    assert status["status"] == "cancel_pending"
    assert status["effective_plan"] == "starter"
    assert status["period_index"] == 2
    assert rs.gate(PAYER, "starter_agents").allowed is True
