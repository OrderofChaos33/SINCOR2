"""Directive #2 Branch A — settle integrity (items 1 + 3).

Item 1 (cross-ledger tx_hash double-spend): before D2, UNIQUE(tx_hash)
existed only inside each ledger separately and the rail tables carried
tx_hash as plain TEXT. One on-chain transfer could fulfill two
entitlements in two systems. These tests reproduce the original attack
— ONE mocked 297 USDC transfer presented to BOTH a rail-pack settle and
a platform 'starter' checkout — and pin the shared
consumed_onchain_txs registry: exactly one entitlement is fulfilled,
the second claim is refused and records no second fulfillment.

Item 3 (finality + OFAC on high-value rail settlements): a high-value
rail settle (rail-pack-* / rail-sub-* resources) below 12 confirmations
is refused, at >=12 it succeeds; a sanctioned payer is refused; a small
per-call skill serve is NOT subjected to the 12-block wait.

The chain is a canned _rpc_call stub (no network, no keys). Treasury /
token addresses / amounts come from the repo's own modules and pricing
config, never invented here.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from eth_account import Account as _Account
from eth_account.messages import encode_defunct as _encode_defunct

import sincor2.platform_payments as pp
import sincor2.rail_routes as rr
import sincor2.rail_subscriptions as rs
import sincor2.x402_payments as x402
from sincor2.governance.money_gate import reset_money_boundary

# Well-known PUBLIC Hardhat dev account #0 — test-only, never funded.
_PAYER_ACCT = _Account.from_key(
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
PAYER = _PAYER_ACCT.address

# Verified present in the vendored OFAC SDN snapshot (see
# test_auditp2_batch_b.py): MESRI, Behzad.
SANCTIONED = "0x252a8bd2319d8a555b872990601221b3a2053bce"

USDC_297 = 297_000_000  # $297.00 in 6-decimal USDC

TX_A = "0x" + "a1" * 32
TX_B = "0x" + "b2" * 32
TX_C = "0x" + "c3" * 32
TX_D = "0x" + "d4" * 32
TX_E = "0x" + "e5" * 32
TX_F = "0x" + "f6" * 32
TX_G = "0x" + "a7" * 32
TX_H = "0x" + "b8" * 32

_REAL_SCREEN = pp.screen_payer_sanctions


def _pad(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:].lower()


class _FakeChain:
    """tx_hash -> canned receipt; mutable head for finality tests."""

    def __init__(self):
        self.transfers: dict[str, dict] = {}
        self.head = 112  # receipt at block 100 -> 13 confirmations

    def register(self, tx_hash, payer, amount_atomic, token="USDC",
                 block=100):
        self.transfers[tx_hash.lower()] = {
            "payer": payer, "amount": amount_atomic,
            "token_addr": pp.token_address(token), "block": block,
        }

    def rpc(self, method, params, attempts=3):
        if method == "eth_getTransactionReceipt":
            tx = params[0].lower()
            t = self.transfers.get(tx)
            if t is None:
                return None
            treasury = pp.TREASURY
            return {
                "status": "0x1",
                "blockNumber": hex(t["block"]),
                "logs": [{
                    "address": t["token_addr"],
                    "topics": [pp.TRANSFER_TOPIC, _pad(t["payer"]),
                               _pad(treasury)],
                    "data": hex(t["amount"]),
                }],
            }
        if method == "eth_blockNumber":
            return hex(self.head)
        raise AssertionError(f"unexpected RPC method {method}")


def _bind(challenge_id, resource_id, acct=_PAYER_ACCT):
    sig = acct.sign_message(_encode_defunct(
        text=x402.challenge_bind_message(
            challenge_id, resource_id, acct.address))).signature.hex()
    bound = x402.bind_challenge_payer(challenge_id, acct.address, sig)
    assert bound.get("ownership_proven") is True, bound
    return bound


@pytest.fixture(autouse=True)
def _fresh_boundary():
    reset_money_boundary()
    yield
    reset_money_boundary()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "sincor2.treasury_inflow._LEDGER_PATH", tmp_path / "inflow.jsonl")
    monkeypatch.setattr("sincor2.agent_billing._log_path",
                        lambda: tmp_path / "platform_payments.jsonl")
    # Default: clean OFAC verdict for every wallet (hermetic — the
    # vendored snapshot's real age must not leak into these tests).
    # The OFAC tests override this verdict / restore the real screen.
    monkeypatch.setattr(
        pp, "screen_payer_sanctions",
        lambda w: {"verdict": "pass", "detail": "clean"})
    pp._ofac_oracle = None
    pp._ofac_oracle_error_at = None
    chain = _FakeChain()
    monkeypatch.setattr(pp, "_rpc_call", chain.rpc)
    x402.init_x402_db()
    pp.init_platform_payments_db()
    yield {"tmp": tmp_path, "chain": chain}
    pp._ofac_oracle = None
    pp._ofac_oracle_error_at = None


@pytest.fixture()
def mock_comms(monkeypatch):
    sent, queued = [], []

    def fake_send_email(self, to_email, to_name="", subject="",
                        html_content="", text_content=None, metadata=None):
        sent.append({"to": to_email})
        return {"status": "sent", "message_id": "msg-test-1"}

    class _FakeResult:
        id = "celery-job-test-1"

    def fake_apply_async(args=None, kwargs=None, queue=None, **kw):
        queued.append({"args": args, "queue": queue})
        return _FakeResult()

    monkeypatch.setattr(
        "sincor2.email_sender.EmailSender.send_email", fake_send_email)
    monkeypatch.setattr(rr.fulfill_pack_agent, "apply_async",
                        fake_apply_async)
    return {"sent": sent, "queued": queued}


@pytest.fixture(scope="module")
def rail_app():
    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = True
    rr.init_rail_routes(app)
    return app


def _pack_challenge(client, sku="credentialing_launch"):
    res = client.post("/rail/checkout", json={
        "pack_sku": sku, "payer_wallet": PAYER,
        "buyer_email": "buyer@example.com"})
    assert res.status_code == 402, res.get_json()
    body = res.get_json()
    _bind(body["challenge_id"], body["resource_id"])
    return body["challenge_id"]


def _settle_pack(client, cid, tx_hash):
    res = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": tx_hash, "payer_wallet": PAYER,
        "buyer_email": "buyer@example.com"})
    return res.get_json()


def _platform_sign(payment_id: str) -> str:
    """W-113: EIP-191 payer signature over the platform bind message,
    as the buyer's connected wallet produces via personal_sign."""
    return _PAYER_ACCT.sign_message(_encode_defunct(
        text=pp.platform_bind_message(
            payment_id, _PAYER_ACCT.address))).signature.hex()


