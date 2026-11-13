"""Hunt-29 regression: rail settle front-run via unbound challenges.

Attack (red-team hunt 29, CONFIRMED on origin/main @ 01010b20): rail
settle called verify_challenge with the default
require_bound_challenge=False, and rail checkout never bound the
challenge to the buyer. An attacker minted their OWN unbound pack
challenge and settled it FIRST with the victim's public USDC tx_hash
(no payer proof anywhere) — fulfilled, working download URL — and the
victim's own settle then failed tx_hash_already_used. Attacker cost:
zero. Blast radius: every pack SKU.

Fix under test:
  1. Rail settle (pack + subscription, first attempt and retry) passes
     require_bound_challenge=True — an unbound challenge can never
     claim a payment (same as the public /api/x402/verify route).
  2. Checkout follows the W-102r3 pattern: the challenge is minted
     unbound, and when the buyer names a wallet the response carries
     the exact EIP-191 sign_message for that wallet; the buyer binds
     via bind_challenge_payer (POST /api/x402/challenge-bind) BEFORE
     paying. A bare address string is never a binding — an attacker
     cannot bind the victim's wallet without the victim's key.
  3. Subscription challenges are bound at issuance to the issued
     wallet (server-issued binding; settle extends only that wallet),
     so subscription verify is bound-consistent too.

The chain is the canned _rpc_call stub pattern from test_rail_routes /
test_rail_digital_delivery: zero network, nothing broadcast. Wallets
are the well-known PUBLIC Hardhat dev accounts #0/#1 — test-only,
never funded — because binding now requires real signatures.
"""
from __future__ import annotations

import sqlite3

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

import sincor2.rail_routes as rr
import sincor2.rail_subscriptions as rs
import sincor2.platform_payments as pp
import sincor2.x402_payments as x402
from sincor2.governance.money_gate import reset_money_boundary

VICTIM_ACCT = Account.from_key(
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
ATTACKER_ACCT = Account.from_key(
    "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d")
VICTIM = VICTIM_ACCT.address
ATTACKER = ATTACKER_ACCT.address

TX_PACK = "0x" + "ab" * 32   # victim's $97 digital-pack payment
TX_SUB = "0x" + "cd" * 32    # victim's $297 starter-subscription payment

# tx_hash -> (amount_atomic, resource whose treasury receives it)
_PAYMENTS = {
    TX_PACK: (97_000_000, "rail-pack-digital_bi_template_pack"),
    TX_SUB: (297_000_000, "rail-sub-starter"),
}


@pytest.fixture(autouse=True)
def _fresh_boundary():
    reset_money_boundary()
    yield
    reset_money_boundary()


@pytest.fixture()
def rail_env(tmp_path, monkeypatch):
    """Hermetic stores + stubbed RPC: the victim's transfers exist
    on the canned chain; nothing else does."""
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "sincor2.treasury_inflow._LEDGER_PATH", tmp_path / "inflow.jsonl")
    monkeypatch.setattr("sincor2.agent_billing._log_path",
                        lambda: tmp_path / "platform_payments.jsonl")

    token_addr = pp.token_address("USDC")

    def _fake_rpc(method, params, attempts=3):
        if method == "eth_getTransactionReceipt":
            tx = params[0]
            assert tx in _PAYMENTS, f"unexpected tx {tx}"
            amount, resource_id = _PAYMENTS[tx]
            treasury = x402.get_resource(resource_id)["treasury"]
            return {
                "status": "0x1",
                "blockNumber": hex(100),
                "logs": [{
                    "address": token_addr,
                    "topics": [
                        pp.TRANSFER_TOPIC,
                        "0x" + "0" * 24 + VICTIM[2:].lower(),
                        "0x" + "0" * 24 + treasury[2:].lower(),
                    ],
                    "data": hex(amount),
                }],
            }
        if method == "eth_blockNumber":
            return hex(112)  # 13 confirmations — past finality depth
        raise AssertionError(f"unexpected RPC method {method}")

    monkeypatch.setattr(pp, "_rpc_call", _fake_rpc)
    x402.init_x402_db()
    return {"tmp": tmp_path}


