"""Zero-to-revenue 4.2: WebBuilder site-builder SKU on the x402/USDC rail.

Port of the parked Stripe tests (test_webbuilder_sku.py on
xioix/z2r2-w4-webbuilder-sku) to the rail flow:

  - rr.PACK_SKUS["webbuilder_site"] price 49700 ($497, TOA decision
    z2r-webbuilder-price-20261023) with the webbuilder_studio module
    binding; the rail-pack-webbuilder_site x402 resource agrees (497.0
    USDC)
  - full loop (WIRED gate): /rail/checkout -> challenge (497.0 USDC;
    client-supplied price ignored) -> /rail/settle (stubbed _rpc_call in
    sincor2.platform_payments) -> payment finalized -> money gate ->
    fulfillment builds exactly one WebBuilder bundle (static HTML with
    business name / tel: phone CTA / 'Book a consultation' CTA) + one
    webbuilder_crm record + one intake + one buyer email; no
    vertical-agent Celery job for this SKU
  - idempotent re-settle: already_fulfilled ack, nothing rebuilt,
    nothing re-sent (exactly one project / contact / intake / email)
  - bundle-build failure: settle returns ok False, event marked failed,
    pack_fulfillment_failed alert recorded; intake + email happened
    before the failure, no project built

The "chain" is a canned _rpc_call stub (the fork-sim analog): zero
network, zero keys, nothing broadcast. Treasury / token address /
decimals come from the repo's own modules, never invented here.
"""
from __future__ import annotations

import sqlite3

import pytest

import sincor2.rail_routes as rr
import sincor2.platform_payments as pp
import sincor2.x402_payments as x402
from sincor2 import payment_ledger as pl
from sincor2.governance.money_gate import reset_money_boundary
from sincor2.revenue_orchestrator import RevenueOrchestrator

# Canned chain values. The tx hash is a well-formed 0x + 64 hex string; the
# payer is a well-formed 0x address. Treasury / token address / decimals
# are read from the repo's own modules, never invented here.
TX_HASH = "0x" + "ab" * 32

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

AMOUNT_ATOMIC_497 = 497_000_000  # $497.00 in 6-decimal USDC


@pytest.fixture(autouse=True)
def _fresh_boundary():
    reset_money_boundary()
    yield
    reset_money_boundary()


@pytest.fixture()
def rail_env(tmp_path, monkeypatch):
    """Hermetic stores + stubbed RPC paying exactly $497 USDC."""
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WEBBUILDER_DATA_DIR", str(tmp_path / "webbuilder"))
    monkeypatch.setattr(
        "sincor2.treasury_inflow._LEDGER_PATH", tmp_path / "inflow.jsonl")
    monkeypatch.setattr("sincor2.agent_billing._log_path",
                        lambda: tmp_path / "platform_payments.jsonl")

    treasury = x402.get_resource("rail-pack-webbuilder_site")["treasury"]
    assert treasury, "rail-pack-webbuilder_site resource must be configured"
    token_addr = pp.token_address("USDC")

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
                "data": hex(AMOUNT_ATOMIC_497),
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
    return {"sent": sent, "queued": queued}


@pytest.fixture(scope="module")
def rail_app():
    """Minimal Flask app with the real rail blueprint."""
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


def _checkout_challenge(client, **extra):
    body = {"pack_sku": "webbuilder_site", "payer_wallet": PAYER,
            "buyer_email": "buyer@example.com", "buyer_name": "Test Buyer"}
    body.update(extra)
    res = client.post("/rail/checkout", json=body)
    assert res.status_code == 402, res.get_json()
    payload = res.get_json()
    _bind_challenge(payload["challenge_id"], payload["resource_id"])
    return payload["challenge_id"]


def _settle(client, challenge_id, **extra):
    body = {"challenge_id": challenge_id, "tx_hash": TX_HASH,
            "payer_wallet": PAYER, "buyer_email": "buyer@example.com",
            "buyer_name": "Test Buyer",
            "business_name": "Acme Dental",
            "phone": "(555) 123-4567",
            "niche": "dental",
            "territory": "Clinton, IA"}
    body.update(extra)
    return client.post("/rail/settle", json=body)


