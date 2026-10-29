"""x402/USDC rail for B2B skill-pack fulfillment.

Port of the parked Stripe tests (test_pack_fulfillment.py,
test_stripe_webhook_safety.py) to the rail flow:

  checkout: unknown SKU -> 400; price is server-derived (a client-supplied
      price is never trusted); PACK_SKUS agrees with the x402_pricing.yaml
      rail-pack-<sku> resources; malformed payer wallet -> 400
  settle (full loop, WIRED gate): checkout -> challenge -> settle with a
      stubbed RPC layer -> payment finalized in the platform ledger ->
      pack fulfilled (intake + buyer email + sincor.long job) -> re-settle
      is idempotent (exactly one intake, one email, one job; the existing
      result is returned, no double-provisioning)
  settle safety: rpc_failed/tx_pending fail closed (never treated as paid);
      a challenge for a non-pack resource is refused; an unknown challenge
      is refused; money-gate denial blocks provisioning and records an
      alert; a failed fulfillment is retryable
  mounting: /rail/checkout and /rail/settle exist on the create_app() test
      app (the x402-paid-blueprints-not-mounted finding must not repeat)

The "chain" is a canned _rpc_call stub (the fork-sim analog): zero network,
zero keys, nothing broadcast. No onchain addresses are invented — the
treasury, token address and decimals come from the repo's own modules.
"""
from __future__ import annotations

import sqlite3

import pytest

import sincor2.rail_routes as rr
import sincor2.platform_payments as pp
import sincor2.x402_payments as x402
from sincor2 import payment_ledger as pl
from sincor2.governance.money_gate import reset_money_boundary

# Canned chain values. The tx hash is a well-formed 0x + 64 hex string; the
# payer is a well-formed 0x address. Treasury / token address / decimals
# are read from the repo's own modules, never invented here.
TX_HASH = "0x" + "ab" * 32
WRONG_TX_HASH = "0x" + "cd" * 32

# Hunt-29: rail settle requires a BOUND challenge (require_bound_
# challenge=True), so the test payer must be a real account that can
# sign the EIP-191 bind message. This is the well-known PUBLIC Hardhat
# dev account #0 — test-only, never funded, never used off-test.
from eth_account import Account as _Account
from eth_account.messages import encode_defunct as _encode_defunct

_PAYER_ACCT = _Account.from_key(
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
PAYER = _PAYER_ACCT.address


def _bind_challenge(challenge_id, resource_id, acct=_PAYER_ACCT):
    """Complete the W-102r3 ownership bind for a minted challenge —
    the same step the rail pay page performs before paying."""
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

    def _receipt():
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
                "data": hex(amount_atomic),
            }],
        }

    def _fake_rpc(method, params, attempts=3):
        if method == "eth_getTransactionReceipt":
            assert params == [TX_HASH], params
            return _receipt()
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
    """Minimal Flask app with the real rail blueprint (mirrors the parked
    Stripe webhook-test pattern: module-global blueprint)."""
    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = True
    rr.init_rail_routes(app)
    return app


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


# ---------------------------------------------------------------------------
# pricing config: PACK_SKUS agrees with config/x402_pricing.yaml
# ---------------------------------------------------------------------------

def test_pack_resources_match_pack_skus():
    for sku, pack in rr.PACK_SKUS.items():
        resource = x402.get_resource(f"rail-pack-{sku}")
        assert resource is not None, f"resource missing for {sku}"
        assert resource["token"] == "USDC"
        expected_usdc = pack["price_cents"] / 100
        assert abs(resource["amount_usdc"] - expected_usdc) < 0.001, (
            f"{sku}: PACK_SKUS=${expected_usdc} != yaml "
            f"${resource['amount_usdc']}")
        assert resource["amount_display"] == expected_usdc
    assert set(rr.PACK_SKUS) == {
        "credentialing_launch", "chroma_compliance", "sadas_execution",
        "digital_ai_agent_bundle", "digital_bi_template_pack", "digital_growth_forecast", "webbuilder_site"}