def _verify_bound(payment_id: str, tx_hash: str):
    """verify_checkout under the W-113 bound flow: the checkout's bound
    payer (PAYER) plus its EIP-191 ownership proof."""
    return pp.verify_checkout(
        payment_id, tx_hash, payer_wallet=PAYER,
        payer_signature=_platform_sign(payment_id))


def _insert_starter_checkout(payment_id: str) -> None:
    """A platform 'starter' checkout denominated in USDC ($297) — the
    documented settle_plan_quote USDC-fallback shape for this plan.
    W-113: the payer wallet (PAYER) is bound at creation."""
    now = datetime.now(timezone.utc)
    with pp._conn() as conn:
        conn.execute(
            """INSERT INTO platform_checkouts
               (payment_id, plan_id, product_name, token, amount_atomic,
                amount_display, usd_reference, payer_wallet, customer_email,
                status, tx_hash, created_at, expires_at, metadata)
               VALUES (?, 'starter', 'Starter', 'USDC', ?, 297.0, 297.0,
                       ?, 'buyer@example.com', 'pending', NULL, ?, ?, ?)""",
            (payment_id, str(USDC_297), PAYER, now.isoformat(),
             (now + timedelta(hours=1)).isoformat(),
             '{"order_type": "subscription", "billing": "month"}'),
        )
        conn.commit()


def _intake_count(tmp_path) -> int:
    conn = sqlite3.connect(str(tmp_path / "revenue.db"))
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM pack_intakes").fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


def _challenge_status(challenge_id: str) -> str:
    with x402._conn() as conn:
        row = conn.execute(
            "SELECT status FROM x402_challenges WHERE challenge_id=?",
            (challenge_id,)).fetchone()
    return row["status"] if row else ""


# ---------------------------------------------------------------------------
# Item 1: the original cross-ledger double-spend, both orders
# ---------------------------------------------------------------------------

def test_double_spend_rail_first_platform_refused(
        rail_app, env, mock_comms):
    """One 297 USDC transfer settles a rail pack; the SAME transfer
    presented to a platform starter checkout is refused and fulfills
    nothing there."""
    env["chain"].register(TX_A, PAYER, USDC_297)
    client = rail_app.test_client()
    cid = _pack_challenge(client)

    body = _settle_pack(client, cid, TX_A)
    assert body["ok"] is True, body
    assert body["fulfillment"]["status"] == "delivered"
    assert _intake_count(env["tmp"]) == 1

    _insert_starter_checkout("PLAT-D2-RAILFIRST")
    r = _verify_bound("PLAT-D2-RAILFIRST", TX_A)
    assert r["ok"] is False, r
    assert r["error"] == "tx_already_used"
    # No second fulfillment: checkout never finalized, cannot fulfill.
    row = pp._load_checkout("PLAT-D2-RAILFIRST")
    assert row["status"] not in ("finalized", "fulfilled")
    assert pp.mark_checkout_fulfilled("PLAT-D2-RAILFIRST") is False
    # Exactly one payment record was finalized across both systems.
    log = env["tmp"] / "platform_payments.jsonl"
    assert len(log.read_text().splitlines()) == 1
    # The registry names the winning entitlement.
    claim = pp.get_consumed_onchain_tx(TX_A)
    assert claim is not None and claim["claim_ref"] == cid


