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
import sqlite3
from pathlib import Path

import pytest

import sincor2.rail_routes as rr
import sincor2.rail_subscriptions as rs
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
    from sincor2.rail_delivery import init_rail_delivery_routes

    template_dir = Path(__file__).resolve().parents[2] / "templates"
    app = Flask(__name__, template_folder=str(template_dir))
    app.config["TESTING"] = True
    rr.init_rail_routes(app)
    init_intake_routes(app)
    init_rail_delivery_routes(app)
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


# ---------------------------------------------------------------------------
# adversarial-fix round: server-issued subscription challenges must not
# feed the pack-path per-wallet cap, and issuance itself is throttled
#
# The PR 7 claim "the bound count can only be raised by the wallet's
# owner" was false for SUBSCRIPTION challenges: /rail/checkout with
# {subscription_plan, payer_wallet: W} server-binds a challenge to W
# with NO ownership signature (sound for settle — the benefit can only
# accrue to W — but those rows are indistinguishable in the counter's
# SQL from EIP-191-bound pack challenges). An attacker naming a victim
# 100x inflated the victim's bound count and locked the victim out of
# pack checkout with a 429. The fix: the pack-path counter excludes
# rail-sub-* resources, subscription issuance reuses a wallet's open
# challenge for the same plan instead of minting duplicates, and a
# per-wallet cap (5) bounds open server-issued challenges outright.
# ---------------------------------------------------------------------------

SUB_PLANS = ("starter", "professional", "enterprise")
SUB_CAP = 5  # rs.MAX_OPEN_SUB_CHALLENGES_PER_WALLET — see fix comment


def _sub_checkout(client, ip, plan, wallet):
    return client.post(
        "/rail/checkout",
        json={"subscription_plan": plan, "payer_wallet": wallet},
        environ_base={"REMOTE_ADDR": ip})


def _seed_server_issued(wallet, resource_id, count):
    """Mint + server-bind ``count`` challenges for ``wallet`` directly
    in the x402 store, bypassing issue_renewal_challenge — the state an
    unthrottled issuance path would accumulate."""
    ids = []
    for _ in range(count):
        minted = x402.create_challenge(resource_id)
        assert minted.get("challenge_id"), minted
        bound = x402.bind_server_issued_challenge(
            minted["challenge_id"], wallet)
        assert bound.get("ok"), bound
        ids.append(minted["challenge_id"])
    return ids


def test_subscription_flood_cannot_lock_victim_out_of_pack_checkout(
        rail_env, rail_app):
    """100 subscription checkouts naming the VICTIM's wallet (cycling
    all three plans, from the attacker's IP) must leave the victim's
    pack-path bound count at ZERO and the victim's own pack checkout
    working. Pre-fix every one of these mints landed in the victim's
    bound count — at 100 the victim's pack checkout returned 429."""
    client = rail_app.test_client()
    for i in range(100):
        resp = _sub_checkout(
            client, ATTACKER_IP, SUB_PLANS[i % len(SUB_PLANS)], VICTIM)
        assert resp.status_code == 402, resp.get_json()
        assert resp.get_json()["challenge_id"]
    # None of the server-issued rows count toward the pack-path cap.
    assert rr._wallet_open_bound_challenges(VICTIM.lower()) == 0
    # And the victim can still buy a pack from their own address.
    resp = _checkout(client, VICTIM_IP, wallet=VICTIM)
    assert resp.status_code == 402, resp.get_json()
    assert resp.get_json()["challenge_id"]


def test_subscription_issuance_capped_per_wallet(rail_env, rail_app):
    """A wallet already holding the cap of OPEN server-issued
    challenges is refused further issuance with the shared 429
    rate_limited shape — at the issuance choke point and over HTTP.
    The cap is per-wallet: any other wallet still issues."""
    assert rs.MAX_OPEN_SUB_CHALLENGES_PER_WALLET == SUB_CAP
    _seed_server_issued(VICTIM, "rail-sub-starter", SUB_CAP)

    with pytest.raises(rs.RateLimitedError):
        rs.issue_renewal_challenge(VICTIM, "professional")

    client = rail_app.test_client()
    resp = _sub_checkout(client, ATTACKER_IP, "professional", VICTIM)
    assert resp.status_code == 429, resp.get_json()
    assert resp.get_json()["error"] == "rate_limited"
    assert resp.get_json()["ok"] is False

    # A different wallet is not collateral damage.
    resp = _sub_checkout(client, ATTACKER_IP, "professional", PAYER)
    assert resp.status_code == 402, resp.get_json()
    assert resp.get_json()["challenge_id"]