def test_pack_agent_task_registered_on_sincor_long():
    assert rr.fulfill_pack_agent.name == "sincor2.fulfill_pack_agent"
    assert rr.PACK_AGENT_QUEUE == "sincor.long"


# ---------------------------------------------------------------------------
# checkout
# ---------------------------------------------------------------------------

def test_checkout_unknown_sku_400(rail_app, rail_env):
    client = rail_app.test_client()
    res = client.post("/rail/checkout", json={"pack_sku": "not_a_pack"})
    assert res.status_code == 400
    assert res.get_json()["error"].startswith("Unknown pack_sku")


def test_checkout_mints_challenge_with_server_price(rail_app, rail_env):
    client = rail_app.test_client()
    # A client-supplied price must be ignored: the server derives it.
    res = client.post("/rail/checkout", json={
        "pack_sku": "credentialing_launch",
        "price_cents": 1,  # tampered price — must be ignored
        "amount_usdc": 0.01,
        "payer_wallet": PAYER,
    })
    assert res.status_code == 402
    body = res.get_json()
    assert body["pack_sku"] == "credentialing_launch"
    assert body["pack_name"] == "Credentialing Launch"
    assert body["amount_usdc"] == 297.0
    assert body["challenge_id"]
    assert body["payment"]  # the x402 payment payload
    assert body["resource_id"] == "rail-pack-credentialing_launch"
    assert body["payer_wallet"] == PAYER.lower()


def test_checkout_invalid_payer_wallet_400(rail_app, rail_env):
    client = rail_app.test_client()
    res = client.post("/rail/checkout", json={
        "pack_sku": "credentialing_launch", "payer_wallet": "not-an-address"})
    assert res.status_code == 400
    assert res.get_json()["error"] == "invalid_payer_wallet"


def test_checkout_price_drift_fails_closed(rail_app, rail_env, monkeypatch):
    """PACK_SKUS vs YAML disagreement refuses to mint (never mis-charge)."""
    monkeypatch.setitem(rr.PACK_SKUS["credentialing_launch"], "price_cents", 1)
    client = rail_app.test_client()
    res = client.post("/rail/checkout",
                      json={"pack_sku": "credentialing_launch"})
    assert res.status_code == 500
    assert res.get_json()["error"] == "pack_price_mismatch"


# ---------------------------------------------------------------------------
# settle: full loop (checkout -> challenge -> settle -> ledger -> fulfill)
# ---------------------------------------------------------------------------

def _checkout_challenge(client, sku="credentialing_launch"):
    res = client.post("/rail/checkout", json={
        "pack_sku": sku, "payer_wallet": PAYER,
        "buyer_email": "buyer@example.com", "buyer_name": "Test Buyer"})
    assert res.status_code == 402
    body = res.get_json()
    _bind_challenge(body["challenge_id"], body["resource_id"])
    return body["challenge_id"]