def test_double_spend_platform_first_rail_refused(
        rail_app, env, mock_comms):
    """Reverse order: the platform checkout finalizes the transfer;
    the rail-pack settle of the same transfer is refused and the pack
    is never fulfilled."""
    env["chain"].register(TX_B, PAYER, USDC_297)
    _insert_starter_checkout("PLAT-D2-PLATFIRST")
    r = _verify_bound("PLAT-D2-PLATFIRST", TX_B)
    assert r["ok"] is True and r["status"] == "finalized", r
    assert pp.mark_checkout_fulfilled("PLAT-D2-PLATFIRST") is True

    client = rail_app.test_client()
    cid = _pack_challenge(client)
    body = _settle_pack(client, cid, TX_B)
    assert body["ok"] is False, body
    assert body["error"] == "tx_hash_already_used"
    # No pack fulfillment, no rail event, no email/job.
    assert rr._get_rail_event(f"rail-{cid}") is None
    assert _intake_count(env["tmp"]) == 0
    assert mock_comms["sent"] == [] and mock_comms["queued"] == []
    assert _challenge_status(cid) != "fulfilled"


def test_double_spend_subscription_first_platform_refused(env):
    """Fourth ledger: a rail-subscription settle consumes the tx; a
    platform checkout presenting the same tx is refused."""
    env["chain"].register(TX_H, PAYER, USDC_297)
    issued = rs.issue_renewal_challenge(PAYER, "starter")
    cid = issued["challenge_id"]
    res = rs.settle_renewal(cid, TX_H, PAYER)
    assert res["ok"] is True, res

    _insert_starter_checkout("PLAT-D2-SUBFIRST")
    r = _verify_bound("PLAT-D2-SUBFIRST", TX_H)
    assert r["ok"] is False, r
    assert r["error"] == "tx_already_used"
    assert pp._load_checkout("PLAT-D2-SUBFIRST")["status"] not in (
        "finalized", "fulfilled")
    claim = pp.get_consumed_onchain_tx(TX_H)
    assert claim is not None and claim["claim_ref"] == cid


# ---------------------------------------------------------------------------
# Item 1: same ledger twice refused; different txs both succeed
# ---------------------------------------------------------------------------

def test_same_tx_twice_same_x402_ledger_refused(env):
    env["chain"].register(TX_C, PAYER, USDC_297)
    c1 = x402.create_challenge("rail-pack-credentialing_launch")
    c2 = x402.create_challenge("rail-pack-credentialing_launch")
    _bind(c1["challenge_id"], "rail-pack-credentialing_launch")
    _bind(c2["challenge_id"], "rail-pack-credentialing_launch")

    r1 = x402.verify_challenge(c1["challenge_id"], TX_C,
                               payer_wallet=PAYER,
                               require_bound_challenge=True)
    assert r1["ok"] is True and r1["status"] == "fulfilled", r1
    r2 = x402.verify_challenge(c2["challenge_id"], TX_C,
                               payer_wallet=PAYER,
                               require_bound_challenge=True)
    assert r2["ok"] is False, r2
    assert r2["error"] == "tx_hash_already_used"
    assert _challenge_status(c2["challenge_id"]) != "fulfilled"


def test_same_tx_twice_same_platform_ledger_refused(env):
    env["chain"].register(TX_D, PAYER, USDC_297)
    _insert_starter_checkout("PLAT-D2-SAME-A")
    _insert_starter_checkout("PLAT-D2-SAME-B")
    r1 = _verify_bound("PLAT-D2-SAME-A", TX_D)
    assert r1["ok"] is True and r1["status"] == "finalized", r1
    r2 = _verify_bound("PLAT-D2-SAME-B", TX_D)
    assert r2["ok"] is False, r2
    assert r2["error"] == "tx_already_used"
    assert pp._load_checkout("PLAT-D2-SAME-B")["status"] == "pending"