def test_subscription_reissue_reuses_open_challenge(rail_env, rail_app):
    """Legitimate flow stays intact — and gets idempotent: a second
    checkout for the same wallet + plan while the first challenge is
    still open returns THAT challenge (a page reload or double-click
    must not pile up duplicate challenges), a different plan issues
    its own, and the issued challenge is the recorded server-bound
    issuance settle will require."""
    client = rail_app.test_client()
    r1 = _sub_checkout(client, VICTIM_IP, "starter", PAYER)
    assert r1.status_code == 402, r1.get_json()
    cid1 = r1.get_json()["challenge_id"]
    assert cid1

    r2 = _sub_checkout(client, VICTIM_IP, "starter", PAYER)
    assert r2.status_code == 402, r2.get_json()
    assert r2.get_json()["challenge_id"] == cid1
    assert r2.get_json()["payment"] == r1.get_json()["payment"]

    r3 = _sub_checkout(client, VICTIM_IP, "professional", PAYER)
    assert r3.status_code == 402, r3.get_json()
    cid3 = r3.get_json()["challenge_id"]
    assert cid3 and cid3 != cid1

    issued = rs._get_issued(cid1)
    assert issued is not None
    assert issued["wallet"] == PAYER.lower()
    assert issued["plan_id"] == "starter"
    # Exactly one open server-issued challenge per plan — no duplicates.
    assert len(rs._open_server_issued_challenges(PAYER.lower())) == 2


def test_settled_subscription_challenge_is_never_reused(
        rail_env, rail_app):
    """Reuse covers ONLY the never-settled in-flight checkout. Once a
    challenge has been presented for settlement — a rail_sub_settles
    row exists, the subscription layer's authoritative journal — it is
    no longer open, and the next checkout mints a FRESH challenge:
    re-issuing the settled one would hand the buyer an
    already_renewed dead end instead of a new billing period (the
    exact failure the atomic-extend renewal suites caught in the
    first fix draft, where "open" was judged by the x402 row's
    status alone)."""
    client = rail_app.test_client()
    r1 = _sub_checkout(client, VICTIM_IP, "starter", PAYER)
    assert r1.status_code == 402, r1.get_json()
    cid1 = r1.get_json()["challenge_id"]
    assert cid1

    # Record the settle claim the way _claim_sub_settle does.
    conn = sqlite3.connect(rail_env["revenue_db"])
    try:
        conn.execute(
            "INSERT INTO rail_sub_settles "
            "(settle_id, challenge_id, wallet, plan_id, created_at) "
            "VALUES (?,?,?,?,?)",
            (f"railsub-{cid1}", cid1, PAYER.lower(), "starter",
             "2026-11-04T00:00:00+00:00"))
        conn.commit()
    finally:
        conn.close()

    r2 = _sub_checkout(client, VICTIM_IP, "starter", PAYER)
    assert r2.status_code == 402, r2.get_json()
    assert r2.get_json()["challenge_id"] != cid1


# ---------------------------------------------------------------------------
# download response headers (HOLE 3): the /download URL is the same
# class of bearer credential as the intake URL — PR 7's own gunicorn
# comment names both — so its responses carry the same three headers.
# ---------------------------------------------------------------------------

DELIVERY_TOKEN = "<MASKED_SECRET_002>"
DIGITAL_SKU = "digital_bi_template_pack"
DIGITAL_ASSET = "money_assets/instant_bi_template_pack.md"


def _seed_digital_intake(db, intake_id="intake-rail-pr7dl01"):
    details = {
        "pack_name": rr.PACK_SKUS[DIGITAL_SKU]["name"],
        "delivery_token": DELIVERY_TOKEN,
        "asset": DIGITAL_ASSET,
        "rail": True,
    }
    intake, _is_new = pl.record_pack_intake(
        db,
        intake_id=intake_id,
        customer_email="buyer@example.com",
        pack_sku=DIGITAL_SKU,
        event_id="rail-pr7dl01",
        intake_url=f"https://getsincor.com/download/{intake_id}"
                   f"?token={DELIVERY_TOKEN}",
        details=details,
    )
    return intake


def test_download_responses_carry_privacy_headers(rail_env, rail_app):
    """The download success response AND the fail-closed 404 carry
    no-store / noindex / no-referrer, matching the intake treatment:
    the ?token= URL must not be stored, indexed, or leaked as a
    referrer. (Pre-fix the 200 carried Cache-Control only.)"""
    intake = _seed_digital_intake(rail_env["revenue_db"])
    client = rail_app.test_client()

    resp = client.get(
        f"/download/{intake.intake_id}?token={DELIVERY_TOKEN}")
    assert resp.status_code == 200, resp.status_code
    _assert_privacy_headers(resp)

    resp = client.get(f"/download/{intake.intake_id}?token=wrong-token")
    assert resp.status_code == 404
    _assert_privacy_headers(resp)
