"""W-113 — platform-verify squat (hunt-31) regression + full-loop tests.

Finding: D2 (#547) made consumed_onchain_txs first-claim-wins across
ledgers, but the platform leg bound nothing: verify_checkout accepted a
bare (payment_id, tx_hash) — verify_treasury_transfer rejected only a
SUPPLIED payer_wallet that mismatched, so a caller supplying NO wallet
finalized a checkout against any observed treasury transfer and claimed
the tx in the shared registry under the chain-recovered payer. The
executed PoC (poc31_d2_platform_squat.py) showed an attacker with a
wallet-less platform checkout fulfilling on a victim's $297 rail
payment and the victim's own settle then failing tx_hash_already_used.

Fix pinned here: verify_checkout's pending->verify transition requires
payer proof — payer_wallet equal to the checkout's bound wallet (stored
at creation, or bound at first proven verify) AND an EIP-191
personal_sign signature from that wallet over
platform_bind_message(payment_id, payer_wallet); the proven wallet is
what verify_treasury_transfer checks the on-chain payer against, and
it is the only payer ever written into a registry claim.

The honest full loop is pinned end-to-end: platform checkout -> payer
proof -> verify -> registry claim -> the SAME tx presented to a bound
rail challenge is refused (and, in the squat scenario, the victim's
rail settle of their own tx succeeds after every squat attempt fails).

Chain is a canned _rpc_call stub (no network). Keys are public test
keys only (Hardhat dev account #0 for the victim/buyer, a fixed
throwaway key for the attacker) — never funded, never real.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from eth_account import Account as _Account
from eth_account.messages import encode_defunct as _encode_defunct

import sincor2.platform_payments as pp
import sincor2.rail_routes as rr
import sincor2.x402_payments as x402
from sincor2.governance.money_gate import reset_money_boundary

# Well-known PUBLIC Hardhat dev account #0 — test-only, never funded.
VICTIM_ACCT = _Account.from_key(
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
VICTIM = VICTIM_ACCT.address

# Fixed throwaway key for the attacker — test-only, never funded.
ATTACKER_ACCT = _Account.from_key("0x" + "11" * 32)
ATTACKER = ATTACKER_ACCT.address

USDC_297 = 297_000_000  # $297.00 in 6-decimal USDC

TX_V = "0x" + "a1" * 32
TX_P = "0x" + "b2" * 32
TX_Q = "0x" + "c3" * 32
TX_R = "0x" + "d4" * 32


def _pad(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:].lower()


class _FakeChain:
    """tx_hash -> canned receipt; head gives 13 confirmations."""

    def __init__(self):
        self.transfers: dict[str, dict] = {}
        self.head = 112  # receipt at block 100 -> 13 confirmations

    def register(self, tx_hash, payer, amount_atomic):
        self.transfers[tx_hash.lower()] = {
            "payer": payer, "amount": amount_atomic}

    def rpc(self, method, params, attempts=3):
        if method == "eth_getTransactionReceipt":
            t = self.transfers.get(params[0].lower())
            if t is None:
                return None
            return {"status": "0x1", "blockNumber": hex(100), "logs": [{
                "address": pp.token_address("USDC"),
                "topics": [pp.TRANSFER_TOPIC, _pad(t["payer"]),
                           _pad(pp.TREASURY)],
                "data": hex(t["amount"])}]}
        if method == "eth_blockNumber":
            return hex(self.head)
        raise AssertionError(f"unexpected RPC method {method}")


def _bind_challenge(challenge_id, resource_id, acct=VICTIM_ACCT):
    sig = acct.sign_message(_encode_defunct(
        text=x402.challenge_bind_message(
            challenge_id, resource_id, acct.address))).signature.hex()
    bound = x402.bind_challenge_payer(challenge_id, acct.address, sig)
    assert bound.get("ownership_proven") is True, bound


def _platform_sign(acct, payment_id):
    """EIP-191 signature over the platform bind message, as the buy
    page's connected wallet produces via personal_sign."""
    return acct.sign_message(_encode_defunct(
        text=pp.platform_bind_message(
            payment_id, acct.address))).signature.hex()


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
        pp, "screen_payer_sanctions",
        lambda w: {"verdict": "pass", "detail": "clean"})
    pp._ofac_oracle = None
    chain = _FakeChain()
    monkeypatch.setattr(pp, "_rpc_call", chain.rpc)
    x402.init_x402_db()
    pp.init_platform_payments_db()
    yield {"tmp": tmp_path, "chain": chain}
    pp._ofac_oracle = None