def _wb_project():
    from sincor2 import webbuilder_studio as wb
    projects = wb.list_projects()
    assert len(projects) == 1, f"expected exactly one project: {projects}"
    return projects[0]


# ---------------------------------------------------------------------------
# 4.2 — SKU registration
# ---------------------------------------------------------------------------

def test_webbuilder_sku_price_49700_server_side(rail_env):
    """The $497 price was decided by TOA; PACK_SKUS is the source of truth,
    and the x402 resource agrees with it (checkout fails closed on drift)."""
    sku = rr.PACK_SKUS["webbuilder_site"]
    assert sku["price_cents"] == 49700
    assert sku["name"] == "WebBuilder Site"
    assert sku["module"] == "webbuilder_studio.py"

    resource = x402.get_resource("rail-pack-webbuilder_site")
    assert resource is not None
    assert resource["token"] == "USDC"
    assert abs(resource["amount_usdc"] - 497.0) < 0.001
    assert resource["amount_display"] == 497.0


def test_build_webbuilder_bundle_ported(rail_env):
    """The bundle builder from the parked branch is importable on the
    rail path and reuses the webbuilder modules (no reimplementation)."""
    assert callable(RevenueOrchestrator._build_webbuilder_bundle)


# ---------------------------------------------------------------------------
# 4.2 — checkout: client price ignored, $497 challenge minted
# ---------------------------------------------------------------------------

def test_checkout_webbuilder_ignores_client_price(rail_app, rail_env):
    client = rail_app.test_client()
    # A client-supplied price must be ignored: the server derives it.
    res = client.post("/rail/checkout", json={
        "pack_sku": "webbuilder_site",
        "price_cents": 100,  # tampered price — must be ignored
        "amount_usdc": 1.0,
        "payer_wallet": PAYER,
    })
    assert res.status_code == 402
    body = res.get_json()
    assert body["pack_sku"] == "webbuilder_site"
    assert body["pack_name"] == "WebBuilder Site"
    assert body["amount_usdc"] == 497.0
    assert body["resource_id"] == "rail-pack-webbuilder_site"
    assert body["challenge_id"]


# ---------------------------------------------------------------------------
# 4.2 — full loop: checkout -> settle -> delivered (WIRED gate)
# ---------------------------------------------------------------------------

def test_full_loop_webbuilder_checkout_settle_fulfillment_delivered(
        rail_app, rail_env, mock_comms):
    """WIRED gate: the whole money path end to end on the stubbed chain."""
    client = rail_app.test_client()
    cid = _checkout_challenge(client)

    res = _settle(client, cid)
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["ok"] is True
    assert body["status"] == "fulfilled"
    assert body["pack_sku"] == "webbuilder_site"

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
    assert intake.pack_sku == "webbuilder_site"
    assert intake.event_id == f"rail-{cid}"

    # buyer emailed from the thank-you template, carrying the intake link
    assert len(mock_comms["sent"]) == 1
    email = mock_comms["sent"][0]
    assert email["to"] == "buyer@example.com"
    assert "WebBuilder Site" in email["html"]
    assert fulfillment["intake_url"] in email["html"]

    # no vertical-agent Celery job for the webbuilder SKU — the deliverable
    # is the site bundle, built synchronously at purchase time
    assert mock_comms["queued"] == []

    # the 4.1 bundle: one project, static HTML with name/phone/CTA
    from sincor2 import webbuilder_studio as wb
    from sincor2 import webbuilder_crm as crm
    project = _wb_project()
    assert project["name"] == "Acme Dental"
    assert fulfillment["job_id"] == project["id"]
    assert fulfillment["webbuilder"]["project_id"] == project["id"]
    assert fulfillment["webbuilder"]["contact_id"]

    html = wb.get_site_html(project["slug"], "draft")
    assert html, "draft lane HTML must exist"
    assert "Acme Dental" in html                      # business name
    assert "tel:" in html and "555" in html            # phone CTA
    assert "Book a consultation" in html               # contact CTA

    # the webbuilder_crm record
    contacts = crm.list_contacts(wb.data_dir(), project["id"])
    assert len(contacts) == 1
    contact = contacts[0]
    assert contact["email"] == "buyer@example.com"
    assert contact["project_id"] == project["id"]
    assert contact["source"] == "pack_purchase"

    # the platform payment was finalized exactly once (JSONL append)
    log_lines = (rail_env["tmp"] / "platform_payments.jsonl").read_text().splitlines()
    assert len(log_lines) == 1

    # treasury inflow recorded, carrying the tx hash
    inflow_lines = (rail_env["tmp"] / "inflow.jsonl").read_text().splitlines()
    assert len(inflow_lines) == 1

    # no alerts on the happy path
    assert pl.list_alerts(db_path) == []


