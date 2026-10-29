"""Directive #2 Branch B — buyer flow fixes.

Items under test (all reproduced/confirmed 2026-10-26):

  ITEM 2 (P0): a SUCCESSFUL challenge bind via POST
  /api/x402/challenge-bind used to return HTTP 402
  {"ok": false, "error": "payment_required", "ownership_proven": true},
  while templates/rail_pay.html's postJSON throws on any non-2xx — so
  "Pay" never enabled for browser buyers. The bind success contract is
  now a 2xx {"ok": true, "ownership_proven": true, ...} response; a
  second bind of the same challenge still returns 409
  challenge_already_bound, wrong-signature binds still 403, and an
  unbound challenge is still refused at settle.

  ITEM 6: production GET /buy rendered buy_tokens.html (0 /rail/pay
  links); it now renders buy.html (10 /rail/pay links).

  ITEM 7a: wallet-less discovery probes (GET /x402/<id>,
  GET|POST /api/paid/<id>) used to return 400 payer_wallet_required;
  they now return the standard 402 offer envelope WITHOUT minting a
  challenge (no challenge_id in the body, no row written).

  ITEM 7b: a paid serve carrying payer_wallet in the JSON body used to
  400 unknown_payload_fields on the G5-G8 resources; payer_wallet is a
  transport field and is stripped before domain allowlist validation
  in web_search_read / market_data_read / toa_micro_decision /
  site_check. Other unknown fields still 400.

  ITEM 8: rail_pay.html collects the optional buyer metadata the
  server already accepts (buyer_email, business_name, niche, phone),
  sends it in the settle POST body, and the completion screen renders
  fulfillment links (download_url / intake_url) from the settle
  response's fulfillment object.

The rail full loop drives the REAL production app object
(sincor2.mvp_app.app) mirroring test_rail_frontrun_bind's harness
(canned _rpc_call chain stub, Hardhat dev accounts #0/#1 — test-only,
never funded). The x402 G-resource flows drive the billing blueprint
exactly like the sibling G5-G8 suites.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

import sincor2.market_data_read as mdr
import sincor2.platform_payments as pp
import sincor2.rail_routes as rr
import sincor2.site_check as sc
import sincor2.toa_micro_decision as toa_mod
import sincor2.web_search_read as ws
import sincor2.x402_payments as x402
from sincor2.defi.price_oracle import ChainlinkRound
from sincor2.governance.money_gate import reset_money_boundary

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RAIL_PAY_HTML = REPO_ROOT / "templates" / "rail_pay.html"

VICTIM_ACCT = Account.from_key(
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
ATTACKER_ACCT = Account.from_key(
    "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d")
VICTIM = VICTIM_ACCT.address
ATTACKER = ATTACKER_ACCT.address

TX_PACK = "0x" + "ab" * 32
_PACK_RESOURCE = "rail-pack-digital_bi_template_pack"
_PAYMENTS = {TX_PACK: (97_000_000, _PACK_RESOURCE)}

TOA_WINNER = {
    "decision_id": "d2-buyer-flow-toa",
    "options": [
        {"id": "a", "label": "Option A", "signals": [1.0, 2.0, 3.0, 4.0]},
        {"id": "b", "label": "Option B", "signals": [4.0, 3.0, 2.0, 1.0]},
    ],
}

SITE_GOOD_URL = "https://acmedental.example.com/"
SITE_GOOD_HTML = """<!doctype html><html><head>
<title>Acme Dental</title>
<meta name="description" content="Family dental care.">
<meta name="viewport" content="width=device-width, initial-scale=1">
</head><body><h1>Acme Dental</h1>
<a href="tel:+15551234567">Call us</a>
</body></html>"""


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

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
    monkeypatch.setattr(
        "sincor2.treasury_inflow._LEDGER_PATH", tmp_path / "inflow.jsonl")
    monkeypatch.setattr("sincor2.agent_billing._log_path",
                        lambda: tmp_path / "platform_payments.jsonl")
    token_addr = pp.token_address("USDC")

    def _fake_rpc(method, params, attempts=3):
        if method == "eth_getTransactionReceipt":
            tx = params[0]
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
            return hex(112)
        raise AssertionError(f"unexpected RPC method {method}")

    monkeypatch.setattr(pp, "_rpc_call", _fake_rpc)
    x402.init_x402_db()
    return {"tmp": tmp_path}


@pytest.fixture()
def mock_comms(monkeypatch):
    sent = []

    def fake_send_email(self, to_email, to_name="", subject="",
                        html_content="", text_content=None, metadata=None):
        sent.append({"to": to_email, "html": html_content,
                     "metadata": metadata or {}})
        return {"status": "sent", "message_id": "msg-test-d2b"}

    def boom_apply_async(*a, **k):
        raise AssertionError(
            "digital fulfillment must not queue a vertical-agent job")

    monkeypatch.setattr(
        "sincor2.email_sender.EmailSender.send_email", fake_send_email)
    monkeypatch.setattr(rr.fulfill_pack_agent, "apply_async",
                        boom_apply_async)
    return {"sent": sent}


@pytest.fixture(scope="module")
def mvp_app_obj(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mvp-d2-buyer-flow")
    os.environ["ORDERS_DB_PATH"] = str(tmp / "orders.db")
    os.environ["SINCOR_REVENUE_DB"] = str(tmp / "revenue.db")
    os.environ["SINCOR_DATA_DIR"] = str(tmp / "data")
    from sincor2.mvp_app import app as mvp_app
    mvp_app.config["TESTING"] = True
    mvp_app.config["SERVER_NAME"] = None
    return mvp_app


@pytest.fixture()
def mvp_client(mvp_app_obj, tmp_path, monkeypatch):
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "sincor2.treasury_inflow._LEDGER_PATH", tmp_path / "inflow.jsonl")
    monkeypatch.setattr("sincor2.agent_billing._log_path",
                        lambda: tmp_path / "platform_payments.jsonl")
    x402.init_x402_db()
    return mvp_app_obj.test_client()


# ---------------------------------------------------------------------------
# Billing-blueprint fixtures (sibling G5-G8 pattern)
# ---------------------------------------------------------------------------

@pytest.fixture()
def x402_db(tmp_path, monkeypatch):
    db = tmp_path / "orders.db"
    monkeypatch.setenv("ORDERS_DB_PATH", str(db))
    x402.init_x402_db()
    return db


@pytest.fixture()
def mock_onchain(monkeypatch):
    def _verify(tx_hash, **kwargs):
        expected = int(kwargs["expected_atomic"])
        supplied = kwargs.get("payer_wallet") or ""
        return {"ok": True, "payer_wallet": supplied or VICTIM}

    monkeypatch.setattr(x402, "verify_treasury_transfer", _verify)
    monkeypatch.setattr(
        x402, "record_platform_payment",
        lambda **kw: {"payment_id": kw.get("payment_id"), "ok": True})
    monkeypatch.setattr(
        x402, "record_inflow", lambda *a, **k: {"ok": True})


@pytest.fixture()
def billing_client(x402_db):
    import sincor2.mvp_app  # noqa: F401  (binding side effects first)
    from sincor2.mvp_blueprints import billing
    app = Flask(__name__)
    app.register_blueprint(billing.bp)
    app.config.update(TESTING=True)
    return app.test_client()


def _sign(acct, challenge_id, resource_id, wallet=None):
    msg = x402.challenge_bind_message(
        challenge_id, resource_id, wallet or acct.address)
    return acct.sign_message(encode_defunct(text=msg)).signature.hex()


def _bind_http(client, challenge_id, resource_id, acct=VICTIM_ACCT):
    return client.post("/api/x402/challenge-bind", json={
        "challenge_id": challenge_id,
        "payer_wallet": acct.address,
        "signature": _sign(acct, challenge_id, resource_id),
    })


def _pay_for_resource(client, resource_id, tx_seed):
    """402 challenge -> bind (NEW success shape) -> verify -> token."""
    r = client.get(f"/x402/{resource_id}?wallet={VICTIM}")
    assert r.status_code == 402, r.get_json()
    ch = r.get_json()
    b = _bind_http(client, ch["challenge_id"], resource_id)
    assert b.status_code == 200, b.get_json()
    assert b.get_json()["ok"] is True
    assert b.get_json()["ownership_proven"] is True
    r = client.post("/api/x402/verify", json={
        "challenge_id": ch["challenge_id"],
        "tx_hash": "0x" + tx_seed * 32,
        "payer_wallet": VICTIM,
    })
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["ok"] is True and body["access_token"]
    return body


def _challenge_count():
    with x402._conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM x402_challenges").fetchone()[0]


# ---------------------------------------------------------------------------
# ITEM 2 — full loop through the production app + bind contract
# ---------------------------------------------------------------------------

def test_full_loop_mvp_checkout_bind_pay_settle_download(
        mvp_client, rail_env, mock_comms):
    client = mvp_client

    # 1. checkout (digital pack, with wallet + buyer metadata fields
    #    the page now collects).
    res = client.post("/rail/checkout", json={
        "pack_sku": "digital_bi_template_pack", "payer_wallet": VICTIM,
        "buyer_email": "buyer@example.com"})
    assert res.status_code == 402, res.get_json()
    body = res.get_json()
    cid = body["challenge_id"]
    rid = body["resource_id"]

    # 2. bind over HTTP — the step that used to 402 and break the page.
    b = _bind_http(client, cid, rid)
    assert b.status_code == 200, b.get_json()
    bound = b.get_json()
    # Exact success shape pinned:
    assert bound["ok"] is True
    assert bound["ownership_proven"] is True
    assert bound["challenge_id"] == cid
    assert bound["resource_id"] == rid
    assert bound["payer_wallet"] == VICTIM.lower()
    assert bound.get("error") is None
    assert bound["payment"]["payTo"]
    assert bound["payment"]["asset"]

    # 2b. second bind of the same challenge -> still 409.
    b2 = _bind_http(client, cid, rid)
    assert b2.status_code == 409, b2.get_json()
    assert b2.get_json()["error"] == "challenge_already_bound"

    # 3+4. pay (canned chain) + settle, carrying the ITEM 8 capture
    # fields exactly as the page now sends them.
    s = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_PACK, "payer_wallet": VICTIM,
        "buyer_email": "buyer@example.com",
        "business_name": "Acme Dental", "niche": "dental",
        "phone": "+1-555-123-4567"})
    assert s.status_code == 200, s.get_json()
    out = s.get_json()
    assert out["ok"] is True
    assert out["status"] == "fulfilled"
    fulfillment = out["fulfillment"]
    assert fulfillment["status"] == "delivered"

    # 5. download 200 through the token-gated URL from the settle
    # response (the fields the completion screen links to).
    download_url = fulfillment["download_url"]
    assert fulfillment["intake_url"]
    parsed = urlparse(download_url)
    assert parsed.path.rsplit("/", 1)[-1] == fulfillment["intake_id"]
    assert "token=" in parsed.query
    resp = client.get(f"{parsed.path}?{parsed.query}")
    assert resp.status_code == 200
    assert resp.data
    assert len(mock_comms["sent"]) == 1
    assert mock_comms["sent"][0]["to"] == "buyer@example.com"


def test_bind_wrong_signature_still_refused_and_unbound_settle_refused(
        mvp_client, rail_env, mock_comms):
    client = mvp_client
    res = client.post("/rail/checkout", json={
        "pack_sku": "digital_bi_template_pack", "payer_wallet": VICTIM,
        "buyer_email": "buyer@example.com"})
    body = res.get_json()
    cid, rid = body["challenge_id"], body["resource_id"]

    # Attacker signs the victim's bind message with the WRONG key.
    forged = client.post("/api/x402/challenge-bind", json={
        "challenge_id": cid, "payer_wallet": VICTIM,
        "signature": _sign(ATTACKER_ACCT, cid, rid, wallet=VICTIM)})
    assert forged.status_code == 403, forged.get_json()
    assert forged.get_json()["error"] == "ownership_proof_failed"

    # And the still-unbound challenge cannot settle.
    s = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_PACK, "payer_wallet": VICTIM,
        "buyer_email": "buyer@example.com"})
    assert s.get_json()["error"] == "challenge_unbound"


# ---------------------------------------------------------------------------
# ITEM 6 — production /buy renders buy.html
# ---------------------------------------------------------------------------

def test_buy_page_mvp_renders_buy_html_with_rail_pay_links(mvp_client):
    resp = mvp_client.get("/buy")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert html.count("/rail/pay/") >= 10
    assert "/rail/pay/rail-pack-webbuilder_site" in html
    assert "/rail/pay/rail-sub-starter" in html


# ---------------------------------------------------------------------------
# ITEM 7a — wallet-less discovery probes get the 402 offer envelope
# ---------------------------------------------------------------------------

def _assert_offer_shape(body, resource_id):
    assert body["ok"] is False
    assert body["error"] == "payment_required"
    assert body["resource"] == resource_id
    assert body["resource_id"] == resource_id
    assert "challenge_id" not in body  # no payable challenge minted
    payment = body["payment"]
    assert payment["payTo"]
    assert payment["asset"]
    assert body["payTo"] == payment["payTo"]
    assert body["asset"] == payment["asset"]
    assert body["price"] == payment["extra"]["amount_display"]
    assert body["accepts"] == [payment]


def test_walletless_challenge_get_returns_402_offer_no_mint(
        billing_client, x402_db):
    before = _challenge_count()
    r = billing_client.get("/x402/web-search-read")
    assert r.status_code == 402, r.get_json()
    _assert_offer_shape(r.get_json(), "web-search-read")
    assert _challenge_count() == before


def test_walletless_paid_post_returns_402_offer_no_mint(
        billing_client, x402_db):
    before = _challenge_count()
    r = billing_client.post("/api/paid/web-search-read",
                            json={"mode": "search", "query": "x402"})
    assert r.status_code == 402, r.get_json()
    _assert_offer_shape(r.get_json(), "web-search-read")
    assert _challenge_count() == before


def test_walletless_paid_get_returns_402_offer(billing_client, x402_db):
    r = billing_client.get("/api/paid/toa-micro-decision")
    assert r.status_code == 402, r.get_json()
    _assert_offer_shape(r.get_json(), "toa-micro-decision")


# ---------------------------------------------------------------------------
# ITEM 7b — payer_wallet stripped for G5-G8; unknown fields still 400
# ---------------------------------------------------------------------------

def test_strip_web_search_read_direct():
    status, body = ws.serve_web_search_read(
        {"mode": "search", "query": "what is x402",
         "payer_wallet": VICTIM},
        search=lambda q: [{"title": "T", "url": "https://base.org/x",
                           "text": "snippet"}],
        read=lambda u: {})
    assert status == 200, body
    assert body["count"] == 1


def test_strip_market_data_read_direct():
    import time
    now = int(time.time())
    rnd = ChainlinkRound(round_id=1, answer=100_000_000, decimals=8,
                         started_at=now - 10, updated_at=now - 10,
                         answered_in_round=1)
    status, body = mdr.serve_market_data_read(
        {"pair": "ETH/USD", "mode": "price", "payer_wallet": VICTIM},
        read_round=lambda pair: rnd, now=now)
    assert status == 200, body


def test_strip_toa_micro_decision_direct():
    payload = dict(TOA_WINNER)
    payload["payer_wallet"] = VICTIM
    status, body = toa_mod.serve_toa_micro_decision(payload)
    assert status == 200, body
    assert body["ok"] is True


def test_strip_site_check_direct():
    # Business-name-only payload: exercises validate+serve with the
    # transport field present, without depending on live DNS for the
    # SSRF target check (the sandbox resolver's fake-IP answers make
    # any real hostname "forbidden" non-deterministically — the same
    # flake hits test_site_check.py's own URL payloads).
    status, body = sc.serve_site_check(
        {"business_name": "Acme Dental", "payer_wallet": VICTIM},
        fetch=lambda url: None)
    assert status == 200, body
    assert body["verdict"] == "build_recommended"
    assert body["deficiencies"][0]["code"] == "no_website"


@pytest.mark.parametrize("serve,payload", [
    ("ws", {"mode": "search", "query": "q", "payer_wallet": VICTIM,
            "bogus_field": 1}),
    ("mdr", {"pair": "ETH/USD", "payer_wallet": VICTIM,
             "bogus_field": 1}),
    ("toa", {**TOA_WINNER, "payer_wallet": VICTIM, "bogus_field": 1}),
    ("sc", {"url": SITE_GOOD_URL, "payer_wallet": VICTIM,
            "bogus_field": 1}),
])
def test_unknown_field_still_400_after_strip(serve, payload):
    if serve == "ws":
        status, body = ws.serve_web_search_read(
            payload, search=lambda q: [], read=lambda u: {})
    elif serve == "mdr":
        status, body = mdr.serve_market_data_read(
            payload, read_round=lambda pair: None)
    elif serve == "toa":
        status, body = toa_mod.serve_toa_micro_decision(payload)
    else:
        status, body = sc.serve_site_check(payload, fetch=lambda u: None)
    assert status == 400, body
    assert body["error"] == "unknown_payload_fields"


def test_paid_serve_with_payer_wallet_http_toa(
        billing_client, x402_db, mock_onchain):
    paid = _pay_for_resource(billing_client, "toa-micro-decision", "d2")
    payload = dict(TOA_WINNER)
    payload["payer_wallet"] = VICTIM
    r = billing_client.post(
        "/api/paid/toa-micro-decision",
        headers={"X-Payment-Token": paid["access_token"]}, json=payload)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["ok"] is True


def test_paid_serve_with_payer_wallet_http_web_search(
        billing_client, x402_db, mock_onchain, monkeypatch):
    monkeypatch.setattr(ws, "search_provider", lambda q: [
        {"title": "T", "url": "https://base.org/x", "text": "s"}])
    paid = _pay_for_resource(billing_client, "web-search-read", "d3")
    r = billing_client.post(
        "/api/paid/web-search-read",
        headers={"X-Payment-Token": paid["access_token"]},
        json={"mode": "search", "query": "what is x402",
              "payer_wallet": VICTIM})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["ok"] is True


def test_paid_serve_unknown_field_still_400_http_toa(
        billing_client, x402_db, mock_onchain):
    paid = _pay_for_resource(billing_client, "toa-micro-decision", "d4")
    payload = dict(TOA_WINNER)
    payload["bogus_field"] = 1
    r = billing_client.post(
        "/api/paid/toa-micro-decision",
        headers={"X-Payment-Token": paid["access_token"]}, json=payload)
    assert r.status_code == 400, r.get_json()
    assert r.get_json()["error"] == "unknown_payload_fields"


# ---------------------------------------------------------------------------
# ITEM 8 — rail_pay.html capture fields + completion fulfillment links
# ---------------------------------------------------------------------------

def test_rail_pay_template_capture_fields_and_completion_links():
    html = RAIL_PAY_HTML.read_text(encoding="utf-8")
    # Optional capture fields (server accepts all four on settle).
    for field in ("buyer_email", "business_name", "niche", "phone"):
        assert f'name="{field}"' in html, field
    # They are sent in the settle POST body.
    for key in ("buyer_email:", "business_name:", "niche:", "phone:"):
        assert key in html, key
    # Completion screen: fulfillment links ADDED from the settle
    # response (download_url / intake_url on settle.fulfillment).
    assert 'id="fulfillmentLinks"' in html
    assert 'id="downloadLink"' in html
    assert 'id="intakeLink"' in html
    assert "fulfillment.download_url" in html
    assert "fulfillment.intake_url" in html


def test_rail_pay_page_serves_capture_fields_mvp(mvp_client):
    resp = mvp_client.get("/rail/pay/rail-pack-digital_bi_template_pack")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'name="buyer_email"' in html
    assert 'id="fulfillmentLinks"' in html
