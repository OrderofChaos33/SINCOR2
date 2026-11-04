"""WREN PR 7: checkout griefing mitigation & log sanitization.

Three changes, three test groups:

  * The /rail/checkout open-challenge cap keys on the CLIENT ADDRESS
    (request.remote_addr), never on a client-supplied wallet. Under the
    PR 4 keying (cap source = the named wallet) an attacker could name
    a victim's wallet in 20 checkout calls and lock that wallet out of
    checkout entirely. Keyed on IP, a flood burns only the flooder's
    own budget — while the per-IP cap still trips for the flooder.
  * A separate per-wallet cap (100) counts only BOUND open challenges.
    Binding requires an EIP-191 ownership signature from the wallet
    itself, so no third party can inflate a victim's count; the cap
    only stops one wallet accumulating unbounded in-flight challenges.
  * gunicorn's access log format is set explicitly WITHOUT the query
    string (%(U)s, not %(r)s): intake/download URLs carry bearer
    tokens in ?token=..., and the default format would write them into
    Railway's plain-text logs. Intake responses additionally carry
    Cache-Control: no-store, X-Robots-Tag: noindex and
    Referrer-Policy: no-referrer.

Everything is hermetic: temp DBs, no network, no RPC (checkout never
touches the chain layer). Test wallets are the well-known PUBLIC
Hardhat dev accounts — test-only, never funded.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import sincor2.rail_routes as rr
import sincor2.x402_payments as x402
from sincor2 import payment_ledger as pl

from eth_account import Account as _Account
from eth_account.messages import encode_defunct as _encode_defunct

# Hardhat dev account #0 — the binder/payer (same account the other
# rail suites use; its key is public and the account is never funded).
_PAYER_ACCT = _Account.from_key(
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
PAYER = _PAYER_ACCT.address

# Hardhat dev account #1 — the griefing victim: a wallet the attacker
# names at checkout but does not control.
_VICTIM_ACCT = _Account.from_key(
    "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d")
VICTIM = _VICTIM_ACCT.address

FRESH_WALLET = "0x" + "22" * 20  # well-formed, never used anywhere

ATTACKER_IP = "203.0.113.10"   # TEST-NET-3
VICTIM_IP = "198.51.100.23"    # TEST-NET-2

PACK_SKU = "credentialing_launch"
RESOURCE_ID = f"rail-pack-{PACK_SKU}"

INTAKE_TOKEN = "<MASKED_SECRET_001>"

CRED_ANSWERS = {
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
    """Hermetic stores: every DB the rail + intake blueprints read is
    redirected into this test's tmp dir."""
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    x402.init_x402_db()
    return {"revenue_db": str(tmp_path / "revenue.db")}


@pytest.fixture(scope="module")
def rail_app():
    """Minimal Flask app: the real rail blueprint + intake blueprint,
    with the repo template folder (mirrors the mvp_app mount)."""
    from flask import Flask

    from sincor2.blueprints.intake import init_intake_routes

    template_dir = Path(__file__).resolve().parents[2] / "templates"
    app = Flask(__name__, template_folder=str(template_dir))
    app.config["TESTING"] = True
    rr.init_rail_routes(app)
    init_intake_routes(app)
    return app


def _checkout(client, ip, wallet=None):
    body = {"pack_sku": PACK_SKU}
    if wallet:
        body["payer_wallet"] = wallet
    return client.post("/rail/checkout", json=body,
                       environ_base={"REMOTE_ADDR": ip})


def _bind(challenge_id, acct=_PAYER_ACCT):
    """Complete the EIP-191 ownership bind — the step that turns a
    minted draft into a BOUND open challenge for the wallet."""
    sig = acct.sign_message(_encode_defunct(
        text=x402.challenge_bind_message(
            challenge_id, RESOURCE_ID, acct.address))).signature.hex()
    bound = x402.bind_challenge_payer(challenge_id, acct.address, sig)
    assert bound.get("ownership_proven") is True, bound
    return bound


# ---------------------------------------------------------------------------
# griefing: cap keyed on the client address, not the named wallet
# ---------------------------------------------------------------------------

def test_flood_naming_victim_wallet_does_not_lock_victim_out(
        rail_env, rail_app):
    """20 checkouts from IP A naming wallet W must not block a checkout
    naming W from IP B. (Pre-PR7 the cap keyed on W itself, so the
    flood capped the victim — this is the lockout being fixed.)"""
    client = rail_app.test_client()
    cap = x402.MAX_OPEN_UNBOUND_CHALLENGES_PER_SOURCE
    for _ in range(cap):
        resp = _checkout(client, ATTACKER_IP, wallet=VICTIM)
        assert resp.status_code == 402, resp.get_json()
        assert resp.get_json()["challenge_id"]
    # The victim, from their own address, still mints.
    resp = _checkout(client, VICTIM_IP, wallet=VICTIM)
    assert resp.status_code == 402, resp.get_json()
    assert resp.get_json()["challenge_id"]