# ---------------------------------------------------------------------------
# 4.2 — idempotent re-settle: nothing rebuilt, nothing re-sent
# ---------------------------------------------------------------------------

def test_webbuilder_resettle_is_idempotent_no_rebuild(
        rail_app, rail_env, mock_comms):
    client = rail_app.test_client()
    cid = _checkout_challenge(client)
    first = _settle(client, cid)
    assert first.get_json()["ok"] is True

    second = _settle(client, cid)
    body = second.get_json()
    assert second.status_code == 200
    assert body["ok"] is True
    assert body["status"] == "already_fulfilled"
    assert body["duplicate"] is True
    assert body["fulfillment"]["status"] == "delivered"
    assert body["fulfillment"]["intake_id"] == f"intake-rail-{cid}"

    # still exactly one of everything — no second project, contact,
    # intake, email, or payment
    db_path = rail_env["tmp"] / "revenue.db"
    assert _db_counts(db_path) == (1, 1)
    assert len(mock_comms["sent"]) == 1
    assert len(mock_comms["queued"]) == 0
    _wb_project()  # exactly one project
    from sincor2 import webbuilder_studio as wb
    from sincor2 import webbuilder_crm as crm
    contacts = crm.list_contacts(wb.data_dir(), _wb_project()["id"])
    assert len(contacts) == 1  # exactly one CRM record
    log_lines = (rail_env["tmp"] / "platform_payments.jsonl").read_text().splitlines()
    assert len(log_lines) == 1


# ---------------------------------------------------------------------------
# 4.2 — bundle-build failure: failed + alert, retryable
# ---------------------------------------------------------------------------

def test_webbuilder_fulfillment_failure_marks_failed_and_alerts(
        rail_app, rail_env, mock_comms, monkeypatch):
    from sincor2 import webbuilder_studio as wb

    orig_create_project = wb.create_project

    def _boom(*a, **k):
        raise RuntimeError("studio unavailable")

    monkeypatch.setattr(wb, "create_project", _boom)

    client = rail_app.test_client()
    cid = _checkout_challenge(client)
    res = _settle(client, cid)
    body = res.get_json()
    assert res.status_code == 402
    assert body["ok"] is False
    assert "studio unavailable" in body["fulfillment"]["error"]

    db_path = rail_env["tmp"] / "revenue.db"
    event = rr._get_rail_event(f"rail-{cid}")
    assert event["fulfillment_status"] == "failed"

    alerts = pl.list_alerts(db_path)
    assert len(alerts) == 1
    assert alerts[0].kind == "pack_fulfillment_failed"
    assert "studio unavailable" in alerts[0].message

    # intake + email happened before the bundle failure; no project built
    assert _db_counts(db_path) == (1, 1)
    assert len(mock_comms["sent"]) == 1
    assert wb.list_projects() == []

    # the failure is retryable, not a duplicate: fixing the studio and
    # re-settling completes without a second payment record. (Restore only
    # the studio patch — monkeypatch.undo() would also drop the RPC stub,
    # the env isolation, and the comms mocks.)
    monkeypatch.setattr(wb, "create_project", orig_create_project)
    retry = _settle(client, cid)
    retry_body = retry.get_json()
    assert retry_body["ok"] is True
    assert retry_body["status"] == "fulfilled"
    assert retry_body["fulfillment"]["status"] == "delivered"
    assert _db_counts(db_path) == (1, 1)
    log_lines = (rail_env["tmp"] / "platform_payments.jsonl").read_text().splitlines()
    assert len(log_lines) == 1  # still exactly one payment record