@pytest.fixture()
def mock_comms(monkeypatch):
    def fake_send_email(self, to_email, to_name="", subject="",
                        html_content="", text_content=None, metadata=None):
        return {"status": "sent", "message_id": "msg-w113"}

    class _FakeResult:
        id = "celery-w113"

    def fake_apply_async(args=None, kwargs=None, queue=None, **kw):
        return _FakeResult()

    import sincor2.email_sender as _es
    monkeypatch.setattr(_es.EmailSender, "send_email", fake_send_email)
    monkeypatch.setattr(rr.fulfill_pack_agent, "apply_async",
                        fake_apply_async)


@pytest.fixture(scope="module")
def rail_app():
    from flask import Flask
    app = Flask(__name__)
    app.config["TESTING"] = True
    rr.init_rail_routes(app)
    return app


def _insert_starter_checkout(payment_id, email, payer_wallet=""):
    """Platform 'starter' checkout (USDC $297), mirroring the PoC."""
    now = datetime.now(timezone.utc)
    with pp._conn() as conn:
        conn.execute(
            """INSERT INTO platform_checkouts
               (payment_id, plan_id, product_name, token, amount_atomic,
                amount_display, usd_reference, payer_wallet, customer_email,
                status, tx_hash, created_at, expires_at, metadata)
               VALUES (?, 'starter', 'Starter', 'USDC', ?, 297.0, 297.0,
                       ?, ?, 'pending', NULL, ?, ?, ?)""",
            (payment_id, str(USDC_297), payer_wallet, email,
             now.isoformat(), (now + timedelta(hours=1)).isoformat(),
             '{"order_type": "subscription", "billing": "month"}'))
        conn.commit()


def _intake_count(tmp_path) -> int:
    try:
        conn = sqlite3.connect(str(tmp_path / "revenue.db"))
        n = conn.execute("SELECT COUNT(*) FROM pack_intakes").fetchone()[0]
        conn.close()
        return n
    except sqlite3.OperationalError:
        return 0


def _pack_challenge(client, acct=VICTIM_ACCT):
    res = client.post("/rail/checkout", json={
        "pack_sku": "credentialing_launch", "payer_wallet": acct.address,
        "buyer_email": "victim@example.com"})
    assert res.status_code == 402, res.get_json()
    body = res.get_json()
    _bind_challenge(body["challenge_id"], body["resource_id"], acct)
    return body["challenge_id"]


def _settle_pack(client, cid, tx_hash, acct=VICTIM_ACCT):
    res = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": tx_hash,
        "payer_wallet": acct.address, "buyer_email": "victim@example.com"})
    return res.get_json()


# ---------------------------------------------------------------------------
# The squat (PoC scenario S, flipped): every attacker variant is refused,
# no registry claim is recorded, and the victim's own settle succeeds.
# ---------------------------------------------------------------------------