@pytest.fixture()
def mock_comms(monkeypatch):
    """Capture outbound email; digital packs must never queue a job."""
    sent = []

    def fake_send_email(self, to_email, to_name="", subject="",
                        html_content="", text_content=None, metadata=None):
        sent.append({"to": to_email, "html": html_content,
                     "metadata": metadata or {}})
        return {"status": "sent", "message_id": "msg-test-h29"}

    def boom_apply_async(*a, **k):
        raise AssertionError(
            "digital fulfillment must not queue a vertical-agent job")

    monkeypatch.setattr(
        "sincor2.email_sender.EmailSender.send_email", fake_send_email)
    monkeypatch.setattr(rr.fulfill_pack_agent, "apply_async",
                        boom_apply_async)
    return {"sent": sent}


@pytest.fixture(scope="module")
def rail_app():
    from flask import Flask
    from sincor2.rail_delivery import init_rail_delivery_routes

    app = Flask(__name__)
    app.config["TESTING"] = True
    rr.init_rail_routes(app)
    init_rail_delivery_routes(app)
    return app


# --- helpers ---------------------------------------------------------------

def _sign_bind(acct, challenge_id, resource_id):
    msg = x402.challenge_bind_message(challenge_id, resource_id,
                                      acct.address)
    return acct.sign_message(encode_defunct(text=msg)).signature.hex()


def _bind(challenge_id, resource_id, acct):
    bound = x402.bind_challenge_payer(
        challenge_id, acct.address,
        _sign_bind(acct, challenge_id, resource_id))
    assert bound.get("ownership_proven") is True, bound
    return bound


def _challenge_row(challenge_id):
    with x402._conn() as conn:
        row = conn.execute(
            "SELECT status, payer_wallet, tx_hash FROM x402_challenges "
            "WHERE challenge_id=?", (challenge_id,)).fetchone()
    return dict(row) if row else None


def _checkout_pack(client, wallet, email="buyer@example.com"):
    res = client.post("/rail/checkout", json={
        "pack_sku": "digital_bi_template_pack", "payer_wallet": wallet,
        "buyer_email": email})
    assert res.status_code == 402, res.get_json()
    return res.get_json()


def _settle(client, challenge_id, tx_hash, wallet):
    return client.post("/rail/settle", json={
        "challenge_id": challenge_id, "tx_hash": tx_hash,
        "payer_wallet": wallet, "buyer_email": "buyer@example.com"})