# ---------------------------------------------------------------------------
# Adversarial-review regressions (w4a builder fixes)
# ---------------------------------------------------------------------------

def test_jsonld_script_breakout_neutralized(rail_env):
    """Break 1 — stored XSS: json.dumps does not escape </script.

    A hostile business_name (arriving unauthenticated via the settle body)
    must be unable to break out of the JSON-LD <script> block — the payload
    may appear only in escaped (<\\/ ) form, and the block must still parse
    with the full business name intact.
    """
    import json as _json
    import re as _re

    from sincor2 import webbuilder_render

    hostile = "Acme </script><script>alert(document.domain)</script>"
    project = {"id": "pid-xss-1", "name": hostile, "niche": "dental",
               "territory": "Clinton, IA", "prompt": "Website for Acme"}
    html = webbuilder_render.build_site_html(
        project, {"name": hostile, "phone": "(555) 123-4567"}, "draft")
    # the JSON-LD block itself may never contain a literal closer — that is
    # what would break out of the <script> element
    m = _re.search(r'<script type="application/ld\+json">(.*?)</script>',
                   html, _re.S)
    assert m, "JSON-LD block must be present"
    assert "</script" not in m.group(1)
    # the attacker's breakout pair never appears as raw HTML anywhere
    assert "</script><script>" not in html
    # the payload survives only in JSON-escaped form
    assert "<\\/script>" in html
    payload = _json.loads(m.group(1))
    assert payload["name"] == hostile


def test_retry_after_partial_build_resumes_single_project(
        rail_app, rail_env, mock_comms, monkeypatch):
    """Break 2 — idempotency across settle retries.

    The first settle fails AFTER create_project succeeded but before
    completion (webbuilder_crm.record_contact raises). The retry must
    resume from the existing project instead of building a second bundle:
    exactly 1 project, 1 site dir, 1 CRM contact, 1 thank-you email, and
    exactly 1 payment record.
    """
    from sincor2 import webbuilder_crm as crm
    from sincor2 import webbuilder_studio as wb

    orig_record_contact = crm.record_contact

    def _boom(*a, **k):
        raise RuntimeError("crm unavailable")

    monkeypatch.setattr(crm, "record_contact", _boom)

    client = rail_app.test_client()
    cid = _checkout_challenge(client)
    first = _settle(client, cid)
    first_body = first.get_json()
    assert first.status_code == 402
    assert first_body["ok"] is False
    assert "crm unavailable" in first_body["fulfillment"]["error"]

    # the project was created before the CRM failure, but the event failed
    projects = wb.list_projects()
    assert len(projects) == 1
    partial_project = projects[0]
    event = rr._get_rail_event(f"rail-{cid}")
    assert event["fulfillment_status"] == "failed"

    # restore CRM and retry: must resume, not rebuild (restore only the CRM
    # patch — monkeypatch.undo() would drop the RPC stub, env isolation,
    # and comms mocks)
    monkeypatch.setattr(crm, "record_contact", orig_record_contact)
    retry = _settle(client, cid)
    retry_body = retry.get_json()
    assert retry.status_code == 200
    assert retry_body["ok"] is True
    assert retry_body["status"] == "fulfilled"
    assert retry_body["fulfillment"]["status"] == "delivered"

    # exactly one of everything — and the SAME project, resumed
    project = _wb_project()
    assert project["id"] == partial_project["id"]
    site_dirs = list((wb.data_dir() / "sites").iterdir())
    assert len(site_dirs) == 1  # exactly one site dir
    assert (site_dirs[0] / "draft" / "index.html").is_file()
    contacts = crm.list_contacts(wb.data_dir(), project["id"])
    assert len(contacts) == 1  # exactly one CRM record
    assert retry_body["fulfillment"]["webbuilder"]["contact_id"] == contacts[0]["id"]
    assert len(mock_comms["sent"]) == 1  # thank-you email not re-sent

    db_path = rail_env["tmp"] / "revenue.db"
    assert _db_counts(db_path) == (1, 1)
    log_lines = (rail_env["tmp"] / "platform_payments.jsonl").read_text().splitlines()
    assert len(log_lines) == 1  # payment recorded exactly once