def test_full_loop_checkout_settle_fulfill(rail_app, rail_env, mock_comms):
    """WIRED gate: the whole money path end to end on the stubbed chain."""
    client = rail_app.test_client()
    cid = _checkout_challenge(client)

    res = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_HASH, "payer_wallet": PAYER,
        "buyer_email": "buyer@example.com", "buyer_name": "Test Buyer"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["status"] == "fulfilled"
    assert body["pack_sku"] == "credentialing_launch"

    fulfillment = body["fulfillment"]
    assert fulfillment["status"] == "delivered"
    intake_id = fulfillment["intake_id"]
    assert intake_id == f"intake-rail-{cid}"
    assert fulfillment["intake_url"] == (
        f"https://getsincor.com/intake/{intake_id}")

    db_path = rail_env["tmp"] / "revenue.db"

    # exactly one rail event and exactly one intake record
    assert _db_counts(db_path) == (1, 1)
    event = rr._get_rail_event(f"rail-{cid}")
    assert event["fulfillment_status"] == "delivered"
    assert event["finalized"] == 1
    assert event["tx_hash"] == TX_HASH
    assert event["payer_wallet"] == PAYER.lower()

    intake = pl.get_pack_intake(db_path, intake_id)
    assert intake is not None
    assert intake.customer_email == "buyer@example.com"
    assert intake.pack_sku == "credentialing_launch"
    assert intake.event_id == f"rail-{cid}"

    # buyer emailed from the thank-you template, carrying the intake link
    assert len(mock_comms["sent"]) == 1
    email = mock_comms["sent"][0]
    assert email["to"] == "buyer@example.com"
    assert "Credentialing Launch" in email["html"]
    assert "Welcome to SINCOR" in email["html"]  # template marker
    assert fulfillment["intake_url"] in email["html"]

    # vertical-agent job queued on sincor.long
    assert len(mock_comms["queued"]) == 1
    queued = mock_comms["queued"][0]
    assert queued["queue"] == "sincor.long"
    assert queued["args"][0] == intake_id
    assert queued["args"][1] == "credentialing_launch"
    assert queued["args"][2] == "buyer@example.com"

    # the platform payment was finalized exactly once (JSONL append)
    log_lines = (rail_env["tmp"] / "platform_payments.jsonl").read_text().splitlines()
    assert len(log_lines) == 1

    # treasury inflow recorded, projected=False, carrying the tx hash
    inflow_lines = (rail_env["tmp"] / "inflow.jsonl").read_text().splitlines()
    assert len(inflow_lines) == 1

    # no alerts on the happy path
    assert pl.list_alerts(db_path) == []


def test_resettle_is_idempotent_no_double_provision(rail_app, rail_env,
                                                    mock_comms):
    client = rail_app.test_client()
    cid = _checkout_challenge(client)
    first = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_HASH, "payer_wallet": PAYER,
        "buyer_email": "buyer@example.com"})
    assert first.get_json()["ok"] is True

    second = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_HASH, "payer_wallet": PAYER,
        "buyer_email": "buyer@example.com"})
    body = second.get_json()
    assert second.status_code == 200
    assert body["ok"] is True
    assert body["status"] == "already_fulfilled"
    assert body["duplicate"] is True
    assert body["fulfillment"]["status"] == "delivered"
    assert body["fulfillment"]["intake_id"] == f"intake-rail-{cid}"

    # still exactly one of everything — no second email, job, or payment
    db_path = rail_env["tmp"] / "revenue.db"
    assert _db_counts(db_path) == (1, 1)
    assert len(mock_comms["sent"]) == 1
    assert len(mock_comms["queued"]) == 1
    log_lines = (rail_env["tmp"] / "platform_payments.jsonl").read_text().splitlines()
    assert len(log_lines) == 1


def test_settle_insufficient_amount_fails_closed(rail_app, rail_env,
                                                 mock_comms):
    """The stubbed receipt pays exactly $297 — settling it against the $997
    chroma_compliance pack fails closed before any provisioning."""
    client = rail_app.test_client()
    res = client.post("/rail/checkout",
                      json={"pack_sku": "chroma_compliance",
                            "payer_wallet": PAYER})
    cid = res.get_json()["challenge_id"]
    _bind_challenge(cid, res.get_json()["resource_id"])

    res = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_HASH, "payer_wallet": PAYER})
    body = res.get_json()
    assert res.status_code == 402
    assert body["ok"] is False
    assert body["error"] == "insufficient_amount"
    # nothing provisioned for an unverified payment
    assert len(mock_comms["sent"]) == 0
    assert len(mock_comms["queued"]) == 0


