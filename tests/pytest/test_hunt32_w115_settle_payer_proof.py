"""Hunt-32 W-115 — rail settle duplicate/retry payer proof (money path).

CONFIRMED finding (red-team hunt 32, PoC poc32b_d2_buyer_flow.py):
``settle_rail_order``'s duplicate path returned the already-fulfilled
result — including the token-gated ``download_url`` and ``intake_url`` —
to ANY caller who knew the ``challenge_id``, with an arbitrary tx_hash
and no payer proof (B1). The retry path for a claimed-but-undelivered
order (e.g. treasury kill-switch gate denial, a designed stuck state)
let an unproven caller complete fulfillment with the victim's tx +
wallet strings and have the delivery emailed to an ATTACKER-supplied
address (B2). The settle endpoint was an unauthenticated entitlement
oracle: the only "secret" was the challenge_id, which leaks through
victim-shared intake/completion links.

Fix contract (founder-approved, mirroring the merged W-113 payer-proof
gate and reusing the W-103 replay mechanism):
  * duplicate settle: full result (download_url / intake_url) ONLY with
    a valid EIP-191 replay signature — personal_sign of
    ``x402.build_replay_message(challenge_id, ts_ms)`` — from the rail
    event's recorded (bound) payer wallet, inside the W-103 freshness
    window. Without it: a status-only ack (status + identifiers, NEVER
    a URL) and no email.
  * retry settle (claimed-but-incomplete): refused with
    ``payer_proof_required`` until the same proof is presented; on a
    proven retry the delivery email goes ONLY to the original buyer
    email recorded at claim time — a caller-supplied different email
    never receives the delivery.

The chain is a canned stub (the PoC's FakeChain shape): zero network,
zero real keys — Hardhat dev accounts #0/#1, test-only, never funded.
"""
from __future__ import annotations

import sqlite3
import time

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

import sincor2.platform_payments as pp
import sincor2.rail_routes as rr
import sincor2.x402_payments as x402
from sincor2 import payment_ledger as pl
from sincor2.governance.money_gate import reset_money_boundary

