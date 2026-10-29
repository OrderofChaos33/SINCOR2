"""Machine-surface contract: discovery/quote/docs URLs serve JSON to
machines regardless of Accept headers, and the human pages that point at
them describe the real flows.

Regression context (live crawl, 2026-10-28): on the production (mvp) app a
``before_request`` hook redirected browser-Accept requests away from
machine URLs — ``/.well-known/agent-card.json`` → ``/agent-card`` HTML,
``/api/a2a/quote?skill_id=toa-decision`` → ``/toa`` HTML, and
``/docs/a2a`` → ``/register-agent`` (whose "Machine docs JSON" button then
looped). Machines are these URLs' audience: JSON is the behavior for every
Accept header. The HTML explainers stay at ``/agent-card`` / ``/toa`` /
``/register-agent``.

Also pinned here:
- the legacy agent card must not send integrators to the dead
  ``/api-keys`` page or claim an X-API-Key scheme the platform does not
  use (canonical card: ``security_schemes={}`` — no API key; registration
  binds a wallet with an EIP-191 proof);
- ``/register-agent`` presents the real registration flow
  (``POST /v1/a2a/register``) instead of implying a web form;
- ``/login`` renders its disabled password form visibly disabled and its
  OAuth hint names only providers that actually render a button.
"""
from __future__ import annotations

import os
import re
import time

import pytest

BROWSER = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
JSON_ACCEPT = {"Accept": "application/json"}


@pytest.fixture
def mvp_client():
    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("ENVIRONMENT", "test")
    from sincor2.mvp_app import app as mvp_app

    mvp_app.config["TESTING"] = True
    mvp_app.config["SERVER_NAME"] = None
    return mvp_app.test_client()


# ── 1. Agent card: JSON for every Accept ────────────────────────────────────

@pytest.mark.parametrize("headers", [BROWSER, JSON_ACCEPT, {}],
                         ids=["browser-accept", "json-accept", "no-accept"])