def test_wallet_only_buyer_fulfills_without_email(rail_app, rail_env,
                                                 mock_comms, monkeypatch):
    """Wallet-native buyers (no email) still get provisioned; no email is
    sent and the intake link is returned in the settle response."""
    amount_atomic = 997_000_000  # $997.00 for chroma_compliance
    treasury = rail_env["treasury"]
    token_addr = pp.token_address("USDC")

    def _fake_rpc(method, params, attempts=3):
        if method == "eth_getTransactionReceipt":
            assert params == [TX_HASH], params
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
                    "data": hex(amount_atomic),
                }],
            }
        if method == "eth_blockNumber":
            return hex(112)
        raise AssertionError(f"unexpected RPC method {method}")

    monkeypatch.setattr(pp, "_rpc_call", _fake_rpc)

    client = rail_app.test_client()
    res = client.post("/rail/checkout",
                      json={"pack_sku": "chroma_compliance",
                            "payer_wallet": PAYER})
    cid = res.get_json()["challenge_id"]
    _bind_challenge(cid, res.get_json()["resource_id"])
    res = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_HASH, "payer_wallet": PAYER})
    body = res.get_json()
    assert res.status_code == 200
    assert body["ok"] is True
    fulfillment = body["fulfillment"]
    assert fulfillment["status"] == "delivered"
    assert fulfillment["intake_url"].startswith("https://getsincor.com/intake/")
    # provisioned, but no email for a wallet-only buyer
    assert len(mock_comms["sent"]) == 0
    assert len(mock_comms["queued"]) == 1
    db_path = rail_env["tmp"] / "revenue.db"
    intake = pl.get_pack_intake(db_path, fulfillment["intake_id"])
    assert intake.pack_sku == "chroma_compliance"


# ---------------------------------------------------------------------------
# settle safety
# ---------------------------------------------------------------------------

def test_settle_rpc_failed_fails_closed(rail_app, rail_env, mock_comms,
                                        monkeypatch):
    def _boom(method, params, attempts=3):
        raise RuntimeError("rpc down")

    monkeypatch.setattr(pp, "_rpc_call", _boom)
    client = rail_app.test_client()
    cid = _checkout_challenge(client)
    res = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_HASH, "payer_wallet": PAYER})
    body = res.get_json()
    assert res.status_code == 402
    assert body["ok"] is False
    assert body["error"] == "rpc_failed"
    # nothing provisioned, no payment recorded
    assert len(mock_comms["sent"]) == 0
    assert len(mock_comms["queued"]) == 0


def test_settle_tx_pending_fails_closed(rail_app, rail_env, mock_comms,
                                        monkeypatch):
    def _pending(method, params, attempts=3):
        if method == "eth_getTransactionReceipt":
            return None  # not mined yet
        if method == "eth_blockNumber":
            return hex(112)
        raise AssertionError(method)

    monkeypatch.setattr(pp, "_rpc_call", _pending)
    client = rail_app.test_client()
    cid = _checkout_challenge(client)
    res = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_HASH, "payer_wallet": PAYER})
    body = res.get_json()
    assert res.status_code == 402
    assert body["ok"] is False
    assert body["error"] == "tx_pending"
    assert len(mock_comms["sent"]) == 0
    assert len(mock_comms["queued"]) == 0


def test_settle_unknown_challenge_400(rail_app, rail_env):
    client = rail_app.test_client()
    res = client.post("/rail/settle", json={
        "challenge_id": "x402-nope", "tx_hash": TX_HASH})
    body = res.get_json()
    assert body["ok"] is False
    assert body["error"] == "challenge_not_found"


def test_settle_missing_params_400(rail_app, rail_env):
    client = rail_app.test_client()
    res = client.post("/rail/settle", json={"challenge_id": ""})
    assert res.status_code == 400
    assert res.get_json()["error"] == "challenge_id_and_tx_hash_required"