def test_squat_refused_in_all_variants_victim_settles(
        rail_app, env, mock_comms):
    chain = env["chain"]
    chain.register(TX_V, VICTIM, USDC_297)
    client = rail_app.test_client()
    cid = _pack_challenge(client)

    # Attacker checkout: attacker email, NO payer wallet bound.
    _insert_starter_checkout("PLAT-W113-SQUAT", "attacker@example.com")

    # Variant 1 — the original PoC: bare verify, no wallet, no signature.
    r = pp.verify_checkout("PLAT-W113-SQUAT", TX_V)
    assert r["ok"] is False and r["error"] == "payer_wallet_required", r

    # Variant 2 — attacker names his OWN wallet and signs with it: the
    # on-chain payer is the victim, not the attacker.
    r = pp.verify_checkout(
        "PLAT-W113-SQUAT", TX_V, payer_wallet=ATTACKER,
        payer_signature=_platform_sign(ATTACKER_ACCT, "PLAT-W113-SQUAT"))
    assert r["ok"] is False and r["error"] == "payer_wallet_mismatch", r

    # Variant 3 — attacker names the VICTIM's wallet (public) but cannot
    # sign for it: signs with his own key instead.
    r = pp.verify_checkout(
        "PLAT-W113-SQUAT", TX_V, payer_wallet=VICTIM,
        payer_signature=_platform_sign(ATTACKER_ACCT, "PLAT-W113-SQUAT"))
    assert r["ok"] is False and r["error"] == "ownership_proof_failed", r

    # Variant 4 — attacker names the victim's wallet, no signature at all.
    r = pp.verify_checkout("PLAT-W113-SQUAT", TX_V, payer_wallet=VICTIM)
    assert r["ok"] is False
    assert r["error"] == "ownership_signature_required", r

    # Nothing was claimed, finalized, or fulfilled for the attacker.
    assert pp.get_consumed_onchain_tx(TX_V) is None
    row = pp._load_checkout("PLAT-W113-SQUAT")
    assert row["status"] == "pending"
    assert pp.mark_checkout_fulfilled("PLAT-W113-SQUAT") is False

    # The victim settles their own rail pack with their own tx.
    body = _settle_pack(client, cid, TX_V)
    assert body["ok"] is True, body
    assert _intake_count(env["tmp"]) == 1
    claim = pp.get_consumed_onchain_tx(TX_V)
    assert claim is not None and claim["claim_ref"] == cid


# ---------------------------------------------------------------------------
# Honest path: bound-at-creation and bind-at-verify both finalize, and
# the registry claim names the proven payer.
# ---------------------------------------------------------------------------

def test_honest_verify_bound_at_creation(env):
    env["chain"].register(TX_P, VICTIM, USDC_297)
    _insert_starter_checkout(
        "PLAT-W113-BOUND", "buyer@example.com", payer_wallet=VICTIM)
    r = pp.verify_checkout(
        "PLAT-W113-BOUND", TX_P, payer_wallet=VICTIM,
        payer_signature=_platform_sign(VICTIM_ACCT, "PLAT-W113-BOUND"))
    assert r["ok"] is True and r["status"] == "finalized", r
    assert pp.mark_checkout_fulfilled("PLAT-W113-BOUND") is True
    claim = pp.get_consumed_onchain_tx(TX_P)
    assert claim is not None
    assert claim["claim_system"] == "platform"
    assert claim["claim_ref"] == "PLAT-W113-BOUND"
    assert claim["payer_wallet"] == VICTIM.lower()


def test_honest_verify_binds_walletless_checkout(env):
    env["chain"].register(TX_Q, VICTIM, USDC_297)
    _insert_starter_checkout("PLAT-W113-BIND", "buyer@example.com")
    r = pp.verify_checkout(
        "PLAT-W113-BIND", TX_Q, payer_wallet=VICTIM,
        payer_signature=_platform_sign(VICTIM_ACCT, "PLAT-W113-BIND"))
    assert r["ok"] is True and r["status"] == "finalized", r
    # The proven wallet is now the checkout's bound payer.
    row = pp._load_checkout("PLAT-W113-BIND")
    assert (row["payer_wallet"] or "").lower() == VICTIM.lower()


def test_bound_checkout_rejects_contradicting_wallet(env):
    env["chain"].register(TX_R, VICTIM, USDC_297)
    _insert_starter_checkout(
        "PLAT-W113-CONTRA", "buyer@example.com", payer_wallet=VICTIM)
    # A different wallet — even with its own valid signature — cannot
    # verify a checkout bound to the victim's wallet.
    r = pp.verify_checkout(
        "PLAT-W113-CONTRA", TX_R, payer_wallet=ATTACKER,
        payer_signature=_platform_sign(ATTACKER_ACCT, "PLAT-W113-CONTRA"))
    assert r["ok"] is False and r["error"] == "payer_wallet_mismatch", r
    # The bound wallet named, but signed by someone else.
    r = pp.verify_checkout(
        "PLAT-W113-CONTRA", TX_R, payer_wallet=VICTIM,
        payer_signature=_platform_sign(ATTACKER_ACCT, "PLAT-W113-CONTRA"))
    assert r["ok"] is False and r["error"] == "ownership_proof_failed", r
    assert pp.get_consumed_onchain_tx(TX_R) is None
    assert pp._load_checkout("PLAT-W113-CONTRA")["status"] == "pending"