def test_different_txs_both_succeed_no_false_positive(
        rail_app, env, mock_comms):
    env["chain"].register(TX_E, PAYER, USDC_297)
    env["chain"].register(TX_F, PAYER, USDC_297)
    client = rail_app.test_client()
    cid = _pack_challenge(client)
    body = _settle_pack(client, cid, TX_E)
    assert body["ok"] is True, body

    _insert_starter_checkout("PLAT-D2-DISTINCT")
    r = _verify_bound("PLAT-D2-DISTINCT", TX_F)
    assert r["ok"] is True and r["status"] == "finalized", r
    assert pp.mark_checkout_fulfilled("PLAT-D2-DISTINCT") is True
    assert _intake_count(env["tmp"]) == 1


# ---------------------------------------------------------------------------
# Item 3: finality + OFAC on high-value rail settles; small serves exempt
# ---------------------------------------------------------------------------

def test_high_value_rail_settle_below_finality_refused_then_succeeds(
        rail_app, env, mock_comms):
    env["chain"].register(TX_G, PAYER, USDC_297, block=100)
    env["chain"].head = 100  # 1 confirmation — the original repro depth
    client = rail_app.test_client()
    cid = _pack_challenge(client)

    body = _settle_pack(client, cid, TX_G)
    assert body["ok"] is False, body
    assert body["error"] == "awaiting_finality"
    assert body["confirmations"] == 1
    assert body["required_confirmations"] == 12
    # Not fulfilled, not claimed, nothing provisioned.
    assert _challenge_status(cid) != "fulfilled"
    assert pp.get_consumed_onchain_tx(TX_G) is None
    assert _intake_count(env["tmp"]) == 0
    assert mock_comms["queued"] == []

    env["chain"].head = 111  # exactly 12 confirmations
    body = _settle_pack(client, cid, TX_G)
    assert body["ok"] is True, body
    assert body["fulfillment"]["status"] == "delivered"
    assert _intake_count(env["tmp"]) == 1


def test_ofac_sanctioned_payer_refused_on_rail_settle(
        rail_app, env, mock_comms, monkeypatch):
    """Snapshot-hit verdict for the payer -> the high-value rail settle
    is refused before fulfillment (verdict stubbed at the
    screen_payer_sanctions boundary — the vendored-OFAC consumer the
    platform path also uses)."""
    monkeypatch.setattr(
        pp, "screen_payer_sanctions",
        lambda w: {"verdict": "hit", "detail": "sanctions_hit"})
    env["chain"].register(TX_A, PAYER, USDC_297)
    client = rail_app.test_client()
    cid = _pack_challenge(client)
    body = _settle_pack(client, cid, TX_A)
    assert body["ok"] is False, body
    assert body["error"] == "sanctions_hit"
    assert _challenge_status(cid) != "fulfilled"
    assert _intake_count(env["tmp"]) == 0
    assert mock_comms["queued"] == []


def test_ofac_real_snapshot_sanctioned_wallet_refused(env, monkeypatch):
    """The real vendored OFAC snapshot (freshness forced current, the
    test_auditp2_batch_b pattern) refuses a genuinely sanctioned payer
    on the high-value verify path."""
    def _fresh_oracle():
        from sincor2.defi.ofac_sdn import build_ofac_oracle
        import time as _time
        oracle = build_ofac_oracle()
        oracle.aml.list_updated_at = _time.time()
        return oracle

    monkeypatch.setattr(pp, "screen_payer_sanctions", _REAL_SCREEN)
    monkeypatch.setattr(pp, "_get_ofac_oracle", _fresh_oracle)
    env["chain"].register(TX_B, SANCTIONED, USDC_297)
    ch = x402.create_challenge("rail-pack-credentialing_launch")
    r = x402.verify_challenge(ch["challenge_id"], TX_B,
                              payer_wallet=SANCTIONED)
    assert r["ok"] is False, r
    assert r["error"] == "sanctions_hit"
    assert _challenge_status(ch["challenge_id"]) != "fulfilled"
    assert pp.get_consumed_onchain_tx(TX_B) is None


def test_small_skill_serve_not_subject_to_finality_wait(env):
    """Scoping pin: a per-call skill resource (agent_task, 1 SINC)
    fulfills at 1 confirmation — the 12-block wait and OFAC gate apply
    only to rail-pack-*/rail-sub-* high-value settlements."""
    assert x402.is_high_value_rail_resource("agent_task") is False
    assert x402.is_high_value_rail_resource(
        "rail-pack-credentialing_launch") is True
    assert x402.is_high_value_rail_resource("rail-sub-starter") is True

    env["chain"].register(TX_C, PAYER, 100_000_000, token="SINC",
                           block=100)
    env["chain"].head = 100  # 1 confirmation
    ch = x402.create_challenge("agent_task")
    r = x402.verify_challenge(ch["challenge_id"], TX_C,
                              payer_wallet=PAYER)
    assert r["ok"] is True and r["status"] == "fulfilled", r