def _rail_event_count(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM rail_events").fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Full loop: checkout (with wallet) -> bind -> pay -> settle -> download
# ---------------------------------------------------------------------------

def test_full_loop_bound_buyer_checkout_bind_pay_settle_download(
        rail_app, rail_env, mock_comms):
    client = rail_app.test_client()

    # 1. checkout with wallet: challenge minted UNBOUND, response
    #    carries the exact bind message for the buyer's wallet.
    body = _checkout_pack(client, VICTIM)
    cid = body["challenge_id"]
    assert body["payer_wallet"] == VICTIM.lower()
    assert body["sign_message"] == x402.challenge_bind_message(
        cid, body["resource_id"], VICTIM)
    assert body["bind"]["bind_url"] == "/api/x402/challenge-bind"
    assert _challenge_row(cid)["payer_wallet"] == ""

    # 2. bind: the buyer proves wallet ownership (EIP-191) — the step
    #    the rail pay page performs before paying.
    _bind(cid, body["resource_id"], VICTIM_ACCT)
    assert _challenge_row(cid)["payer_wallet"] == VICTIM.lower()

    # 3. pay: the victim's USDC transfer exists on the (stubbed) chain.
    # 4. settle: fulfilled, exactly one provisioning.
    res = _settle(client, cid, TX_PACK, VICTIM)
    assert res.status_code == 200, res.get_json()
    out = res.get_json()
    assert out["ok"] is True
    assert out["status"] == "fulfilled"
    fulfillment = out["fulfillment"]
    assert fulfillment["status"] == "delivered"

    # 5. download/serve works: the token-gated URL resolves over HTTP.
    download_url = fulfillment["download_url"]
    from urllib.parse import urlparse
    parsed = urlparse(download_url)
    intake_id = parsed.path.rsplit("/", 1)[-1]
    assert intake_id == fulfillment["intake_id"]
    assert "token=" in parsed.query, (
        "settle response must carry the token-gated download URL")
    # Pass path+query verbatim — no decode/re-encode round trip on
    # the token.
    resp = client.get(f"{parsed.path}?{parsed.query}")
    assert resp.status_code == 200
    assert resp.data  # the real asset bytes are served

    assert _rail_event_count(rail_env["tmp"] / "revenue.db") == 1
    assert len(mock_comms["sent"]) == 1


def test_unbound_buyer_refused_at_settle(rail_app, rail_env, mock_comms):
    """The intended behavior change: a buyer who never binds is
    refused at settle (challenge_unbound), same as the public route —
    even though their payment is real on-chain."""
    client = rail_app.test_client()
    body = _checkout_pack(client, VICTIM)
    cid = body["challenge_id"]
    # no bind step
    res = _settle(client, cid, TX_PACK, VICTIM)
    out = res.get_json()
    assert out["ok"] is False
    assert out["error"] == "challenge_unbound"
    assert _challenge_row(cid)["status"] == "pending"
    assert _rail_event_count(rail_env["tmp"] / "revenue.db") == 0
    assert len(mock_comms["sent"]) == 0


# ---------------------------------------------------------------------------
# Attack regression (hunt-29 PoC, adapted fresh against the app fixtures)
# ---------------------------------------------------------------------------

def test_attacker_unbound_challenge_cannot_claim_victim_tx(
        rail_app, rail_env, mock_comms):
    """PoC replay: attacker mints their OWN unbound challenge and
    settles FIRST with the victim's tx_hash and no payer proof.
    Pre-fix: fulfilled + download URL; victim then locked out.
    Post-fix: rejected, tx unclaimed, victim settles successfully."""
    client = rail_app.test_client()

    # Victim's legitimate flow up to (but not including) settle.
    victim_body = _checkout_pack(client, VICTIM)
    victim_cid = victim_body["challenge_id"]
    _bind(victim_cid, victim_body["resource_id"], VICTIM_ACCT)

    # Attacker mints their own challenge for the same SKU (no wallet,
    # no bind — exactly the PoC) and front-runs the settle.
    atk_body = _checkout_pack(client, "", email="")
    atk_cid = atk_body["challenge_id"]
    atk = _settle(client, atk_cid, TX_PACK, "")
    atk_out = atk.get_json()
    assert atk_out["ok"] is False
    assert atk_out["error"] == "challenge_unbound"
    assert "fulfillment" not in atk_out
    # The attacker's challenge is unfulfilled and the tx unclaimed.
    row = _challenge_row(atk_cid)
    assert row["status"] == "pending"
    assert row["tx_hash"] is None

    # The victim's own settle afterward SUCCEEDS.
    vic = _settle(client, victim_cid, TX_PACK, VICTIM)
    assert vic.status_code == 200, vic.get_json()
    vic_out = vic.get_json()
    assert vic_out["ok"] is True
    assert vic_out["status"] == "fulfilled"
    assert vic_out["fulfillment"]["download_url"]
    # Exactly one provisioning — the victim's.
    assert _rail_event_count(rail_env["tmp"] / "revenue.db") == 1
    assert len(mock_comms["sent"]) == 1


def test_attacker_bound_to_self_cannot_claim_victim_tx(
        rail_app, rail_env, mock_comms):
    """Stronger attacker: binds their own challenge to their OWN
    wallet, then tries to claim the victim's payment — naming the
    victim's wallet fails the challenge binding; naming their own
    fails the on-chain payer check."""
    client = rail_app.test_client()
    victim_body = _checkout_pack(client, VICTIM)
    victim_cid = victim_body["challenge_id"]
    _bind(victim_cid, victim_body["resource_id"], VICTIM_ACCT)

    atk_body = _checkout_pack(client, ATTACKER, email="")
    atk_cid = atk_body["challenge_id"]
    _bind(atk_cid, atk_body["resource_id"], ATTACKER_ACCT)

    named_victim = _settle(client, atk_cid, TX_PACK, VICTIM)
    assert named_victim.get_json()["error"] == "challenge_payer_mismatch"
    named_self = _settle(client, atk_cid, TX_PACK, ATTACKER)
    assert named_self.get_json()["error"] == "payer_wallet_mismatch"
    assert _challenge_row(atk_cid)["status"] == "pending"

    vic = _settle(client, victim_cid, TX_PACK, VICTIM)
    assert vic.get_json()["ok"] is True
    assert _rail_event_count(rail_env["tmp"] / "revenue.db") == 1


def test_attacker_cannot_bind_victim_wallet_without_victim_key(
        rail_app, rail_env, mock_comms):
    """The binding itself is proof-gated: an attacker who signs the
    victim's bind message with the ATTACKER key cannot bind the
    victim's wallet to the attacker's challenge (W-102r3), so the
    front-run cannot be re-created through the bind flow."""
    client = rail_app.test_client()
    atk_body = _checkout_pack(client, ATTACKER, email="")
    atk_cid = atk_body["challenge_id"]

    # Claiming VICTIM requires a signature over the VICTIM bind
    # message; the attacker signs it with the ATTACKER key.
    victim_msg = x402.challenge_bind_message(
        atk_cid, atk_body["resource_id"], VICTIM)
    forged_sig = ATTACKER_ACCT.sign_message(
        encode_defunct(text=victim_msg)).signature.hex()
    forged = x402.bind_challenge_payer(atk_cid, VICTIM, forged_sig)
    assert forged["ok"] is False
    assert forged["error"] == "ownership_proof_failed"
    assert _challenge_row(atk_cid)["payer_wallet"] == ""

    # And the unbound attacker challenge still cannot settle.
    atk = _settle(client, atk_cid, TX_PACK, VICTIM)
    assert atk.get_json()["error"] == "challenge_unbound"


# ---------------------------------------------------------------------------
# Subscriptions: bound at issuance, bound-consistent settle
# ---------------------------------------------------------------------------

def test_subscription_challenge_bound_at_issuance_and_settles(
        rail_app, rail_env, mock_comms):
    """issue_renewal_challenge binds the x402 row to the issued
    wallet, so the bound-required subscription settle succeeds for
    the legitimate subscriber (full loop through /rail/settle)."""
    client = rail_app.test_client()
    res = client.post("/rail/checkout", json={
        "subscription_plan": "starter", "payer_wallet": VICTIM})
    assert res.status_code == 402, res.get_json()
    cid = res.get_json()["challenge_id"]
    assert _challenge_row(cid)["payer_wallet"] == VICTIM.lower()

    out = _settle(client, cid, TX_SUB, VICTIM)
    assert out.status_code == 200, out.get_json()
    assert out.get_json()["ok"] is True
    status = rs.subscription_status(VICTIM)
    assert status["status"] == "active"
    assert status["plan_id"] == "starter"


def test_unbound_subscription_challenge_refused_at_settle(
        rail_app, rail_env, mock_comms):
    """A subscription challenge that was never bound at issuance
    (hand-minted x402 draft + an issued-wallet row, i.e. the pre-fix
    issuance shape) is refused by settle_renewal's bound check."""
    ch = x402.create_challenge("rail-sub-starter")
    cid = ch["challenge_id"]
    conn = rs._connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO rail_sub_challenges "
            "(challenge_id, wallet, plan_id, issued_at) VALUES (?,?,?,?)",
            (cid, VICTIM.lower(), "starter", "2026-10-26T00:00:00+00:00"))
        conn.commit()
    finally:
        conn.close()

    result = rs.settle_renewal(cid, TX_SUB, VICTIM)
    assert result["ok"] is False
    assert result["error"] == "challenge_unbound"
    assert rs.subscription_status(VICTIM)["status"] == "none"