def test_signature_not_replayable_across_checkouts(env):
    env["chain"].register(TX_P, VICTIM, USDC_297)
    env["chain"].register(TX_Q, VICTIM, USDC_297)
    _insert_starter_checkout(
        "PLAT-W113-REPLAY-A", "buyer@example.com", payer_wallet=VICTIM)
    _insert_starter_checkout(
        "PLAT-W113-REPLAY-B", "buyer@example.com", payer_wallet=VICTIM)
    sig_for_a = _platform_sign(VICTIM_ACCT, "PLAT-W113-REPLAY-A")
    # The bind message names the payment_id: A's signature proves
    # nothing for B.
    r = pp.verify_checkout(
        "PLAT-W113-REPLAY-B", TX_Q, payer_wallet=VICTIM,
        payer_signature=sig_for_a)
    assert r["ok"] is False and r["error"] == "ownership_proof_failed", r
    assert pp.get_consumed_onchain_tx(TX_Q) is None


def test_high_s_signature_twin_refused(env):
    env["chain"].register(TX_P, VICTIM, USDC_297)
    _insert_starter_checkout(
        "PLAT-W113-HIGHS", "buyer@example.com", payer_wallet=VICTIM)
    good = _platform_sign(VICTIM_ACCT, "PLAT-W113-HIGHS")
    raw = bytes.fromhex(good.removeprefix("0x"))
    n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
    r_bytes, s_bytes, v = raw[:32], raw[32:64], raw[64]
    s_twin = (n - int.from_bytes(s_bytes, "big")).to_bytes(32, "big")
    v_twin = 28 if v == 27 else 27
    twin = (r_bytes + s_twin + bytes([v_twin])).hex()
    # Sanity: the twin recovers to the same signer under naive recovery,
    # so only the low-s canonicality guard can refuse it.
    from eth_account import Account
    from eth_account.messages import encode_defunct as _ed
    msg = pp.platform_bind_message("PLAT-W113-HIGHS", VICTIM)
    assert Account.recover_message(_ed(text=msg), signature=twin).lower() \
        == VICTIM.lower()
    r = pp.verify_checkout(
        "PLAT-W113-HIGHS", TX_P, payer_wallet=VICTIM, payer_signature=twin)
    assert r["ok"] is False and r["error"] == "ownership_proof_failed", r
    assert pp.get_consumed_onchain_tx(TX_P) is None


# ---------------------------------------------------------------------------
# Full loop, platform first: a proven platform verify claims the tx and
# the same tx cannot then settle a rail pack.
# ---------------------------------------------------------------------------

def test_platform_first_with_proof_blocks_rail_settle(
        rail_app, env, mock_comms):
    env["chain"].register(TX_P, VICTIM, USDC_297)
    _insert_starter_checkout(
        "PLAT-W113-FIRST", "buyer@example.com", payer_wallet=VICTIM)
    r = pp.verify_checkout(
        "PLAT-W113-FIRST", TX_P, payer_wallet=VICTIM,
        payer_signature=_platform_sign(VICTIM_ACCT, "PLAT-W113-FIRST"))
    assert r["ok"] is True and r["status"] == "finalized", r
    assert pp.mark_checkout_fulfilled("PLAT-W113-FIRST") is True

    client = rail_app.test_client()
    cid = _pack_challenge(client)
    body = _settle_pack(client, cid, TX_P)
    assert body["ok"] is False, body
    assert body["error"] == "tx_hash_already_used"
    assert _intake_count(env["tmp"]) == 0