def test_settle_non_pack_challenge_refused(rail_app, rail_env, mock_comms,
                                           monkeypatch):
    """A challenge for a non-pack resource must not provision a pack.

    The stubbed receipt carries a valid SINC transfer for the agent_task
    resource, so on-chain verification SUCCEEDS — the refusal comes from
    the rail's pack-resource check, not from a verify failure.
    """
    treasury = x402.get_resource("agent_task")["treasury"]
    token_addr = pp.token_address("SINC")
    amount_atomic = 100_000_000  # 1.0 SINC, 8 decimals

    def _fake_rpc(method, params, attempts=3):
        if method == "eth_getTransactionReceipt":
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
                    "data": hex(amount_atomic),
                }],
            }
        if method == "eth_blockNumber":
            return hex(112)
        raise AssertionError(f"unexpected RPC method {method}")

    monkeypatch.setattr(pp, "_rpc_call", _fake_rpc)
    ch = x402.create_challenge("agent_task")
    assert ch["challenge_id"]
    # Bound to the payer (Hunt-29: settle requires a bound challenge) so
    # verification SUCCEEDS and the refusal under test is the rail's
    # pack-resource check, not the binding gate.
    _bind_challenge(ch["challenge_id"], "agent_task")
    res = rail_app.test_client().post("/rail/settle", json={
        "challenge_id": ch["challenge_id"], "tx_hash": TX_HASH,
        "payer_wallet": PAYER})
    body = res.get_json()
    assert res.status_code == 400
    assert body["ok"] is False
    assert body["error"] == "not_a_pack_challenge"
    assert body["resource_id"] == "agent_task"
    assert len(mock_comms["sent"]) == 0
    assert len(mock_comms["queued"]) == 0
    # and the refusal left no rail residue
    assert rr._get_rail_event(f"rail-{ch['challenge_id']}") is None


def test_settle_retry_tx_hash_mismatch_fails_closed(rail_app, rail_env,
                                                    mock_comms, monkeypatch):
    """A retry naming a different tx_hash than the claim is refused —
    the first tx won the x402 fulfill, a second tx is a different payment.
    Uses a gate-denied (non-delivered) event so the mismatch guard runs."""
    import sincor2.governance.money_gate as mg

    monkeypatch.setattr(mg, "treasury_halt_tripped", lambda: True)
    client = rail_app.test_client()
    cid = _checkout_challenge(client)
    first = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_HASH, "payer_wallet": PAYER,
        "buyer_email": "buyer@example.com"})
    assert first.get_json()["error"] == "money_gate_denied"

    res = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": WRONG_TX_HASH, "payer_wallet": PAYER})
    body = res.get_json()
    assert res.status_code == 400
    assert body["ok"] is False
    assert body["error"] == "tx_hash_mismatch"
    assert len(mock_comms["queued"]) == 0


def test_settle_crash_window_recovers_with_replay_signature(
        rail_app, rail_env, mock_comms, monkeypatch):
    """Narrow crash window: the x402 challenge was fulfilled but the rail
    never claimed it. A plain re-settle fails closed (replay_auth_failed
    with a recovery hint); re-settling WITH the payer's EIP-191 replay
    signature completes the pipeline exactly once."""
    import time
    from eth_account import Account
    from eth_account.messages import encode_defunct

    acct = Account.create()
    payer = acct.address
    treasury = rail_env["treasury"]
    token_addr = pp.token_address("USDC")

    def _fake_rpc(method, params, attempts=3):
        if method == "eth_getTransactionReceipt":
            return {
                "status": "0x1",
                "blockNumber": hex(100),
                "logs": [{
                    "address": token_addr,
                    "topics": [
                        pp.TRANSFER_TOPIC,
                        "0x" + "0" * 24 + payer[2:].lower(),
                        "0x" + "0" * 24 + treasury[2:].lower(),
                    ],
                    "data": hex(297_000_000),
                }],
            }
        if method == "eth_blockNumber":
            return hex(112)
        raise AssertionError(f"unexpected RPC method {method}")

    monkeypatch.setattr(pp, "_rpc_call", _fake_rpc)

    # out-of-band fulfill (simulates the crashed first settle)
    ch = x402.create_challenge("rail-pack-credentialing_launch")
    cid = ch["challenge_id"]
    vr = x402.verify_challenge(cid, TX_HASH, payer_wallet=payer)
    assert vr["ok"] is True and vr["status"] == "fulfilled"
    assert rr._get_rail_event(f"rail-{cid}") is None

    client = rail_app.test_client()
    plain = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_HASH, "payer_wallet": payer,
        "buyer_email": "buyer@example.com"})
    plain_body = plain.get_json()
    assert plain_body["ok"] is False
    assert plain_body["error"] == "replay_auth_failed"
    assert "replay_hint" in plain_body
    assert len(mock_comms["queued"]) == 0

    # the payer signs the W-103 replay message and re-settles
    ts_ms = int(time.time() * 1000)
    sig = "0x" + acct.sign_message(
        encode_defunct(text=x402.build_replay_message(cid, ts_ms))).signature.hex()
    res = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_HASH, "payer_wallet": payer,
        "buyer_email": "buyer@example.com",
        "replay_signature": sig, "replay_ts_ms": ts_ms})
    body = res.get_json()
    assert res.status_code == 200
    assert body["ok"] is True
    assert body["status"] == "fulfilled"
    assert body["fulfillment"]["status"] == "delivered"
    # exactly one payment finalized, one intake, one job
    assert len(mock_comms["queued"]) == 1
    log_lines = (rail_env["tmp"] / "platform_payments.jsonl").read_text().splitlines()
    assert len(log_lines) == 1