def test_agent_card_json_regardless_of_accept(mvp_client, headers):
    resp = mvp_client.get("/.well-known/agent-card.json", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("application/json")
    payload = resp.get_json()
    assert payload is not None
    assert payload.get("name")
    assert len(payload.get("skills") or []) == 17


def test_agent_card_html_explainer_still_at_agent_card(mvp_client):
    resp = mvp_client.get("/agent-card", headers=BROWSER)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["Content-Type"]


# ── 2. Quote endpoint: JSON for API callers, no HTML swallow ────────────────

@pytest.mark.parametrize("headers", [BROWSER, JSON_ACCEPT, {}],
                         ids=["browser-accept", "json-accept", "no-accept"])
def test_quote_toa_decision_json_regardless_of_accept(mvp_client, headers):
    resp = mvp_client.get("/api/a2a/quote?skill_id=toa-decision", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("application/json")
    payload = resp.get_json()
    assert payload is not None
    assert payload.get("skill_id") == "toa-decision"
    assert payload.get("pay_to")


def test_quote_other_skill_json_with_browser_accept(mvp_client):
    resp = mvp_client.get("/api/a2a/quote?skill_id=lead-enrichment", headers=BROWSER)
    assert resp.status_code == 200
    assert resp.get_json().get("skill_id") == "lead-enrichment"


# ── 3. Legacy card: no dead /api-keys URL, no phantom API-key scheme ────────

@pytest.mark.parametrize("headers", [BROWSER, {}], ids=["browser-accept", "no-accept"])
def test_legacy_card_json_and_no_dead_api_key_url(mvp_client, headers):
    resp = mvp_client.get("/.well-known/agent.json", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("application/json")
    body = resp.get_data(as_text=True)
    assert "api-keys" not in body
    assert "X-API-Key" not in body
    auth = (resp.get_json() or {}).get("authentication") or {}
    assert "register" in (auth.get("description") or "").lower()


def test_legacy_dict_unit_has_no_dead_url():
    from sincor2.a2a_integration import build_agent_card

    legacy = build_agent_card().to_legacy_dict()
    text = str(legacy)
    assert "api-keys" not in text
    assert "X-API-Key" not in text


# ── 4a. Machine docs: /docs/a2a serves JSON, never redirects to the
#        human register page (the loop the register page button hit) ────────

@pytest.mark.parametrize("headers", [BROWSER, JSON_ACCEPT, {}],
                         ids=["browser-accept", "json-accept", "no-accept"])
def test_docs_a2a_json_regardless_of_accept(mvp_client, headers):
    resp = mvp_client.get("/docs/a2a", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("application/json")
    payload = resp.get_json()
    assert payload is not None
    assert payload.get("discovery", {}).get("agentCard", "").endswith(
        "/.well-known/agent-card.json")


# ── 4b. /register-agent presents the REAL registration flow ─────────────────

def test_register_agent_page_describes_real_api_flow(mvp_client):
    resp = mvp_client.get("/register-agent")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # The real endpoint, a curl example, and the EIP-191 proof fields.
    assert "POST /v1/a2a/register" in html
    assert "curl" in html
    assert "registration_signature" in html
    assert "registration_wallet" in html
    assert "registration_ts" in html
    assert "SINCOR-REGISTER" in html
    # No web registration form is implied: registration is an API call.
    assert "<form" not in html.lower()


def test_register_agent_machine_docs_link_returns_json(mvp_client):
    html = mvp_client.get("/register-agent").get_data(as_text=True)
    assert 'href="/docs/a2a"' in html
    # Following that link exactly as a browser would must land on JSON,
    # not bounce back to /register-agent.
    resp = mvp_client.get("/docs/a2a", headers=BROWSER)
    assert resp.status_code == 200
    assert resp.get_json() is not None


def test_documented_anonymous_registration_actually_works(mvp_client):
    resp = mvp_client.post("/v1/a2a/register", json={
        "agent_id": "machine-surface-probe-anon",
        "name": "Machine Surface Probe",
        "capability_tags": ["data"],
    })
    assert resp.status_code == 201
    payload = resp.get_json()
    assert payload["agent_id"] == "machine-surface-probe-anon"
    assert payload["status"] == "registered"
    assert payload["identity"] == "unverified"


def test_documented_wallet_registration_requires_eip191_proof(mvp_client):
    wallet = "0x" + "22" * 20
    resp = mvp_client.post("/v1/a2a/register", json={
        "agent_id": "machine-surface-probe-noproof",
        "name": "Machine Surface Probe",
        "capability_tags": ["data"],
        "wallet": wallet,
    })
    assert resp.status_code == 403


def test_documented_signed_wallet_registration_works(mvp_client):
    from eth_account import Account
    from eth_account.messages import encode_defunct

    from sincor2.a2a_identity import register_message

    acct = Account.from_key("0x" + "33" * 32)
    agent_id = "machine-surface-probe-signed"
    ts_ms = int(time.time() * 1000)
    sig = acct.sign_message(
        encode_defunct(text=register_message(agent_id, ts_ms))).signature
    resp = mvp_client.post("/v1/a2a/register", json={
        "agent_id": agent_id,
        "name": "Machine Surface Probe",
        "capability_tags": ["data"],
        "wallet": acct.address,
        "registration_wallet": acct.address,
        "registration_signature": "0x" + bytes(sig).hex(),
        "registration_ts": ts_ms,
    })
    assert resp.status_code == 201
    payload = resp.get_json()
    assert payload["identity"] == "verified"
    assert payload["owner_wallet"] == acct.address.lower()


# ── 5. /login: disabled password form is visibly disabled; OAuth hint
#        names only providers whose button actually renders ─────────────────

def _button_tag(html: str) -> str:
    m = re.search(r"<button[^>]*>\s*Sign in\s*</button>", html)
    assert m, "Sign in button not found"
    return m.group(0)


def test_login_password_form_visibly_disabled(mvp_client):
    html = mvp_client.get("/login").get_data(as_text=True)
    assert "disabled" in _button_tag(html)
    # The credential inputs are disabled too — not just the button.
    for field in ("identifier", "password"):
        m = re.search(r'<input[^>]*name="%s"[^>]*>' % field, html)
        assert m, f"input {field} not found"
        assert "disabled" in m.group(0), f"input {field} not disabled"
    assert "password sign-in is currently disabled" in html.lower()


def test_login_oauth_hint_matches_rendered_buttons(mvp_client):
    html = mvp_client.get("/login").get_data(as_text=True)
    status = mvp_client.get("/api/auth/oauth-status").get_json()
    hint = " ".join(re.findall(r"Customer password sign-in is[^<]*", html))
    assert hint, "disabled-password hint missing"
    # A provider may be named in the hint only if its button renders,
    # and a rendered button must be named in the hint.
    for provider, label in (("google", "Google"), ("github", "GitHub")):
        renders = f'href="/auth/{provider}"' in html
        assert renders == bool(status.get(provider)), (
            f"{provider} button rendering disagrees with oauth-status")
        assert (label in hint) == renders, (
            f"hint names {label}={label in hint} but button renders={renders}")
