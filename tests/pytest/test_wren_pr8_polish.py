"""Wren PR 8: correctness polish.

Covered here:
  * The #600 gate fix (founder ruling 2026-11-04): a buyer who
    completes intake BEFORE the pack-agent kickoff job runs must
    still get the kickoff — intake completion alone is not proof
    the kickoff executed. The full loop runs end to end: fulfill
    instantiates the agent, the receipt is ok, the buyer's token /
    wallet / answers survive, and the status never regresses.
  * At-most-once is preserved for a kickoff that genuinely ran:
    a second delivery returns already_ran, including after the
    buyer later completes intake (the completion write's 'agent'
    marker is the proof then).
  * The worker gate's execution claim for buyer-first rows blocks
    a duplicate delivery while held and is handed back on release.
  * The enqueue-side kickoff claim (_try_claim_pack_kickoff) stamps
    its timestamp by merging details — pre-existing keys survive.
  * Unsubscribe: GET renders a confirmation page and does NOT
    suppress; POST (incl. RFC 8058 one-click) suppresses; bad
    tokens 404 on both methods. In production the unsubscribe
    token secret must be the dedicated UNSUBSCRIBE_TOKEN_SECRET.
  * The pack_email_failed alert names the intake id, never the
    buyer's raw email address.
  * The intake POST's advancing transition covers the
    agent_kickoff -> intake_complete path (expected-status guard).
  * _paid_skill_dispatch's default branch strips x402 transport
    keys from JSON bodies and args payloads alike.
"""
from __future__ import annotations

import pytest
from flask import Flask

# Production import order matters: mvp_app must be imported before
# the mvp_blueprints modules (same pattern as
# test_airdrop_register.py) — billing binds mvp_app's namespace at
# import, and mvp_app registers the blueprint on the production app
# during its own import.
import sincor2.mvp_app  # noqa: F401  (imported for binding side effects)
from sincor2.mvp_blueprints import billing

import sincor2.rail_routes as rr
from sincor2 import comms_compliance
from sincor2 import payment_ledger as pl

BUYER_EMAIL = "buyer@example.com"
PAYER = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
INTAKE_TOKEN = "tok-pr8-unguessable-1"
EVENT_ID = "rail-x402-pr8test001"
INTAKE_ID = f"intake-{EVENT_ID}"

ANSWERS = {
    "business_name": "Acme Dental LLC",
    "contact_name": "Amy Acme",
    "contact_email": "amy@acmedental.example",
    "website_or_none": "https://acmedental.example",
    "practice_type": "dental practice",
    "state": "IA",
    "specialties": "general dentistry, implants",
    "current_payer_list": "Delta Dental, Cigna",
}


@pytest.fixture()
def rail_env(tmp_path, monkeypatch):
    """Hermetic rail ledger (the pack intakes + alerts store)."""
    db = tmp_path / "revenue.db"
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(db))
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    return db


def _seed(db, *, status="created", details=None, intake_id=INTAKE_ID):
    """Persist a post-fulfillment intake row the way
    _fulfill_rail_pack leaves it, then move it to ``status``."""
    base_details = {
        "pack_name": rr.PACK_SKUS["credentialing_launch"]["name"],
        "module": rr.PACK_SKUS["credentialing_launch"].get("module", ""),
        "payer_wallet": PAYER.lower(),
        "rail": True,
        "intake_token": INTAKE_TOKEN,
    }
    if details:
        base_details.update(details)
    intake, _is_new = pl.record_pack_intake(
        db,
        intake_id=intake_id,
        customer_email=BUYER_EMAIL,
        pack_sku="credentialing_launch",
        event_id=EVENT_ID,
        intake_url=f"https://getsincor.com/intake/{intake_id}"
                   f"?token={INTAKE_TOKEN}",
        details=base_details,
    )
    if status != "created":
        pl.update_pack_intake_status(db, intake_id, status)
    return intake


def _fulfill(intake_id=INTAKE_ID):
    return rr.fulfill_pack_agent(
        intake_id=intake_id,
        pack_sku="credentialing_launch",
        customer_email=BUYER_EMAIL)


# ---------------------------------------------------------------------------
# #600: buyer finished intake before the kickoff ran (full loop)
# ---------------------------------------------------------------------------

def test_kickoff_executes_when_buyer_finished_intake_first(rail_env):
    db = rail_env
    _seed(db, status="intake_complete",
          details={"intake_answers": ANSWERS})

    receipt = _fulfill()
    assert receipt["status"] == "ok"
    assert receipt["agent"]["agent"] == "healthcare_rcm_agent"

    row = pl.get_pack_intake(db, INTAKE_ID)
    # The kickoff write merged — nothing the buyer/fulfillment
    # stored was wiped, and the status never regressed.
    assert row.status == "intake_complete"
    assert row.details["agent"]["agent"] == "healthcare_rcm_agent"
    assert row.details["intake_token"] == INTAKE_TOKEN
    assert row.details["payer_wallet"] == PAYER.lower()
    assert row.details["intake_answers"] == ANSWERS