def test_settle_money_gate_denial_blocks_provisioning(
        rail_app, rail_env, mock_comms, monkeypatch):
    import sincor2.governance.money_gate as mg

    monkeypatch.setattr(mg, "treasury_halt_tripped", lambda: True)
    client = rail_app.test_client()
    cid = _checkout_challenge(client)
    res = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_HASH, "payer_wallet": PAYER,
        "buyer_email": "buyer@example.com"})
    body = res.get_json()
    assert body["ok"] is False
    assert body["error"] == "money_gate_denied"

    # payment WAS finalized (money moved onchain) but nothing provisioned
    db_path = rail_env["tmp"] / "revenue.db"
    event = rr._get_rail_event(f"rail-{cid}")
    assert event["finalized"] == 1
    assert event["fulfillment_status"] == "gate_denied"
    assert len(mock_comms["sent"]) == 0
    assert len(mock_comms["queued"]) == 0
    assert _db_counts(db_path) == (1, 0)  # no intake record
    alerts = pl.list_alerts(db_path)
    assert any(a.kind == "pack_fulfillment_gate_denied" for a in alerts)


def test_failed_fulfillment_is_retryable(rail_app, rail_env, mock_comms,
                                         monkeypatch):
    """A failed fulfillment is NOT treated as delivered — re-settle retries
    instead of acking a duplicate (F1 crash-retry residual stays scoped)."""
    def _boom(*a, **k):
        raise RuntimeError("broker down")

    monkeypatch.setattr(rr.fulfill_pack_agent, "apply_async", _boom)
    client = rail_app.test_client()
    cid = _checkout_challenge(client)
    first = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_HASH, "payer_wallet": PAYER,
        "buyer_email": "buyer@example.com"})
    first_body = first.get_json()
    assert first_body["ok"] is False
    assert "broker down" in first_body["fulfillment"]["error"]

    event = rr._get_rail_event(f"rail-{cid}")
    assert event["fulfillment_status"] == "failed"
    # intake + email happened before the queue failure; job did not
    assert _db_counts(rail_env["tmp"] / "revenue.db") == (1, 1)
    assert len(mock_comms["sent"]) == 1
    assert len(mock_comms["queued"]) == 0

    # broker recovers: re-settle reprocesses (x402 returns already_fulfilled
    # internally) and does NOT re-finalize the payment.
    # W-115 (hunt 32): a retry must carry the payer's W-103 replay proof —
    # an unproven retry is refused with payer_proof_required (this call
    # previously pinned the unauthenticated retry the hunt exploited).
    monkeypatch.setattr(
        rr.fulfill_pack_agent, "apply_async", mock_comms["fake_apply_async"])
    import time as _time

    _ts_ms = int(_time.time() * 1000)
    _sig = "0x" + _PAYER_ACCT.sign_message(_encode_defunct(
        text=x402.build_replay_message(cid, _ts_ms))).signature.hex()
    second = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_HASH, "payer_wallet": PAYER,
        "buyer_email": "buyer@example.com",
        "replay_signature": _sig, "replay_ts_ms": _ts_ms})
    second_body = second.get_json()
    assert second_body["ok"] is True
    assert second_body["status"] == "fulfilled"
    assert second_body["fulfillment"]["status"] == "delivered"
    assert _db_counts(rail_env["tmp"] / "revenue.db") == (1, 1)
    log_lines = (rail_env["tmp"] / "platform_payments.jsonl").read_text().splitlines()
    assert len(log_lines) == 1  # still exactly one payment record


