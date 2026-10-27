"""x402/USDC rail: token-gated digital delivery (zero-to-revenue rail rework w4).

Rework of the parked Stripe branch's test_digital_delivery.py onto the
x402 rail. The money_assets/ digital packs ($97 / $297 / $147) are bought
through the rail instead of Stripe checkout:

  - the three digital SKUs are registered in DIGITAL_SKUS (server-side
    source of truth) and merged into the rail's PACK_SKUS; prices match
    money_assets/HOW_TO_MAKE_MONEY_NOW.md and the rail-pack-digital_*
    x402 resources
  - full loop per SKU: /rail/checkout (client price ignored) ->
    /rail/settle (stubbed RPC) -> exactly one delivery token issued ->
    GET /download/<intake_id>?token=<token> serves the real asset bytes
  - re-settle returns the ORIGINAL token (no second issuance), the ack
    carries the same download_url
  - break-it probes (ported): tampered price fails closed
    (insufficient_amount, nothing provisioned), wrong / cross-intake /
    blank / missing tokens and unknown intakes -> 404, poisoned asset in
    an intake row rejected by verify_digital_delivery, duplicate settle ->
    exactly one delivery (no second email, no agent job, no second token)
  - digital fulfillment NEVER queues a vertical-agent job
  - the "43 agents" claim is gone from money_assets/ (live card: 17 skills)

The "chain" is a canned _rpc_call stub: zero network, zero keys, nothing
broadcast. No onchain addresses are invented — the treasury, token
address and decimals come from the repo's own modules.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

import sincor2.rail_routes as rr
import sincor2.revenue_orchestrator as ro
import sincor2.platform_payments as pp
import sincor2.x402_payments as x402
from sincor2 import payment_ledger as pl
from sincor2.governance.money_gate import reset_money_boundary

# Canned chain values. The tx hash is a well-formed 0x + 64 hex string; the
# payer is a well-formed 0x address. Treasury / token address / decimals
# are read from the repo's own modules, never invented here.
TX_HASH = "0x" + "ab" * 32
TX_HASH_2 = "0x" + "cd" * 32

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

DIGITAL_CASES = [
    # (pack_sku, price_cents, asset file)
    ("digital_bi_template_pack", 9700, "instant_bi_template_pack.md"),
    ("digital_ai_agent_bundle", 29700, "ai_agent_bundle.md"),
    ("digital_growth_forecast", 14700, "growth_forecast_template.md"),
]


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fresh_boundary():
    reset_money_boundary()
    yield
    reset_money_boundary()


@pytest.fixture()
def rail_env(tmp_path, monkeypatch):
    """Hermetic stores + stubbed RPC with a settable payment amount."""
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "sincor2.treasury_inflow._LEDGER_PATH", tmp_path / "inflow.jsonl")
    monkeypatch.setattr("sincor2.agent_billing._log_path",
                        lambda: tmp_path / "platform_payments.jsonl")

    treasury = x402.get_resource(
        "rail-pack-digital_bi_template_pack")["treasury"]
    token_addr = pp.token_address("USDC")
    amounts = {"atomic": 97_000_000}  # $97.00 in 6-decimal USDC

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
                "data": hex(amounts["atomic"]),
            }],
        }

    def _fake_rpc(method, params, attempts=3):
        if method == "eth_getTransactionReceipt":
            assert params == [TX_HASH] or params == [TX_HASH_2], params
            return _receipt()
        if method == "eth_blockNumber":
            return hex(112)  # 13 confirmations — past finality depth
        raise AssertionError(f"unexpected RPC method {method}")

    monkeypatch.setattr(pp, "_rpc_call", _fake_rpc)
    x402.init_x402_db()
    env = {"tx_hash": TX_HASH, "payer": PAYER, "treasury": treasury,
           "tmp": tmp_path}
    # Re-point the stubbed on-chain payment at another price point
    # (USDC has 6 decimals: cents * 10_000).
    env["pay_cents"] = lambda cents: amounts.update(
        atomic=int(cents * 10_000))
    return env


@pytest.fixture()
def mock_comms(monkeypatch):
    """Capture outbound email; nothing leaves the process.

    Digital packs must NOT queue a Celery agent job — any attempt to do so
    raises so the test fails loudly.
    """
    sent = []

    def fake_send_email(self, to_email, to_name="", subject="",
                        html_content="", text_content=None, metadata=None):
        sent.append({
            "to": to_email, "to_name": to_name, "subject": subject,
            "html": html_content, "text": text_content,
            "metadata": metadata or {},
        })
        return {"status": "sent", "message_id": "msg-test-digital"}

    def boom_apply_async(*a, **k):
        raise AssertionError(
            "digital fulfillment must not queue a vertical-agent job")

    monkeypatch.setattr(
        "sincor2.email_sender.EmailSender.send_email", fake_send_email)
    monkeypatch.setattr(rr.fulfill_pack_agent, "apply_async", boom_apply_async)
    return {"sent": sent}


@pytest.fixture(scope="module")
def rail_app():
    """Minimal Flask app with the rail + digital-delivery blueprints."""
    from flask import Flask
    from sincor2.rail_delivery import init_rail_delivery_routes

    app = Flask(__name__)
    app.config["TESTING"] = True
    rr.init_rail_routes(app)
    init_rail_delivery_routes(app)
    return app


def _repo_root() -> Path:
    return Path(ro.__file__).resolve().parents[2]


def _extract_download_link(html: str) -> tuple[str, str]:
    """Return (intake_id, token) from the token-gated download link."""
    m = re.search(
        r"https://getsincor\.com/download/([^\"'?\s]+)\?token=([A-Za-z0-9\-_]+)",
        html)
    assert m, "no token-gated download link found in buyer email"
    return m.group(1), m.group(2)


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


def _checkout_digital(client, pack_sku, price_cents):
    """Checkout a digital pack; the client-supplied price must be ignored."""
    res = client.post("/rail/checkout", json={
        "pack_sku": pack_sku,
        "price_cents": 1,  # tampered price — must be ignored
        "amount_usdc": 0.01,
        "payer_wallet": PAYER,
        "buyer_email": "buyer@example.com",
        "buyer_name": "Digital Buyer",
    })
    assert res.status_code == 402, res.get_json()
    body = res.get_json()
    assert body["amount_usdc"] == price_cents / 100, (
        "server-side digital pack price must win")
    assert body["resource_id"] == f"rail-pack-{pack_sku}"
    _bind_challenge(body["challenge_id"], body["resource_id"])
    return body["challenge_id"]


def _settle(client, challenge_id, tx_hash=TX_HASH, payer=PAYER,
            email="buyer@example.com"):
    return client.post("/rail/settle", json={
        "challenge_id": challenge_id, "tx_hash": tx_hash,
        "payer_wallet": payer, "buyer_email": email,
        "buyer_name": "Digital Buyer"})


# ---------------------------------------------------------------------------
# SKU registration: documented prices, real assets, catalog + yaml parity
# ---------------------------------------------------------------------------

def test_digital_skus_registered_with_documented_prices():
    doc = (_repo_root() / "money_assets" / "HOW_TO_MAKE_MONEY_NOW.md"
           ).read_text(encoding="utf-8")
    documented = {"digital_bi_template_pack": 97,
                  "digital_ai_agent_bundle": 297,
                  "digital_growth_forecast": 147}
    for sku, dollars in documented.items():
        assert f"${dollars}" in doc, f"${dollars} missing from money doc"
        assert ro.DIGITAL_SKUS[sku]["price_cents"] == dollars * 100, (
            f"{sku}: DIGITAL_SKUS price does not match the documented "
            f"${dollars}")
        # digital SKUs ride the rail's checkout/settle machinery as pack SKUs
        assert rr.PACK_SKUS[sku]["price_cents"] == dollars * 100

    # digital flag + asset binding, assets really exist on disk
    for sku, _cents, asset_file in DIGITAL_CASES:
        pack = rr.PACK_SKUS[sku]
        assert pack.get("digital") is True, f"{sku} not flagged digital"
        assert pack["asset"].endswith(asset_file)
        assert (_repo_root() / pack["asset"]).is_file(), (
            f"{sku}: asset {pack['asset']} missing from repo")

    # products/catalog.json carries the same three digital products
    catalog = json.loads(
        (_repo_root() / "products" / "catalog.json").read_text())
    dig = {p["pack_sku"]: p for p in catalog["digital_products"]}
    for sku, cents, asset_file in DIGITAL_CASES:
        assert dig[sku]["price_cents"] == cents
        assert dig[sku]["asset"].endswith(asset_file)


def test_digital_resources_match_digital_skus():
    """config/x402_pricing.yaml rail-pack-digital_* resources agree with
    the server-side table — the checkout route fails closed on drift."""
    for sku, cents, _asset_file in DIGITAL_CASES:
        resource = x402.get_resource(f"rail-pack-{sku}")
        assert resource is not None, f"resource missing for {sku}"
        assert resource["token"] == "USDC"
        expected_usdc = cents / 100
        assert abs(resource["amount_usdc"] - expected_usdc) < 0.001, (
            f"{sku}: DIGITAL_SKUS=${expected_usdc} != yaml "
            f"${resource['amount_usdc']}")


def test_no_43_agent_claims_in_money_assets():
    """The live agent card has 17 skills — the '43 agents' claim is gone
    from the ported money_assets files.

    NOTE: money_assets/HOW_TO_MAKE_MONEY_NOW.md still carries 43-claims —
    the parked branch fixed those too, but that file is outside this
    rework's scope; flagged for follow-up.
    """
    for name in ("ai_agent_bundle.md", "PRODUCT_CATALOG.json"):
        text = (_repo_root() / "money_assets" / name).read_text(
            encoding="utf-8")
        assert not re.search(r"43[-\s]?(pre[-\s]?built|pre[-\s]?configured)?\s*agent",
                             text, re.IGNORECASE), (
            f"'43 agents' claim still present in {name}")


def test_issue_delivery_token_properties():
    """32 bytes of entropy: 43-char urlsafe tokens, unique per call."""
    seen = {ro.issue_delivery_token() for _ in range(50)}
    assert len(seen) == 50, "delivery tokens must be unique"
    for token in seen:
        assert re.fullmatch(r"[A-Za-z0-9\-_]{43}", token), (
            f"token {token!r} is not a 32-byte token_urlsafe value")


# ---------------------------------------------------------------------------
# full loop per digital SKU: checkout -> settle -> token -> download
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pack_sku,price_cents,asset_file", DIGITAL_CASES)
def test_digital_full_loop_checkout_settle_download(
        rail_app, rail_env, mock_comms, pack_sku, price_cents, asset_file):
    """WIRED gate: digital pack end to end on the stubbed chain."""
    rail_env["pay_cents"](price_cents)
    client = rail_app.test_client()

    # 1. checkout — server-side price wins over the tampered client price
    cid = _checkout_digital(client, pack_sku, price_cents)

    # 2. settle — the stubbed receipt pays exactly the pack price
    res = _settle(client, cid)
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["ok"] is True
    assert body["status"] == "fulfilled"
    assert body["pack_sku"] == pack_sku

    fulfillment = body["fulfillment"]
    assert fulfillment["status"] == "delivered"
    assert fulfillment["digital"] is True
    intake_id = fulfillment["intake_id"]
    assert intake_id == f"intake-rail-{cid}"
    download_url = fulfillment["download_url"]
    assert download_url.startswith(
        f"https://getsincor.com/download/{intake_id}?token="), (
        "settle response must carry the token-gated download URL")

    db_path = rail_env["tmp"] / "revenue.db"

    # exactly one rail event and exactly one intake record
    assert _db_counts(db_path) == (1, 1)
    event = rr._get_rail_event(f"rail-{cid}")
    assert event["fulfillment_status"] == "delivered"
    assert event["finalized"] == 1

    intake = pl.get_pack_intake(db_path, intake_id)
    assert intake is not None
    assert intake.customer_email == "buyer@example.com"
    assert intake.pack_sku == pack_sku
    token = (intake.details or {}).get("delivery_token")
    assert token, "intake row must bind the delivery token"
    assert (intake.details or {}).get("asset", "").endswith(asset_file)
    assert download_url.endswith(f"?token={token}")

    # buyer emailed from the thank-you template, carrying the download link
    sent = mock_comms["sent"]
    assert len(sent) == 1
    email = sent[0]
    assert email["to"] == "buyer@example.com"
    assert rr.PACK_SKUS[pack_sku]["name"] in email["html"]
    assert "Welcome to SINCOR" in email["html"]  # template marker
    assert "download" in email["subject"].lower()
    assert download_url in email["html"]
    assert pl.list_alerts(db_path) == []

    # 3. the download link works: token-gated, resolves to the real asset
    link_intake, link_token = _extract_download_link(email["html"])
    assert (link_intake, link_token) == (intake_id, token)
    # intake id alone (no token) is useless
    with pytest.raises(ValueError):
        ro.verify_digital_delivery(intake_id, "", db_path=db_path)
    asset = ro.verify_digital_delivery(intake_id, token, db_path=db_path)
    assert asset.endswith(asset_file)

    resp = client.get(f"/download/{intake_id}?token={token}")
    assert resp.status_code == 200
    expected = (_repo_root() / ro.DIGITAL_SKUS[pack_sku]["asset"]).read_bytes()
    assert resp.data == expected, "route must serve the real asset bytes"
    assert resp.headers["Content-Type"] == "text/markdown; charset=utf-8"

    # 4. break-it: wrong / blank / missing token and unknown intake -> 404
    assert client.get(
        f"/download/{intake_id}?token=wrong-token").status_code == 404
    assert client.get(f"/download/{intake_id}?token=").status_code == 404
    assert client.get(f"/download/{intake_id}").status_code == 404
    assert client.get(
        "/download/intake-does-not-exist?token=whatever").status_code == 404


def test_wallet_only_digital_buyer_gets_download_url_in_settle_response(
        rail_app, rail_env, mock_comms):
    """Wallet-native buyers (no email) still get the download URL in the
    settle response — the link is the delivery mechanism."""
    rail_env["pay_cents"](9700)
    client = rail_app.test_client()
    res = client.post("/rail/checkout", json={
        "pack_sku": "digital_bi_template_pack", "payer_wallet": PAYER})
    cid = res.get_json()["challenge_id"]
    _bind_challenge(cid, res.get_json()["resource_id"])

    res = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_HASH, "payer_wallet": PAYER})
    body = res.get_json()
    assert res.status_code == 200
    assert body["ok"] is True
    fulfillment = body["fulfillment"]
    assert fulfillment["status"] == "delivered"
    assert fulfillment["digital"] is True
    download_url = fulfillment["download_url"]
    m = re.search(r"/download/([^?]+)\?token=([A-Za-z0-9\-_]+)", download_url)
    assert m, "settle response must carry the download URL"
    assert len(mock_comms["sent"]) == 0, "no email for a wallet-only buyer"

    resp = client.get(f"/download/{m.group(1)}?token={m.group(2)}")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# idempotency: re-settle returns the ORIGINAL token — never mints a second
# ---------------------------------------------------------------------------

def test_resettle_returns_original_token_no_second_issuance(
        rail_app, rail_env, mock_comms):
    client = rail_app.test_client()
    rail_env["pay_cents"](29700)
    cid = _checkout_digital(client, "digital_ai_agent_bundle", 29700)

    first = _settle(client, cid)
    first_body = first.get_json()
    assert first_body["ok"] is True
    url1 = first_body["fulfillment"]["download_url"]
    token1 = url1.split("?token=")[1]
    assert len(token1) == 43

    second = _settle(client, cid)
    body = second.get_json()
    assert second.status_code == 200
    assert body["ok"] is True
    assert body["status"] == "already_fulfilled"
    assert body["duplicate"] is True
    url2 = body["fulfillment"]["download_url"]
    assert url2 == url1, "re-settle must return the ORIGINAL download URL"
    assert url2.split("?token=")[1] == token1, (
        "no second token may be minted for the same purchase")

    # still exactly one of everything — one token in the intake row
    db_path = rail_env["tmp"] / "revenue.db"
    assert _db_counts(db_path) == (1, 1)
    assert len(mock_comms["sent"]) == 1
    intake = pl.get_pack_intake(db_path, f"intake-rail-{cid}")
    assert (intake.details or {}).get("delivery_token") == token1

    # the original token still downloads
    resp = rail_app.test_client().get(
        f"/download/intake-rail-{cid}?token={token1}")
    assert resp.status_code == 200
    assert resp.data == (
        _repo_root() / "money_assets" / "ai_agent_bundle.md").read_bytes()


def test_duplicate_settle_single_digital_delivery(
        rail_app, rail_env, mock_comms):
    """A redelivered settle is acked; the buyer gets exactly one delivery."""
    client = rail_app.test_client()
    rail_env["pay_cents"](14700)
    cid = _checkout_digital(client, "digital_growth_forecast", 14700)

    first = _settle(client, cid)
    assert first.get_json()["ok"] is True
    second = _settle(client, cid)
    assert second.status_code == 200
    assert second.get_json().get("duplicate") is True

    db_path = rail_env["tmp"] / "revenue.db"
    assert _db_counts(db_path) == (1, 1)
    assert len(mock_comms["sent"]) == 1
    log_lines = (rail_env["tmp"] / "platform_payments.jsonl"
                 ).read_text().splitlines()
    assert len(log_lines) == 1, "payment finalized exactly once"


# ---------------------------------------------------------------------------
# break-it probes: tokens are per-intake and unguessable
# ---------------------------------------------------------------------------

def test_wrong_cross_and_unknown_tokens_rejected(
        rail_app, rail_env, mock_comms):
    """Tokens are per-intake: wrong token, cross-intake token, and unknown
    intake ids all fail — the link is not guessable."""
    client = rail_app.test_client()
    links = {}
    # Two separate on-chain payments (distinct tx hashes) — x402 rejects
    # double-fulfillment of one tx across challenges.
    for (pack_sku, cents), txh in (
            (("digital_bi_template_pack", 9700), TX_HASH),
            (("digital_growth_forecast", 14700), TX_HASH_2)):
        rail_env["pay_cents"](cents)
        cid = _checkout_digital(client, pack_sku, cents)
        res = _settle(client, cid, tx_hash=txh)
        assert res.get_json()["ok"] is True
        email = mock_comms["sent"][-1]
        links[pack_sku] = _extract_download_link(email["html"])

    db = rail_env["tmp"] / "revenue.db"
    bi_intake, bi_token = links["digital_bi_template_pack"]
    fc_intake, fc_token = links["digital_growth_forecast"]
    assert bi_token != fc_token, "tokens must be unique per intake"

    # wrong token for the right intake
    with pytest.raises(ValueError):
        ro.verify_digital_delivery(bi_intake, "nope-not-the-token", db_path=db)
    # right token for the WRONG intake
    with pytest.raises(ValueError):
        ro.verify_digital_delivery(bi_intake, fc_token, db_path=db)
    # unknown intake id
    with pytest.raises(ValueError):
        ro.verify_digital_delivery("intake-rail-nope", bi_token, db_path=db)
    # the genuine pairs still resolve
    assert ro.verify_digital_delivery(
        bi_intake, bi_token, db_path=db).endswith("instant_bi_template_pack.md")
    assert ro.verify_digital_delivery(
        fc_intake, fc_token, db_path=db).endswith("growth_forecast_template.md")

    # route level: a cross-intake token 404s
    assert client.get(
        f"/download/{bi_intake}?token={fc_token}").status_code == 404


def test_poisoned_asset_in_intake_raises(tmp_path):
    """A poisoned intake row (correct delivery token, hostile `asset` such
    as ../../../../etc/passwd) must be rejected by verify_digital_delivery —
    the asset allowlist is the defense-in-depth line for asset resolution."""
    db = tmp_path / "poison.db"
    pl.record_pack_intake(
        db,
        intake_id="intake-poison",
        customer_email="buyer@example.com",
        pack_sku="digital_bi_template_pack",
        event_id="evt-poison",
        intake_url="https://getsincor.com/download/intake-poison?token=abc",
        details={
            "asset": "../../../../etc/passwd",
            "delivery_token": "real-token-here",
            "digital": True,
        },
    )
    with pytest.raises(ValueError):
        ro.verify_digital_delivery(
            "intake-poison", "real-token-here", db_path=db)


def test_tampered_price_settle_fails_closed(rail_app, rail_env, mock_comms):
    """The stubbed receipt pays $97 but the challenge is for the $297
    bundle: settle fails closed (insufficient_amount) — nothing is
    provisioned, no token is issued, no download can be forged."""
    client = rail_app.test_client()
    rail_env["pay_cents"](9700)  # $97 receipt...
    cid = _checkout_digital(client, "digital_ai_agent_bundle", 29700)

    res = _settle(client, cid)
    body = res.get_json()
    assert res.status_code == 402
    assert body["ok"] is False
    assert body["error"] == "insufficient_amount"

    db_path = rail_env["tmp"] / "revenue.db"
    # verify fails BEFORE the event claim: no rail event row exists for an
    # unverified payment, and nothing is provisioned
    assert _db_counts(db_path) == (0, 0), "no intake for an unpaid pack"
    assert len(mock_comms["sent"]) == 0, "no delivery email for tampered price"

    # the intake id is guessable — but with no intake row there is nothing
    # to resolve, so no download link can be forged
    with pytest.raises(ValueError):
        ro.verify_digital_delivery(
            f"intake-rail-{cid}", "anything", db_path=db_path)
    assert client.get(
        f"/download/intake-rail-{cid}?token=anything").status_code == 404


# ---------------------------------------------------------------------------
# mounting: the download route exists on the real app factory
# ---------------------------------------------------------------------------

def test_download_route_mounted_on_create_app(rail_env):
    from sincor2.app import create_app

    app = create_app()
    rules = {str(r) for r in app.url_map.iter_rules()}
    assert "/download/<intake_id>" in rules