def test_genuine_kickoff_still_returns_already_ran(rail_env):
    db = rail_env
    _seed(db)  # created

    first = _fulfill()
    assert first["status"] == "ok"

    # Duplicate delivery of the same job: stands down.
    second = _fulfill()
    assert second["status"] == "already_ran"
    assert second["agent"] is None

    # The buyer completes intake afterwards; a third delivery must
    # STILL stand down — the completion marker proves the run.
    pl.update_pack_intake_status(
        db, INTAKE_ID, "intake_complete",
        details={"intake_answers": ANSWERS})
    third = _fulfill()
    assert third["status"] == "already_ran"

    row = pl.get_pack_intake(db, INTAKE_ID)
    assert row.details["agent"]["agent"] == "healthcare_rcm_agent"


def test_execution_claim_blocks_duplicate_and_releases(rail_env):
    db = rail_env
    _seed(db, status="intake_complete",
          details={"intake_answers": ANSWERS})

    gate, prior = rr._begin_pack_kickoff(INTAKE_ID)
    assert (gate, prior) == ("begun", "intake_complete")

    # While the claim is held, a duplicate delivery stands down.
    gate2, _prior2 = rr._begin_pack_kickoff(INTAKE_ID)
    assert gate2 == "already"

    # Releasing hands the execution back to a retry delivery.
    assert rr._release_pack_kickoff_run(INTAKE_ID, prior) is True
    gate3, prior3 = rr._begin_pack_kickoff(INTAKE_ID)
    assert gate3 == "begun"
    assert rr._release_pack_kickoff_run(INTAKE_ID, prior3) is True


# ---------------------------------------------------------------------------
# Kickoff claim: details are merged, never replaced
# ---------------------------------------------------------------------------

def test_kickoff_claim_merges_details_without_dropping_keys(rail_env):
    db = rail_env
    _seed(db)  # created, with fulfillment details

    assert rr._try_claim_pack_kickoff(INTAKE_ID) is True
    row = pl.get_pack_intake(db, INTAKE_ID)
    assert row.status == "kickoff_claimed"
    # Every pre-existing key survives the claim's timestamp stamp.
    assert row.details["pack_name"] == "Credentialing Launch"
    assert row.details["module"] == "verticals/healthcare"
    assert row.details["payer_wallet"] == PAYER.lower()
    assert row.details["intake_token"] == INTAKE_TOKEN
    assert row.details["kickoff_claimed_at"]

    # The claim is still a claim: nobody else can take it.
    assert rr._try_claim_pack_kickoff(INTAKE_ID) is False


# ---------------------------------------------------------------------------
# Intake POST: advancing transition from agent_kickoff (guard path)
# ---------------------------------------------------------------------------

def test_intake_post_from_agent_kickoff_completes(rail_env):
    from sincor2.blueprints.intake import init_intake_routes

    db = rail_env
    _seed(db, status="agent_kickoff",
          details={"agent": {"agent": "healthcare_rcm_agent"}})
    app = Flask(__name__)
    app.config["TESTING"] = True
    init_intake_routes(app)
    client = app.test_client()

    res = client.post(
        f"/intake/{INTAKE_ID}?token={INTAKE_TOKEN}", json=ANSWERS)
    assert res.status_code == 200
    assert res.get_json()["status"] == "intake_complete"

    row = pl.get_pack_intake(db, INTAKE_ID)
    assert row.status == "intake_complete"
    assert row.details["intake_answers"] == ANSWERS
    assert row.details["agent"]["agent"] == "healthcare_rcm_agent"
    assert row.details["intake_token"] == INTAKE_TOKEN


# ---------------------------------------------------------------------------
# Unsubscribe: GET confirms, POST acts; production secret discipline
# ---------------------------------------------------------------------------

@pytest.fixture()
def unsub_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv(
        "UNSUBSCRIBE_TOKEN_SECRET", "pr8-secret-0123456789abcdef")
    return tmp_path


def _unsub_client():
    from sincor2.blueprints.unsubscribe import unsubscribe_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(unsubscribe_bp)
    return app.test_client()


def test_unsubscribe_get_renders_confirmation_without_suppressing(
        unsub_env):
    token = comms_compliance.make_unsubscribe_token("pr8-ops@example.com")
    assert token
    res = _unsub_client().get(f"/unsubscribe/{token}")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "<form" in body and 'method="post"' in body
    # A scanner prefetch must not unsubscribe anyone.
    assert comms_compliance.is_suppressed("pr8-ops@example.com") is False


