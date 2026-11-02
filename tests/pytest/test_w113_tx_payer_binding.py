"""W-113 follow-up (Wren PR 11) — the payer-proof signature binds
(payment_id, wallet), deliberately not tx_hash: refusal pin.

Accepted-risk pin: a VALID payer-proof signature for wallet A
presented together with a transaction whose on-chain payer is a
DIFFERENT wallet B must be REFUSED. The signature proves control of
A only; verify_checkout passes the proven wallet into
verify_treasury_transfer, which requires the presented tx's on-chain
payer to BE that wallet — so A's proof can never spend B's payment.
Binding tx_hash into the signature instead was rejected because the
tx does not exist until after the buyer pays, so it would force a
second signature round-trip into the checkout UX.

Chain is a canned _rpc_call stub (no network). Keys are public test
keys only (Hardhat dev account #0 for wallet A, a fixed throwaway
key for wallet B) — never funded, never real.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from eth_account import Account as _Account
from eth_account.messages import encode_defunct as _encode_defunct

import sincor2.platform_payments as pp
import sincor2.x402_payments as x402
from sincor2.governance.money_gate import reset_money_boundary

# Well-known PUBLIC Hardhat dev account #0 — wallet A, the prover.
WALLET_A_ACCT = _Account.from_key(
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
WALLET_A = WALLET_A_ACCT.address

# Fixed throwaway key — wallet B, the tx's actual on-chain payer.
WALLET_B_ACCT = _Account.from_key("0x" + "22" * 32)
WALLET_B = WALLET_B_ACCT.address

USDC_297 = 297_000_000  # $297.00 in 6-decimal USDC

TX_FROM_B = "0x" + "e5" * 32
TX_FROM_A = "0x" + "f6" * 32


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


def _platform_sign(acct, payment_id):
    """EIP-191 signature over the platform bind message — binds
    (payment_id, wallet) only; the message contains no tx_hash."""
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


def _insert_starter_checkout(payment_id, email, payer_wallet=""):
    """Platform 'starter' checkout (USDC $297), mirroring the W-113
    test file's helper."""
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


# ---------------------------------------------------------------------------
# The pin: A's signature is fully valid — right wallet, right checkout,
# right message. The tx is a genuine treasury transfer of the exact
# amount. The ONLY defect is that wallet B paid it. Refusal must come
# from the on-chain payer check against the proven wallet.
# ---------------------------------------------------------------------------

def test_valid_proof_plus_other_wallets_tx_refused_bound_checkout(env):
    # Checkout bound to wallet A at creation; B's tx pays the exact
    # amount in the right token to the treasury.
    env["chain"].register(TX_FROM_B, WALLET_B, USDC_297)
    _insert_starter_checkout(
        "PLAT-W113-TXBIND-BOUND", "buyer@example.com",
        payer_wallet=WALLET_A)
    r = pp.verify_checkout(
        "PLAT-W113-TXBIND-BOUND", TX_FROM_B, payer_wallet=WALLET_A,
        payer_signature=_platform_sign(
            WALLET_A_ACCT, "PLAT-W113-TXBIND-BOUND"))
    assert r["ok"] is False and r["error"] == "payer_wallet_mismatch", r
    # Nothing claimed, nothing finalized, nothing fulfillable.
    assert pp.get_consumed_onchain_tx(TX_FROM_B) is None
    assert pp._load_checkout("PLAT-W113-TXBIND-BOUND")["status"] == "pending"
    assert pp.mark_checkout_fulfilled("PLAT-W113-TXBIND-BOUND") is False


def test_valid_proof_plus_other_wallets_tx_refused_walletless_checkout(env):
    # Same shape on a wallet-less checkout: A proves control at verify
    # (which would bind A), but B's tx still cannot be spent by A.
    env["chain"].register(TX_FROM_B, WALLET_B, USDC_297)
    _insert_starter_checkout("PLAT-W113-TXBIND-FREE", "buyer@example.com")
    r = pp.verify_checkout(
        "PLAT-W113-TXBIND-FREE", TX_FROM_B, payer_wallet=WALLET_A,
        payer_signature=_platform_sign(
            WALLET_A_ACCT, "PLAT-W113-TXBIND-FREE"))
    assert r["ok"] is False and r["error"] == "payer_wallet_mismatch", r
    assert pp.get_consumed_onchain_tx(TX_FROM_B) is None
    assert pp._load_checkout("PLAT-W113-TXBIND-FREE")["status"] == "pending"
    assert pp.mark_checkout_fulfilled("PLAT-W113-TXBIND-FREE") is False


def test_control_proven_wallets_own_tx_finalizes(env):
    # Control: the identical proof shape with a tx A actually paid
    # finalizes — the refusals above come from the payer check, not
    # from a broken proof path.
    env["chain"].register(TX_FROM_A, WALLET_A, USDC_297)
    _insert_starter_checkout(
        "PLAT-W113-TXBIND-OWN", "buyer@example.com",
        payer_wallet=WALLET_A)
    r = pp.verify_checkout(
        "PLAT-W113-TXBIND-OWN", TX_FROM_A, payer_wallet=WALLET_A,
        payer_signature=_platform_sign(
            WALLET_A_ACCT, "PLAT-W113-TXBIND-OWN"))
    assert r["ok"] is True and r["status"] == "finalized", r
    claim = pp.get_consumed_onchain_tx(TX_FROM_A)
    assert claim is not None
    assert claim["payer_wallet"] == WALLET_A.lower()