# Hardhat dev accounts #0 (victim) and #1 (attacker) — test-only.
VICTIM_ACCT = Account.from_key(
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
ATTACKER_ACCT = Account.from_key(
    "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d")
VICTIM = VICTIM_ACCT.address
ATTACKER = ATTACKER_ACCT.address

TX_V = "0x" + "ab" * 32        # victim's real payment tx
TX_V2 = "0x" + "cd" * 32       # victim's second purchase tx
TX_FAKE = "0x" + "ff" * 32     # arbitrary well-formed tx (B1)
SKU = "digital_bi_template_pack"
RES = "rail-pack-digital_bi_template_pack"
AMT = 97_000_000               # $97.00 in 6-decimal USDC
VICTIM_EMAIL = "victim@example.com"
ATTACKER_EMAIL = "attacker@example.com"

_SECP256K1_N = int(
    "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141", 16)


def _pad(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:].lower()


class FakeChain:
    """PoC-shaped stub: receipts only for registered (tx -> payer) rows."""

    def __init__(self, treasury: str):
        self.treasury = treasury
        self.t = {}

    def reg(self, tx, payer, amount=AMT):
        self.t[tx.lower()] = (payer, amount)

    def rpc(self, method, params, attempts=3):
        if method == "eth_getTransactionReceipt":
            r = self.t.get(params[0].lower())
            if r is None:
                return None
            payer, amount = r
            return {"status": "0x1", "blockNumber": hex(100), "logs": [{
                "address": pp.token_address("USDC"),
                "topics": [pp.TRANSFER_TOPIC, _pad(payer),
                           _pad(self.treasury)],
                "data": hex(amount)}]}
        if method == "eth_blockNumber":
            return hex(112)  # 13 confirmations — past finality depth
        raise AssertionError(method)


@pytest.fixture(autouse=True)
def _fresh_boundary():
    reset_money_boundary()
    yield
    reset_money_boundary()


@pytest.fixture()
def rail_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SINCOR_STAKE_LEDGER_PATH", str(tmp_path / "stake.json"))
    monkeypatch.setenv("SINCOR_STORE_DB_PATH", str(tmp_path / "store.db"))
    monkeypatch.setattr(
        "sincor2.treasury_inflow._LEDGER_PATH", tmp_path / "inflow.jsonl")
    monkeypatch.setattr("sincor2.agent_billing._log_path",
                        lambda: tmp_path / "platform_payments.jsonl")

    treasury = x402.get_resource(RES)["treasury"]
    chain = FakeChain(treasury)
    chain.reg(TX_V, VICTIM)
    chain.reg(TX_V2, VICTIM)
    monkeypatch.setattr(pp, "_rpc_call", chain.rpc)
    monkeypatch.setattr(pp, "screen_payer_sanctions",
                        lambda w: {"verdict": "pass", "detail": "clean"})
    pp._ofac_oracle = None
    x402.init_x402_db()
    pp.init_platform_payments_db()
    return {"tmp": tmp_path, "chain": chain}


@pytest.fixture()
def mock_comms(monkeypatch):
    sent = []

    def fake_send_email(self, to_email, to_name="", subject="",
                        html_content="", text_content=None, metadata=None):
        sent.append({"to": to_email, "html": html_content})
        return {"status": "sent", "message_id": "msg-test-w115"}

    def boom_apply_async(*a, **k):
        raise AssertionError(
            "digital fulfillment must not queue a vertical-agent job")

    monkeypatch.setattr(
        "sincor2.email_sender.EmailSender.send_email", fake_send_email)
    monkeypatch.setattr(rr.fulfill_pack_agent, "apply_async",
                        boom_apply_async)
    return {"sent": sent}


@pytest.fixture()
def rail_app():
    from sincor2.rail_delivery import init_rail_delivery_routes

    app = Flask(__name__)
    app.config["TESTING"] = True
    rr.init_rail_routes(app)
    init_rail_delivery_routes(app)
    return app


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _bind_sig(acct, cid, wallet=None):
    msg = x402.challenge_bind_message(cid, RES, wallet or acct.address)
    return acct.sign_message(encode_defunct(text=msg)).signature.hex()


def _replay_proof(acct, cid, ts_ms=None):
    """W-103 replay proof: EIP-191 sign of build_replay_message."""
    ts_ms = int(time.time() * 1000) if ts_ms is None else ts_ms
    sig = "0x" + acct.sign_message(
        encode_defunct(text=x402.build_replay_message(cid, ts_ms))
    ).signature.hex()
    return sig, ts_ms


def _checkout_and_bind(client, email=VICTIM_EMAIL):
    r = client.post("/rail/checkout", json={
        "pack_sku": SKU, "payer_wallet": VICTIM, "buyer_email": email})
    assert r.status_code == 402, r.get_json()
    cid = r.get_json()["challenge_id"]
    b = x402.bind_challenge_payer(cid, VICTIM, _bind_sig(VICTIM_ACCT, cid))
    assert b.get("ownership_proven") is True, b
    return cid


def _victim_buy_and_settle(client, tx=TX_V, email=VICTIM_EMAIL):
    cid = _checkout_and_bind(client, email=email)
    s = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": tx, "payer_wallet": VICTIM,
        "buyer_email": email})
    body = s.get_json()
    assert body.get("ok") is True and body.get("status") == "fulfilled", body
    return cid, body


def _db_counts(db_path):
    conn = sqlite3.connect(db_path)
    try:
        n_events = conn.execute(
            "SELECT COUNT(*) FROM rail_events").fetchone()[0]
        n_intakes = conn.execute(
            "SELECT COUNT(*) FROM pack_intakes").fetchone()[0]
        return n_events, n_intakes
    finally:
        conn.close()


def _assert_no_entitlement(body):
    """No token-gated URL anywhere in a settle response body."""
    fulfillment = body.get("fulfillment") or {}
    assert not fulfillment.get("download_url"), body
    assert not fulfillment.get("intake_url"), body


# ---------------------------------------------------------------------------
# B1 — duplicate settle is an entitlement oracle no longer
# ---------------------------------------------------------------------------