def test_unsubscribe_post_suppresses(unsub_env):
    token = comms_compliance.make_unsubscribe_token("pr8-ops@example.com")
    res = _unsub_client().post(
        f"/unsubscribe/{token}", data={"List-Unsubscribe": "One-Click"})
    assert res.status_code == 200
    assert comms_compliance.is_suppressed("pr8-ops@example.com") is True


def test_unsubscribe_bad_token_404s_on_both_methods(unsub_env):
    client = _unsub_client()
    assert client.get("/unsubscribe/not-a-token").status_code == 404
    assert client.post("/unsubscribe/not-a-token").status_code == 404


def test_unsubscribe_secret_required_in_production(
        tmp_path, monkeypatch):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("UNSUBSCRIBE_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.setenv("SECRET_KEY", "app-secret-must-not-count-0123")
    # The app secret alone must NOT mint unsubscribe tokens in prod.
    assert comms_compliance.make_unsubscribe_token(
        "pr8-ops@example.com") is None
    # The dedicated secret re-enables them.
    monkeypatch.setenv(
        "UNSUBSCRIBE_TOKEN_SECRET", "dedicated-0123456789abcdef")
    token = comms_compliance.make_unsubscribe_token("pr8-ops@example.com")
    assert token
    assert comms_compliance.verify_unsubscribe_token(
        token) == "pr8-ops@example.com"


def test_unsubscribe_secret_fallback_outside_production(
        tmp_path, monkeypatch):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FLASK_ENV", "test")
    monkeypatch.delenv("UNSUBSCRIBE_TOKEN_SECRET", raising=False)
    monkeypatch.setenv("SECRET_KEY", "dev-secret-0123456789")
    assert comms_compliance.make_unsubscribe_token("pr8-ops@example.com")


# ---------------------------------------------------------------------------
# pack_email_failed alert: intake id, never the buyer's email
# ---------------------------------------------------------------------------

def test_pack_email_failed_alert_names_intake_not_buyer(
        rail_env, monkeypatch):
    db = rail_env

    def _boom(**kwargs):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(rr, "_send_pack_email", _boom)
    rr._fulfill_rail_pack({
        "event_id": EVENT_ID,
        "customer_email": BUYER_EMAIL,
        "customer_name": "Test Buyer",
        "pack_sku": "credentialing_launch",
        "payer_wallet": PAYER,
    })

    alerts = [a for a in pl.list_alerts(db, payment_id=EVENT_ID)
              if a.kind == "pack_email_failed"]
    assert len(alerts) == 1
    alert = alerts[0]
    assert INTAKE_ID in alert.message
    assert BUYER_EMAIL not in alert.message
    assert alert.details.get("intake_id") == INTAKE_ID


# ---------------------------------------------------------------------------
# _paid_skill_dispatch default branch: transport keys stripped
# ---------------------------------------------------------------------------

def test_paid_skill_dispatch_default_branch_strips_transport_keys(
        monkeypatch):
    import sincor2.x402_payments as x402

    seen = {}

    def _fake_execute(resource_id, payload):
        seen["payload"] = payload
        return 200, {"ok": True}

    monkeypatch.setattr(x402, "execute_paid_resource", _fake_execute)
    app = Flask(__name__)

    # JSON body shape: transport keys (any case) never reach the
    # handler; domain keys and 'wallet' pass through.
    with app.test_request_context(json={
            "access_token": "tok", "Payer_Wallet": "0xabc",
            "ACCESS_TOKEN": "tok2", "wallet": "0xdef",
            "query": "dental implants"}):
        status, _body = billing._paid_skill_dispatch(
            "unlisted-resource-pr8")
    assert status == 200
    lowered = {str(k).lower() for k in seen["payload"]}
    assert "access_token" not in lowered
    assert "payer_wallet" not in lowered
    assert seen["payload"]["wallet"] == "0xdef"
    assert seen["payload"]["query"] == "dental implants"

    # Args shape: same stripping (previously only an exact-case
    # 'access_token' pop).
    with app.test_request_context(
            "/api/skill?access_token=tok&Payer_Wallet=0xabc"
            "&wallet=0xdef&mode=read"):
        status, _body = billing._paid_skill_dispatch(
            "unlisted-resource-pr8")
    assert status == 200
    lowered = {str(k).lower() for k in seen["payload"]}
    assert "access_token" not in lowered
    assert "payer_wallet" not in lowered
    assert seen["payload"]["wallet"] == "0xdef"
    assert seen["payload"]["mode"] == "read"
