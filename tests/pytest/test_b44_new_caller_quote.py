"""B44 (Base44 external review, 2026-11-06): new-caller quote reports 0.

The documented funnel (static/llms.txt, static/ai.txt) tells a brand-new
external caller to quote unauthenticated —

    GET /api/a2a/quote?skill_id=<id>&caller_id=<you>

— and promises "Five skills offer a free quota (5 free executions per
caller) for new external callers" plus a free daily ration of 3 calls
per agent per skill per day. The live API answered that exact call with
``free_quota_remaining: 0`` and ``free_daily_ration_remaining: 0`` for
three brand-new caller IDs: the quote conflated "no verified identity
presented yet" (no attributable usage exists) with "entitlement
exhausted".

These tests pin the fixed reporting: a caller who has presented no
verified identity is shown the documented NEW-CALLER entitlement (5 on
the top-5 skills, 3/day ration on every skill), while ``is_free`` stays
False and the paid price stays visible — nothing is granted by a quote.
Consumption on send still requires the verified/registered identities
exactly as before (G2.6 anti-sybil + Wave 1 registered gating), and a
verified wallet still sees its own attributable remaining.

Run: PYTHONPATH=src:. <venv>/python -m pytest \
  tests/pytest/test_b44_new_caller_quote.py -q
"""
from __future__ import annotations

import time

import pytest
from flask import Flask

from sincor2 import a2a_integration as ai

TOP5_SKILL = "lead-enrichment"
NON_TOP5_SKILL = "compliance-sbom"


@pytest.fixture()
def client():
    from sincor2.free_ration import daily_ration_tracker

    ai._free_quota_tracker._store.clear()
    daily_ration_tracker.clear()
    app = Flask("b44_quote")
    app.register_blueprint(ai.A2ARouter().blueprint)
    app.config.update(TESTING=True)
    yield app.test_client()
    ai._free_quota_tracker._store.clear()
    daily_ration_tracker.clear()


def _skill(skill_id):
    return next(s for s in ai.SINCOR_SKILLS if s.id == skill_id)


# ---------------------------------------------------------------------------
# The review repro: brand-new caller IDs on the documented quote call
# ---------------------------------------------------------------------------

def test_new_caller_get_quote_reports_documented_entitlement(client):
    """GET exactly as llms.txt documents it, three fresh caller IDs."""
    for caller_id in ("brand-new-1", "brand-new-2", "brand-new-3"):
        resp = client.get(
            f"/api/a2a/quote?skill_id={TOP5_SKILL}&caller_id={caller_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["free_quota_remaining"] == _skill(TOP5_SKILL).free_quota
        assert data["free_daily_ration_remaining"] == ai.DAILY_RATION_LIMIT
        # Nothing is granted by a quote: not marked free, price visible.
        assert data["is_free"] is False
        assert data["axm_price_wei"] != "0"


def test_new_caller_post_quote_reports_documented_entitlement(client):
    resp = client.post("/api/a2a/quote", json={
        "skill_id": TOP5_SKILL, "caller_id": "brand-new-caller-xyz"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["free_quota_remaining"] == _skill(TOP5_SKILL).free_quota
    assert data["free_daily_ration_remaining"] == ai.DAILY_RATION_LIMIT
    assert data["is_free"] is False


def test_new_caller_non_top5_skill_reports_daily_ration(client):
    """No lifetime quota outside the top 5, but the daily ration is
    universal: a new caller must see 3, not 0."""
    resp = client.get(
        f"/api/a2a/quote?skill_id={NON_TOP5_SKILL}&caller_id=brand-new-1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["free_quota_remaining"] == 0
    assert data["free_daily_ration_remaining"] == ai.DAILY_RATION_LIMIT
    assert data["is_free"] is False


def test_new_caller_quote_note_names_the_claiming_steps(client):
    """The numbers come with the documented steps to claim them, so the
    entitlement is not read as already-granted."""
    data = client.get(
        f"/api/a2a/quote?skill_id={TOP5_SKILL}&caller_id=brand-new-1"
    ).get_json()
    assert "signature" in data["note"]
    data = client.get(
        f"/api/a2a/quote?skill_id={NON_TOP5_SKILL}&caller_id=brand-new-1"
    ).get_json()
    assert "registered agent" in data["note"]


# ---------------------------------------------------------------------------
# Anti-abuse invariants: reporting must not become a grant
# ---------------------------------------------------------------------------

def test_new_caller_quotes_do_not_consume_any_quota(client):
    """Repeated new-caller quotes consume nothing and do not touch a
    verified wallet's attributable bucket."""
    for _ in range(3):
        client.get(
            f"/api/a2a/quote?skill_id={TOP5_SKILL}&caller_id=brand-new-1")
    data = client.get(
        f"/api/a2a/quote?skill_id={TOP5_SKILL}&caller_id=brand-new-1"
    ).get_json()
    assert data["free_quota_remaining"] == _skill(TOP5_SKILL).free_quota
    assert ai._free_quota_tracker._store.get(
        "brand-new-1", TOP5_SKILL) == 0


def test_unsigned_send_still_gets_no_free_work(client):
    """A quote showing the entitlement must not let an unsigned send
    consume it — the G2.6 bypass stays closed."""
    body = {
        "jsonrpc": "2.0", "id": 1, "method": "message/send",
        "params": {
            "skillId": TOP5_SKILL,
            "callerId": "brand-new-caller-xyz",
            "message": {
                "role": "user",
                "parts": [{"text": "unsigned attempt"}],
                "metadata": {},
            },
        },
    }
    resp = client.post("/api/a2a", json=body)
    payload = resp.get_json()
    if resp.status_code == 200 and "result" in payload:
        assert payload["result"]["metadata"]["free_call"] is False
    else:
        # Strict create-auth tree: rejected before quota logic (401).
        assert resp.status_code in (401, 403) or "error" in payload
    assert ai._free_quota_tracker._store.get(
        "brand-new-caller-xyz", TOP5_SKILL) == 0


def test_signed_wallet_still_sees_its_own_attributable_remaining(client):
    """Verified-identity reporting is unchanged: a fresh signed wallet
    sees its real remaining (5), keyed on the wallet, not caller_id."""
    from eth_account import Account
    from eth_account.messages import encode_defunct

    acct = Account.create()
    ts = int(time.time() * 1000)
    message = ai.quota_message_for_quote(TOP5_SKILL, ts)
    sig = acct.sign_message(encode_defunct(text=message)).signature.hex()
    resp = client.post("/api/a2a/quote", json={
        "skill_id": TOP5_SKILL, "signature": "0x" + sig,
        "quota_ts": str(ts), "wallet": acct.address,
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["free_quota_remaining"] == _skill(TOP5_SKILL).free_quota
    assert data["is_free"] is True