def test_b1_duplicate_settle_no_proof_status_only_ack(
        rail_app, rail_env, mock_comms):
    client = rail_app.test_client()
    cid, settle = _victim_buy_and_settle(client)
    assert (settle.get("fulfillment") or {}).get("download_url")
    mock_comms["sent"].clear()

    # PoC B1 exactly: victim's challenge_id + ARBITRARY tx + NO payer.
    dup = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_FAKE}).get_json()
    assert dup.get("status") == "already_fulfilled", dup
    assert dup.get("duplicate") is True, dup
    _assert_no_entitlement(dup)
    assert mock_comms["sent"] == [], "ack path must never send email"

    # Variant: real tx + victim wallet STRING (public data) — a bare
    # address is not proof of control.
    dup2 = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_V, "payer_wallet": VICTIM,
        "buyer_email": ATTACKER_EMAIL}).get_json()
    assert dup2.get("status") == "already_fulfilled", dup2
    _assert_no_entitlement(dup2)
    assert mock_comms["sent"] == []


def test_b1_duplicate_settle_attacker_signed_proof_refused(
        rail_app, rail_env, mock_comms):
    """A replay signature from the ATTACKER's key proves nothing."""
    client = rail_app.test_client()
    cid, _settle = _victim_buy_and_settle(client)

    sig, ts_ms = _replay_proof(ATTACKER_ACCT, cid)
    dup = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_V, "payer_wallet": ATTACKER,
        "replay_signature": sig, "replay_ts_ms": ts_ms}).get_json()
    assert dup.get("status") == "already_fulfilled", dup
    _assert_no_entitlement(dup)


def test_b1_proof_bound_to_challenge_and_freshness(
        rail_app, rail_env, mock_comms):
    """A victim signature minted for ANOTHER challenge, or outside the
    W-103 freshness window, must not unlock this order (no cross-order
    replay, no stale-signature replay)."""
    client = rail_app.test_client()
    cid, _settle = _victim_buy_and_settle(client)
    cid2, _settle2 = _victim_buy_and_settle(client, tx=TX_V2)

    # victim's valid proof for cid2 presented against cid
    sig, ts_ms = _replay_proof(VICTIM_ACCT, cid2)
    dup = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_V, "payer_wallet": VICTIM,
        "replay_signature": sig, "replay_ts_ms": ts_ms}).get_json()
    _assert_no_entitlement(dup)

    # victim's proof for cid but 11 minutes old (window is 10)
    stale_ts = int(time.time() * 1000) - 660_000
    sig, ts_ms = _replay_proof(VICTIM_ACCT, cid, ts_ms=stale_ts)
    dup = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_V, "payer_wallet": VICTIM,
        "replay_signature": sig, "replay_ts_ms": ts_ms}).get_json()
    _assert_no_entitlement(dup)


def test_b1_proof_high_s_malleation_refused(rail_app, rail_env, mock_comms):
    """The (r, n-s) malleation twin of the victim's valid proof recovers
    to the same address but is non-canonical — refused (codebase low-s
    standard, inherited from the W-103 check)."""
    client = rail_app.test_client()
    cid, _settle = _victim_buy_and_settle(client)

    sig, ts_ms = _replay_proof(VICTIM_ACCT, cid)
    raw = bytes.fromhex(sig[2:])
    r, s, v = raw[:32], int.from_bytes(raw[32:64], "big"), raw[64]
    twin = (r + (_SECP256K1_N - s).to_bytes(32, "big")
            + bytes([27 + ((v - 27) ^ 1)])).hex()
    dup = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_V, "payer_wallet": VICTIM,
        "replay_signature": "0x" + twin, "replay_ts_ms": ts_ms}).get_json()
    _assert_no_entitlement(dup)