def test_per_ip_cap_still_trips_for_the_flooding_ip(rail_env, rail_app):
    """The cap still exists — it just burns the flooder's own budget:
    past the per-source limit, IP A is refused no matter which wallet
    it names (or none), while other IPs are unaffected."""
    client = rail_app.test_client()
    cap = x402.MAX_OPEN_UNBOUND_CHALLENGES_PER_SOURCE
    for _ in range(cap):
        resp = _checkout(client, ATTACKER_IP, wallet=VICTIM)
        assert resp.status_code == 402, resp.get_json()
    resp = _checkout(client, ATTACKER_IP, wallet=FRESH_WALLET)
    assert resp.status_code == 429, resp.get_json()
    assert resp.get_json()["error"] == "rate_limited"
    resp = _checkout(client, ATTACKER_IP)  # anonymous mints count too
    assert resp.status_code == 429, resp.get_json()
    assert resp.get_json()["error"] == "rate_limited"
    # A different client address is not collateral damage.
    resp = _checkout(client, VICTIM_IP, wallet=FRESH_WALLET)
    assert resp.status_code == 402, resp.get_json()


def test_per_wallet_bound_cap_trips_at_threshold(rail_env, rail_app):
    """The separate per-wallet cap (100) counts BOUND open challenges —
    state only the wallet owner can create, via a signed bind. Setup
    runs at the module layer (mints rotate source keys so the per-IP
    cap stays out of the way); the assertions run over HTTP."""
    limit = rr.MAX_OPEN_BOUND_CHALLENGES_PER_WALLET
    per_ip = x402.MAX_OPEN_UNBOUND_CHALLENGES_PER_SOURCE
    for i in range(limit):
        minted = x402.create_challenge(
            RESOURCE_ID, source_key=f"10.9.0.{i // per_ip + 1}")
        assert minted.get("challenge_id"), minted
        _bind(minted["challenge_id"])
    assert rr._wallet_open_bound_challenges(PAYER.lower()) == limit

    client = rail_app.test_client()
    # Naming the capped wallet is refused even from a fresh IP...
    resp = _checkout(client, VICTIM_IP, wallet=PAYER)
    assert resp.status_code == 429, resp.get_json()
    assert resp.get_json()["error"] == "rate_limited"
    # ...while any other wallet from that same IP still checks out.
    resp = _checkout(client, VICTIM_IP, wallet=VICTIM)
    assert resp.status_code == 402, resp.get_json()
    assert resp.get_json()["challenge_id"]


# ---------------------------------------------------------------------------
# log sanitization: gunicorn access log carries no query string
# ---------------------------------------------------------------------------

def test_gunicorn_access_log_format_omits_query_string():
    """Load gunicorn.conf.py the way gunicorn does (import the file,
    push the setting through gunicorn's own Config validation) and
    assert the effective format logs the path only — no %(r)s request
    line, no %(q)s query atom — so ?token=... never reaches the logs."""
    from gunicorn.config import Config

    conf_path = Path(__file__).resolve().parents[2] / "gunicorn.conf.py"
    spec = importlib.util.spec_from_file_location(
        "sincor_gunicorn_conf", conf_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    cfg = Config()
    cfg.set("access_log_format", module.access_log_format)
    fmt = cfg.access_log_format
    assert fmt == '%(h)s %(t)s "%(m)s %(U)s" %(s)s %(b)s %(L)s'
    assert "%(U)s" in fmt       # URL path without query string
    assert "%(r)s" not in fmt   # full request line includes the query
    assert "%(q)s" not in fmt   # the query-string atom itself


# ---------------------------------------------------------------------------
# intake response headers
# ---------------------------------------------------------------------------

def _seed_intake(db, intake_id="intake-rail-pr7test01"):
    details = {
        "pack_name": rr.PACK_SKUS[PACK_SKU]["name"],
        "module": rr.PACK_SKUS[PACK_SKU].get("module", ""),
        "payer_wallet": PAYER.lower(),
        "rail": True,
        "intake_token": INTAKE_TOKEN,
    }
    intake, _is_new = pl.record_pack_intake(
        db,
        intake_id=intake_id,
        customer_email="buyer@example.com",
        pack_sku=PACK_SKU,
        event_id="rail-pr7test01",
        intake_url=f"https://getsincor.com/intake/{intake_id}"
                   f"?token={INTAKE_TOKEN}",
        details=details,
    )
    return intake


def _assert_privacy_headers(resp):
    assert resp.headers.get("Cache-Control") == "no-store"
    assert resp.headers.get("X-Robots-Tag") == "noindex"
    assert resp.headers.get("Referrer-Policy") == "no-referrer"


def test_intake_responses_carry_privacy_headers(rail_env, rail_app):
    """Every intake response — the form page, the submit ack, and the
    fail-closed 404 — carries no-store / noindex / no-referrer: the URL
    is a bearer credential and must not be stored, indexed, or leaked
    as a referrer."""
    intake = _seed_intake(rail_env["revenue_db"])
    client = rail_app.test_client()
    url = f"/intake/{intake.intake_id}?token={INTAKE_TOKEN}"

    resp = client.get(url)
    assert resp.status_code == 200, resp.status_code
    _assert_privacy_headers(resp)

    resp = client.post(url, json=CRED_ANSWERS)
    assert resp.status_code == 200, resp.get_json()
    _assert_privacy_headers(resp)

    resp = client.get(f"/intake/{intake.intake_id}?token=wrong-token")
    assert resp.status_code == 404
    _assert_privacy_headers(resp)