# ---------------------------------------------------------------------------
# mounting: routes exist on the real app factory
# ---------------------------------------------------------------------------

def test_rail_routes_mounted_on_create_app(rail_env):
    from sincor2.app import create_app

    app = create_app()
    rules = {str(r) for r in app.url_map.iter_rules()}
    assert "/rail/checkout" in rules
    assert "/rail/settle" in rules


# ---------------------------------------------------------------------------
# P0-1: revenue_orchestrator import must never raise on an unwritable fs.
#
# In production the app runs as appuser under a root-owned /app, so the
# import-time Path("logs").mkdir(...) raised PermissionError; because
# rail_routes imports revenue_orchestrator at top level, mvp_app's rail
# mount then failed and the whole rail was silently absent. The import is
# exercised in a subprocess with pathlib.Path.mkdir guarded to deny any
# directory creation outside SINCOR_DATA_DIR (a faithful read-only-cwd
# simulation: mkdir on an already-existing path still succeeds, exactly
# like mkdir(2) returning EEXIST on a read-only filesystem).
# ---------------------------------------------------------------------------

_IMPORT_DRIVER = r"""
import os, pathlib
_real_mkdir = pathlib.Path.mkdir

def _guarded_mkdir(self, mode=0o777, parents=False, exist_ok=False):
    if self.exists():
        if exist_ok:
            return None
        raise FileExistsError(str(self))
    _allowed = os.environ.get("SINCOR_DATA_DIR", "")
    if _allowed and str(self).startswith(_allowed):
        return _real_mkdir(self, mode, parents, exist_ok)
    raise PermissionError(13, "Permission denied: %s" % self)

pathlib.Path.mkdir = _guarded_mkdir

import sincor2.revenue_orchestrator as ro
print("orchestrator import OK")

from flask import Flask
from sincor2.rail_routes import init_rail_routes
_app = Flask(__name__)
init_rail_routes(_app)
_rules = {str(r) for r in _app.url_map.iter_rules()}
assert "/rail/checkout" in _rules and "/rail/settle" in _rules, _rules
print("RAIL IMPORT OK")
"""


def _run_import_driver(tmp_path, data_dir):
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo_root / "src"), str(repo_root)])
    env["ORDERS_DB_PATH"] = str(tmp_path / "orders.db")
    if data_dir is None:
        env.pop("SINCOR_DATA_DIR", None)
    else:
        env["SINCOR_DATA_DIR"] = str(data_dir)
    return subprocess.run(
        [sys.executable, "-c", _IMPORT_DRIVER],
        cwd=str(tmp_path), env=env, capture_output=True, text=True,
        timeout=180)


def test_revenue_orchestrator_import_survives_unwritable_cwd(tmp_path):
    # SINCOR_DATA_DIR set (the Railway layout): logs + revenue data root
    # there, and creating anything outside it is denied.
    data_dir = tmp_path / "sdata"
    data_dir.mkdir()
    proc = _run_import_driver(tmp_path, data_dir)
    assert proc.returncode == 0, proc.stderr
    assert "RAIL IMPORT OK" in proc.stdout
    assert (data_dir / "logs").is_dir()
    assert (data_dir / "revenue").is_dir()


def test_revenue_orchestrator_import_survives_unwritable_cwd_no_data_dir(
        tmp_path):
    # SINCOR_DATA_DIR unset and NOTHING may be created: import must still
    # succeed (logging degrades to stream-only; ledger dir setup is
    # best-effort) and the rail must still mount.
    proc = _run_import_driver(tmp_path, None)
    assert proc.returncode == 0, proc.stderr
    assert "RAIL IMPORT OK" in proc.stdout