def test_b1_duplicate_settle_with_payer_proof_returns_full_result(
        rail_app, rail_env, mock_comms):
    """Legit UX preserved: the proven payer's idempotent re-settle
    returns the ORIGINAL download URL (never a re-minted token)."""
    client = rail_app.test_client()
    cid, settle = _victim_buy_and_settle(client)
    url1 = settle["fulfillment"]["download_url"]
    token1 = url1.split("?token=")[1]

    sig, ts_ms = _replay_proof(VICTIM_ACCT, cid)
    dup = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_V, "payer_wallet": VICTIM,
        "buyer_email": VICTIM_EMAIL,
        "replay_signature": sig, "replay_ts_ms": ts_ms}).get_json()
    assert dup.get("status") == "already_fulfilled", dup
    assert dup.get("duplicate") is True, dup
    fulfillment = dup.get("fulfillment") or {}
    assert fulfillment.get("download_url") == url1, dup
    assert fulfillment.get("intake_url"), dup

    # no second token, no second intake, no second email
    db_path = rail_env["tmp"] / "revenue.db"
    assert _db_counts(db_path) == (1, 1)
    intake = pl.get_pack_intake(db_path, f"intake-rail-{cid}")
    assert (intake.details or {}).get("delivery_token") == token1
    assert len(mock_comms["sent"]) == 1


# ---------------------------------------------------------------------------
# B2 — gate-denied (stuck) order cannot be hijacked by an unproven retry
# ---------------------------------------------------------------------------

def _gate_denied_purchase(client, monkeypatch, tx=TX_V2):
    """Victim buys; the treasury kill-switch denies the first settle —
    the designed stuck state from the PoC."""
    import sincor2.governance.money_gate as mg

    cid = _checkout_and_bind(client)
    monkeypatch.setattr(mg, "treasury_halt_tripped", lambda: True)
    s1 = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": tx, "payer_wallet": VICTIM,
        "buyer_email": VICTIM_EMAIL}).get_json()
    assert s1.get("error") == "money_gate_denied", s1
    monkeypatch.setattr(mg, "treasury_halt_tripped", lambda: False)
    event = rr._get_rail_event(f"rail-{cid}")
    assert event["fulfillment_status"] == "gate_denied", event
    return cid


def test_b2_unproven_retry_cannot_complete_stuck_order(
        rail_app, rail_env, mock_comms, monkeypatch):
    client = rail_app.test_client()
    cid = _gate_denied_purchase(client, monkeypatch)
    mock_comms["sent"].clear()

    # PoC B2 exactly: victim's tx + victim wallet + ATTACKER email.
    s2 = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_V2, "payer_wallet": VICTIM,
        "buyer_email": ATTACKER_EMAIL}).get_json()
    assert s2.get("ok") is False, s2
    assert s2.get("error") == "payer_proof_required", s2
    _assert_no_entitlement(s2)

    # nothing moved: still stuck, no intake, no delivery email anywhere
    event = rr._get_rail_event(f"rail-{cid}")
    assert event["fulfillment_status"] == "gate_denied", event
    assert _db_counts(rail_env["tmp"] / "revenue.db") == (1, 0)
    assert mock_comms["sent"] == []

    # attacker-keyed proof does not help the attacker either
    sig, ts_ms = _replay_proof(ATTACKER_ACCT, cid)
    s3 = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_V2, "payer_wallet": VICTIM,
        "buyer_email": ATTACKER_EMAIL,
        "replay_signature": sig, "replay_ts_ms": ts_ms}).get_json()
    assert s3.get("ok") is False, s3
    assert s3.get("error") == "payer_proof_required", s3
    event = rr._get_rail_event(f"rail-{cid}")
    assert event["fulfillment_status"] == "gate_denied", event
    assert mock_comms["sent"] == []


def test_b2_proven_retry_completes_and_emails_original_buyer_only(
        rail_app, rail_env, mock_comms, monkeypatch):
    client = rail_app.test_client()
    cid = _gate_denied_purchase(client, monkeypatch)
    mock_comms["sent"].clear()

    # The proven payer retries — but the request's buyer_email field
    # points at the ATTACKER (tampered/foreign field). Delivery must
    # still go only to the original buyer email recorded at claim.
    sig, ts_ms = _replay_proof(VICTIM_ACCT, cid)
    s2 = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_V2, "payer_wallet": VICTIM,
        "buyer_email": ATTACKER_EMAIL,
        "replay_signature": sig, "replay_ts_ms": ts_ms}).get_json()
    assert s2.get("ok") is True, s2
    assert s2.get("status") == "fulfilled", s2
    fulfillment = s2.get("fulfillment") or {}
    assert fulfillment.get("status") == "delivered", s2
    assert fulfillment.get("download_url"), s2

    assert len(mock_comms["sent"]) == 1, mock_comms["sent"]
    assert mock_comms["sent"][0]["to"] == VICTIM_EMAIL
    assert all(m["to"] != ATTACKER_EMAIL for m in mock_comms["sent"])
    # the delivery link in the buyer's email is the same token-gated URL
    assert fulfillment["download_url"].split("https://getsincor.com")[-1] \
        in mock_comms["sent"][0]["html"]


# ---------------------------------------------------------------------------
# Full loop (money-path "wired" gate): buy -> settle -> attacks dead ->
# proven duplicate OK -> second buy stuck at gate -> attack dead ->
# proven retry completes -> token downloads the real asset
# ---------------------------------------------------------------------------

def test_w115_full_loop(rail_app, rail_env, mock_comms, monkeypatch):
    client = rail_app.test_client()

    # 1. buy + settle #1
    cid1, settle1 = _victim_buy_and_settle(client, tx=TX_V)
    url1 = settle1["fulfillment"]["download_url"]
    intake1 = settle1["fulfillment"]["intake_id"]

    # 2. B1 attack on the fulfilled order: dead (status-only ack)
    atk = client.post("/rail/settle", json={
        "challenge_id": cid1, "tx_hash": TX_FAKE}).get_json()
    assert atk.get("status") == "already_fulfilled", atk
    _assert_no_entitlement(atk)

    # 3. proven duplicate: original URL back, still one of everything
    sig, ts_ms = _replay_proof(VICTIM_ACCT, cid1)
    dup = client.post("/rail/settle", json={
        "challenge_id": cid1, "tx_hash": TX_V, "payer_wallet": VICTIM,
        "replay_signature": sig, "replay_ts_ms": ts_ms}).get_json()
    assert (dup.get("fulfillment") or {}).get("download_url") == url1, dup

    # 4. buy #2, gate-denied into the stuck state
    cid2 = _gate_denied_purchase(client, monkeypatch)

    # 5. B2 attack on the stuck order: dead, no state change, no email
    sent_before = len(mock_comms["sent"])
    atk2 = client.post("/rail/settle", json={
        "challenge_id": cid2, "tx_hash": TX_V2, "payer_wallet": VICTIM,
        "buyer_email": ATTACKER_EMAIL}).get_json()
    assert atk2.get("ok") is False, atk2
    assert atk2.get("error") == "payer_proof_required", atk2
    assert len(mock_comms["sent"]) == sent_before
    assert rr._get_rail_event(f"rail-{cid2}")["fulfillment_status"] \
        == "gate_denied"

    # 6. proven retry completes; email to the original buyer only
    sig, ts_ms = _replay_proof(VICTIM_ACCT, cid2)
    done = client.post("/rail/settle", json={
        "challenge_id": cid2, "tx_hash": TX_V2, "payer_wallet": VICTIM,
        "buyer_email": VICTIM_EMAIL,
        "replay_signature": sig, "replay_ts_ms": ts_ms}).get_json()
    assert done.get("ok") is True and done.get("status") == "fulfilled", done
    url2 = done["fulfillment"]["download_url"]
    assert [m["to"] for m in mock_comms["sent"]] == [VICTIM_EMAIL,
                                                     VICTIM_EMAIL]

    # 7. both token-gated downloads serve the real asset bytes
    for url in (url1, url2):
        path_q = url.split("https://getsincor.com", 1)[-1]
        resp = client.get(path_q)
        assert resp.status_code == 200, (url, resp.status_code)
        assert resp.data

    # exactly two events, two intakes, two finalized payments
    assert _db_counts(rail_env["tmp"] / "revenue.db") == (2, 2)
    log_lines = (rail_env["tmp"] / "platform_payments.jsonl"
                 ).read_text().splitlines()
    assert len(log_lines) == 2
    assert intake1 == f"intake-rail-{cid1}"
