"""Local-Business Site Check (G8) — unit + full-loop x402 + rail tests.

Unit layer proves the HONESTY CORE against fixture pages injected
through the module's fetch seam (``serve_site_check(..., fetch=...)``
— the same seam the production ``fetch_site_page`` fills). No live
network, no Places/Yelp API keys anywhere: the check is built on the
fetch seam alone because Scout/Places on main requires live keys
(see the module docstring).

Fixture verdicts (skills-gap-map-2026-10-24 §4 item 6):
- (c) a good modern site  -> ``no_build_needed`` and NO upsell
  (the anti-upsell test);
- (b) a deficient site    -> ``build_recommended`` with named
  deficiencies, each carrying its proving observation, plus the
  webbuilder next-step read from the real rail SKU table ($497);
- (a) no site at all      -> ``build_recommended`` / ``no_website``;
- unreachable / empty     -> ``unknown``, never a guess.

Integration layer proves the complete first-dollar loop through the
real Flask routes (same mock pattern as test_market_data_read.py):
402 -> EIP-191 bound challenge -> verify/settle -> single-use token
-> verdict delivered -> tx_hash replay rejected -> token replay
rejected -> unbound challenge rejected -> underpayment rejected.

Final test proves the spec's upsell path end-to-end: a flagged
(build_recommended) lead completes the REAL rail purchase of the
$497 webbuilder pack (/rail/checkout -> /rail/settle, stubbed chain
receipt as in test_webbuilder_sku.py) and exactly one WebBuilder
project is built for that business.
"""
from __future__ import annotations

import socket
import sqlite3

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

# Production import order matters: mvp_app must be imported before the
# billing blueprint (see test_x402_verify_frontrun.py).
import sincor2.mvp_app  # noqa: F401  (imported for binding side effects)
from sincor2.mvp_blueprints import billing

import sincor2.site_check as sc
import sincor2.x402_payments as x402

RESOURCE = "local-business-site-check"
GOOD_URL = "https://acmedental.example.com/"
DEFICIENT_URL = "http://oldsite.example.com/"

GOOD_HTML = """<!doctype html><html><head>
<title>Acme Dental — Clinton, Iowa</title>
<meta name="description" content="Family dental care in Clinton, Iowa.">
<meta name="viewport" content="width=device-width, initial-scale=1">
</head><body><h1>Acme Dental</h1>
<a href="tel:+15551234567">Call us</a>
<a href="/contact">Contact</a>
</body></html>"""

DEFICIENT_HTML = """<html><head></head><body>
<p>We do stuff. Est. 1998.</p>
<a href="/about">About</a>
</body></html>"""

GOOD_PAGE = sc.SiteFetch(status_code=200, url=GOOD_URL, html=GOOD_HTML)
DEFICIENT_PAGE = sc.SiteFetch(status_code=200, url=DEFICIENT_URL,
                              html=DEFICIENT_HTML)


def _serve(page, payload):
    return sc.serve_site_check(payload, fetch=lambda url: page)


# ---------------------------------------------------------------------------
# Unit: the honesty core — three verdicts, evidence or nothing
# ---------------------------------------------------------------------------

class TestHonestVerdicts:
    def test_good_site_returns_no_build_needed_and_no_upsell(self):
        """THE anti-upsell test: a business with a good site must get
        'no build needed' — and the response must not carry the $497
        webbuilder next-step at all."""
        status, body = _serve(GOOD_PAGE, {"url": GOOD_URL,
                                         "business_name": "Acme Dental"})
        assert status == 200
        assert body["ok"] is True
        assert body["verdict"] == "no_build_needed"
        assert body["has_website"] is True
        assert body["deficiencies"] == []
        assert "next_step" not in body

    def test_good_site_observations_match_fixture(self):
        status, body = _serve(GOOD_PAGE, {"url": GOOD_URL})
        assert status == 200
        obs = body["observations"]
        assert obs["status_code"] == 200
        assert obs["https"] is True
        assert obs["title"] == "Acme Dental — Clinton, Iowa"
        assert obs["has_meta_description"] is True
        assert obs["has_viewport"] is True
        assert obs["h1"] == ["Acme Dental"]
        assert "tel:+15551234567" in obs["contact_paths"]

    def test_deficient_site_build_recommended_with_named_evidenced_deficiencies(self):
        status, body = _serve(DEFICIENT_PAGE, {"url": DEFICIENT_URL})
        assert status == 200
        assert body["verdict"] == "build_recommended"
        codes = {d["code"] for d in body["deficiencies"]}
        assert codes == {"not_https", "missing_title",
                         "missing_meta_description", "missing_viewport",
                         "missing_h1", "no_contact_path"}
        # Every deficiency carries the observation that proves it.
        for d in body["deficiencies"]:
            assert isinstance(d["evidence"], str) and d["evidence"]
        # The upsell appears ONLY here, priced from the rail table.
        step = body["next_step"]
        assert step["sku"] == "webbuilder_site"
        assert step["resource"] == "rail-pack-webbuilder_site"
        assert step["price_usdc"] == 497.0
        assert step["checkout"] == "/rail/checkout"

    def test_no_website_business_build_recommended_without_fetch(self):
        def _boom(url):
            raise AssertionError("fetch must not run without a URL")
        status, body = sc.serve_site_check(
            {"business_name": "No Site Plumbing"}, fetch=_boom)
        assert status == 200
        assert body["verdict"] == "build_recommended"
        assert body["has_website"] is False
        assert [d["code"] for d in body["deficiencies"]] == ["no_website"]
        assert body["deficiencies"][0]["evidence"]
        assert body["next_step"]["price_usdc"] == 497.0

    def test_unreachable_site_returns_unknown_never_a_guess(self):
        status, body = _serve(None, {"url": GOOD_URL})
        assert status == 200
        assert body["verdict"] == "unknown"
        assert body["reason"] == "site_unreachable"
        assert body["deficiencies"] == []
        assert "next_step" not in body

    def test_fetch_exception_returns_unknown(self):
        def _down(url):
            raise RuntimeError("connection refused")
        status, body = sc.serve_site_check({"url": GOOD_URL}, fetch=_down)
        assert status == 200
        assert body["verdict"] == "unknown"
        assert body["reason"] == "site_unreachable"

    def test_empty_page_returns_unknown_insufficient_data(self):
        empty = sc.SiteFetch(status_code=200, url=GOOD_URL, html="  ")
        status, body = _serve(empty, {"url": GOOD_URL})
        assert status == 200
        assert body["verdict"] == "unknown"
        assert body["reason"] == "insufficient_data"
        assert "next_step" not in body

    def test_http_error_status_is_an_evidenced_deficiency(self):
        # A perfect page served as 404: the ONLY deficiency is the
        # status itself, evidenced by its code.
        page = sc.SiteFetch(status_code=404, url=GOOD_URL, html=GOOD_HTML)
        status, body = _serve(page, {"url": GOOD_URL})
        assert status == 200
        assert body["verdict"] == "build_recommended"
        assert [d["code"] for d in body["deficiencies"]] == [
            "site_error_status"]
        assert "404" in body["deficiencies"][0]["evidence"]

    def test_next_step_price_matches_rail_sku_table(self):
        import sincor2.rail_routes as rr
        assert rr.PACK_SKUS["webbuilder_site"]["price_cents"] == 49700
        step = sc._webbuilder_next_step()
        assert step["price_usdc"] == (
            rr.PACK_SKUS["webbuilder_site"]["price_cents"] / 100)


# ---------------------------------------------------------------------------
# Unit: payload validation + price registration
# ---------------------------------------------------------------------------

class TestPayloadAndRegistration:
    def test_neither_url_nor_name_400(self):
        status, body = _serve(GOOD_PAGE, {})
        assert status == 400
        assert body["error"] == "url_or_business_name_required"

    def test_bad_scheme_400(self):
        status, body = _serve(GOOD_PAGE, {"url": "ftp://x.example.com"})
        assert status == 400
        assert body["error"] == "url_invalid"

    def test_unknown_fields_400(self):
        status, body = _serve(GOOD_PAGE,
                              {"url": GOOD_URL, "guess": 1})
        assert status == 400
        assert body["error"] == "unknown_payload_fields"

    def test_non_object_payload_400(self):
        status, body = sc.serve_site_check(
            ["https://x.example.com"], fetch=lambda u: GOOD_PAGE)
        assert status == 400
        assert body["error"] == "payload_must_be_object"

    def test_price_registration(self):
        res = x402.get_resource(RESOURCE)
        assert res is not None
        assert res["token"] == "USDC"
        assert res["amount_usdc"] == 0.01
        # USDC has 6 decimals: $0.01 == 10000 atomic units.
        assert res["amount_atomic"] == "10000"
        ids = [r["id"] for r in x402.list_resources()]
        assert RESOURCE in ids


# ---------------------------------------------------------------------------
# SSRF hardening (audit P1-2): the buyer-supplied URL is attacker
# controlled. Forbidden targets are 400 invalid_target_host BEFORE any
# fetch, and redirect hops are re-validated before connecting.
# Hermetic: DNS is stubbed for the whole file (autouse fixture below);
# a test that needs a specific resolution installs its own stub,
# which overrides the fixture's.
# ---------------------------------------------------------------------------

_PUBLIC_IP = "93.184.216.34"


def _dns_stub(mapping=None, default=_PUBLIC_IP):
    """A socket.getaddrinfo replacement: hosts in ``mapping`` resolve
    to the mapped address (or fail when mapped to None); every other
    host resolves to ``default`` (one public address)."""
    mapping = mapping or {}

    def _resolve(host, port, *args, **kwargs):
        ip = mapping.get(host, default)
        if ip is None:
            raise socket.gaierror(f"stub DNS: no such host {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM,
                 socket.IPPROTO_TCP, "", (ip, port or 0))]

    return _resolve


@pytest.fixture(autouse=True)
def _hermetic_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _dns_stub())


class TestSSRFGuard:
    @pytest.mark.parametrize("bad_url", [
        "http://127.0.0.1",
        "http://127.0.0.1:8080/health",
        "http://localhost",
        "http://localhost:8080/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1",
        "http://192.168.1.1/router",
        "http://service.railway.internal",
        "https://example.com:8443",
        "http://example.com:22/",
    ])
    def test_forbidden_targets_400_before_any_fetch(self, bad_url):
        calls = []

        def _fetch(url):
            calls.append(url)
            return GOOD_PAGE

        status, body = sc.serve_site_check({"url": bad_url},
                                           fetch=_fetch)
        assert status == 400, body
        assert body["ok"] is False
        assert body["error"] == "invalid_target_host"
        assert "verdict" not in body
        # Rejected at validation: the fetch seam never ran.
        assert calls == []

    def test_hostname_resolving_to_private_ip_400(self, monkeypatch):
        monkeypatch.setattr(
            socket, "getaddrinfo",
            _dns_stub({"sneaky.example.com": "192.168.1.20"}))
        calls = []
        status, body = sc.serve_site_check(
            {"url": "https://sneaky.example.com/"},
            fetch=lambda u: calls.append(u) or GOOD_PAGE)
        assert status == 400, body
        assert body["error"] == "invalid_target_host"
        assert calls == []

    def test_validate_payload_rejects_metadata_endpoint(self):
        parsed, err = sc.validate_payload(
            {"url": "http://169.254.169.254/latest/meta-data/"})
        assert parsed is None
        assert err is not None
        assert err[0] == 400
        assert err[1]["error"] == "invalid_target_host"


class _FakeResponse:
    def __init__(self, status_code, url, text="", headers=None):
        self.status_code = status_code
        self.url = url
        self.text = text
        self.headers = headers or {}


class TestSSRFRedirects:
    def test_fetcher_raises_on_forbidden_target_without_transport(
            self, monkeypatch):
        def _fake_get(url, **kwargs):
            raise AssertionError("transport must not run for a "
                                 "forbidden target")
        monkeypatch.setattr("requests.get", _fake_get)
        with pytest.raises(sc.InvalidTargetHostError):
            sc.fetch_site_page("http://169.254.169.254/")

    def test_redirect_to_loopback_rejected_before_connect(
            self, monkeypatch):
        calls = []

        def _fake_get(url, **kwargs):
            calls.append(url)
            # Redirects must be handled manually, never auto-followed.
            assert kwargs.get("allow_redirects") is False
            return _FakeResponse(302, url, headers={
                "Location": "http://127.0.0.1:8080/health"})

        monkeypatch.setattr("requests.get", _fake_get)
        status, body = sc.serve_site_check(
            {"url": "https://shop.example.com/"})
        assert status == 400, body
        assert body["ok"] is False
        assert body["error"] == "invalid_target_host"
        assert "verdict" not in body
        # The transport was invoked exactly once — with the public
        # URL — and never with the forbidden redirect target.
        assert calls == ["https://shop.example.com/"]

    def test_redirect_to_public_target_is_followed(self, monkeypatch):
        pages = {
            "https://shop.example.com/": _FakeResponse(
                302, "https://shop.example.com/",
                headers={"Location": "/landing"}),
            "https://shop.example.com/landing": _FakeResponse(
                200, "https://shop.example.com/landing",
                text=GOOD_HTML),
        }
        calls = []

        def _fake_get(url, **kwargs):
            calls.append(url)
            assert kwargs.get("allow_redirects") is False
            return pages[url]

        monkeypatch.setattr("requests.get", _fake_get)
        status, body = sc.serve_site_check(
            {"url": "https://shop.example.com/"})
        assert status == 200, body
        assert body["verdict"] == "no_build_needed"
        assert body["observations"]["final_url"] == \
            "https://shop.example.com/landing"
        assert calls == ["https://shop.example.com/",
                         "https://shop.example.com/landing"]


# ---------------------------------------------------------------------------
# Integration: full paid loop through the real routes
# ---------------------------------------------------------------------------

def _make_key() -> Account:
    from sincor2.sig_canonical import is_low_s
    while True:
        acct = Account.create()
        probe = acct.sign_message(encode_defunct(text="sincor-low-s-probe"))
        if is_low_s(probe.signature):
            return acct


BUYER_KEY = _make_key()
BUYER = BUYER_KEY.address


@pytest.fixture()
def x402_db(tmp_path, monkeypatch):
    db = tmp_path / "orders.db"
    monkeypatch.setenv("ORDERS_DB_PATH", str(db))
    x402.init_x402_db()
    return db


@pytest.fixture()
def paid_amount():
    return {"atomic": None}  # None = pay exactly what the server asks


@pytest.fixture()
def mock_onchain(monkeypatch, paid_amount):
    def _verify(tx_hash, **kwargs):
        expected = int(kwargs["expected_atomic"])
        paid = expected if paid_amount["atomic"] is None \
            else paid_amount["atomic"]
        supplied = kwargs.get("payer_wallet") or ""
        if paid < expected * 9995 // 10000:  # mirror AMOUNT_TOLERANCE_BPS
            return {"ok": False, "error": "insufficient_amount",
                    "paid_display": paid / 1e6,
                    "required_display": expected / 1e6, "token": "USDC"}
        # The real verifier reports the on-chain sender of the
        # transfer; in this hermetic world that is the wallet the
        # caller claims paid (BUYER on the billing loop, the rail
        # payer on the rail leg of the flagged-lead test).
        return {"ok": True, "payer_wallet": supplied or BUYER}

    monkeypatch.setattr(x402, "verify_treasury_transfer", _verify)
    monkeypatch.setattr(
        x402, "record_platform_payment",
        lambda **kw: {"payment_id": kw.get("payment_id"), "ok": True})
    monkeypatch.setattr(
        x402, "record_inflow", lambda *a, **k: {"ok": True})


@pytest.fixture()
def fetch_stub(monkeypatch):
    """Stub the module's production fetch seam with the deficient
    fixture (mutable so a test can swap in the good page)."""
    state = {"page": DEFICIENT_PAGE}
    monkeypatch.setattr(sc, "fetch_site_page",
                        lambda url: state["page"])
    return state


@pytest.fixture()
def client():
    app = Flask(__name__)
    app.register_blueprint(billing.bp)
    app.config.update(TESTING=True)
    return app.test_client()


def _tx(seed: str) -> str:
    return "0x" + seed * 32


def _pay(client, tx_hash: str) -> dict:
    """Full W-102r3 flow: 402 challenge -> bind -> verify/settle."""
    r = client.get(f"/x402/{RESOURCE}?wallet={BUYER}")
    assert r.status_code == 402, r.get_json()
    ch = r.get_json()
    # Price is server-derived: the 402 demands exactly $0.01 USDC.
    assert ch["payment"]["maxAmountRequired"] == "10000", ch
    sig = BUYER_KEY.sign_message(
        encode_defunct(text=ch["sign_message"])).signature.hex()
    r = client.post("/api/x402/challenge-bind", json={
        "challenge_id": ch["challenge_id"],
        "payer_wallet": BUYER,
        "signature": sig,
    })
    assert r.status_code == 402, r.get_json()
    assert r.get_json()["error"] == "payment_required"
    r = client.post("/api/x402/verify", json={
        "challenge_id": ch["challenge_id"],
        "tx_hash": tx_hash,
        "payer_wallet": BUYER,
    })
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["ok"] is True and body["status"] == "fulfilled"
    assert body["access_token"]
    return body


def _serve_paid(client, access_token: str, payload: dict):
    return client.post(
        f"/api/paid/{RESOURCE}",
        headers={"X-Payment-Token": access_token},
        json=payload,
    )


class TestFullLoop:
    def test_paid_deficient_site_delivers_build_recommended(
            self, client, x402_db, mock_onchain, fetch_stub):
        paid = _pay(client, _tx("d1"))
        r = _serve_paid(client, paid["access_token"],
                        {"url": DEFICIENT_URL,
                         "business_name": "Old Site Co"})
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["verdict"] == "build_recommended"
        assert body["next_step"]["price_usdc"] == 497.0
        codes = {d["code"] for d in body["deficiencies"]}
        assert "missing_viewport" in codes

    def test_paid_good_site_delivers_no_build_needed_no_upsell(
            self, client, x402_db, mock_onchain, fetch_stub):
        fetch_stub["page"] = GOOD_PAGE
        paid = _pay(client, _tx("d2"))
        r = _serve_paid(client, paid["access_token"], {"url": GOOD_URL})
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["verdict"] == "no_build_needed"
        assert "next_step" not in body

    def test_paid_unreachable_delivers_unknown(
            self, client, x402_db, mock_onchain, fetch_stub):
        fetch_stub["page"] = None
        paid = _pay(client, _tx("d3"))
        r = _serve_paid(client, paid["access_token"], {"url": GOOD_URL})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["verdict"] == "unknown"

    def test_paid_bad_payload_400_token_not_consumed(
            self, client, x402_db, mock_onchain, fetch_stub):
        # P1-1: a 400 validation error must NOT burn the single-use
        # token — the same token delivers once the payload is valid.
        paid = _pay(client, _tx("d9"))
        r = _serve_paid(client, paid["access_token"], {})
        assert r.status_code == 400, r.get_json()
        r2 = _serve_paid(client, paid["access_token"],
                         {"url": DEFICIENT_URL})
        assert r2.status_code == 200, r2.get_json()
        assert r2.get_json()["verdict"] == "build_recommended"

    def test_unpaid_serve_rejected(
            self, client, x402_db, mock_onchain, fetch_stub):
        r = client.post(f"/api/paid/{RESOURCE}?wallet={BUYER}",
                        json={"url": DEFICIENT_URL})
        assert r.status_code == 402
        assert "verdict" not in (r.get_json() or {})

    def test_tx_hash_replay_rejected(
            self, client, x402_db, mock_onchain, fetch_stub):
        tx = _tx("d4")
        _pay(client, tx)
        draft = x402.create_challenge(RESOURCE)
        msg = x402.challenge_bind_message(
            draft["challenge_id"], RESOURCE, BUYER)
        sig = BUYER_KEY.sign_message(
            encode_defunct(text=msg)).signature.hex()
        bound = x402.bind_challenge_payer(
            draft["challenge_id"], BUYER, sig)
        assert bound.get("ownership_proven") is True, bound
        r = client.post("/api/x402/verify", json={
            "challenge_id": draft["challenge_id"],
            "tx_hash": tx,
            "payer_wallet": BUYER,
        })
        assert r.status_code == 400, r.get_json()
        assert r.get_json()["error"] == "tx_hash_already_used"

    def test_access_token_single_use(
            self, client, x402_db, mock_onchain, fetch_stub):
        paid = _pay(client, _tx("d5"))
        r1 = _serve_paid(client, paid["access_token"],
                         {"url": DEFICIENT_URL})
        assert r1.status_code == 200
        r2 = _serve_paid(client, paid["access_token"],
                         {"url": DEFICIENT_URL})
        assert r2.status_code != 200
        assert "verdict" not in (r2.get_json() or {})

    def test_unbound_challenge_cannot_settle(
            self, client, x402_db, mock_onchain, fetch_stub):
        draft = x402.create_challenge(RESOURCE)
        r = client.post("/api/x402/verify", json={
            "challenge_id": draft["challenge_id"],
            "tx_hash": _tx("d6"),
            "payer_wallet": BUYER,
        })
        assert r.status_code == 400, r.get_json()
        assert r.get_json()["error"] == "challenge_unbound"

    def test_underpayment_rejected(
            self, client, x402_db, mock_onchain, fetch_stub, paid_amount):
        paid_amount["atomic"] = 100  # $0.0001 against a $0.01 price
        r = client.get(f"/x402/{RESOURCE}?wallet={BUYER}")
        ch = r.get_json()
        sig = BUYER_KEY.sign_message(
            encode_defunct(text=ch["sign_message"])).signature.hex()
        client.post("/api/x402/challenge-bind", json={
            "challenge_id": ch["challenge_id"],
            "payer_wallet": BUYER,
            "signature": sig,
        })
        r = client.post("/api/x402/verify", json={
            "challenge_id": ch["challenge_id"],
            "tx_hash": _tx("d7"),
            "payer_wallet": BUYER,
        })
        assert r.status_code == 402, r.get_json()
        assert r.get_json()["error"] == "insufficient_amount"


# ---------------------------------------------------------------------------
# Upsell path: a flagged lead completes the REAL rail webbuilder purchase
# (fixtures ported from test_webbuilder_sku.py — stubbed chain receipt,
# zero network, zero keys, nothing broadcast)
# ---------------------------------------------------------------------------

import sincor2.platform_payments as pp
import sincor2.rail_routes as rr
from sincor2 import payment_ledger as pl
from sincor2.governance.money_gate import reset_money_boundary

RAIL_TX_HASH = "0x" + "ab" * 32
# Hunt-29: rail settle requires a BOUND challenge, so the rail payer
# must be a real account that can sign the EIP-191 bind message.
# Well-known PUBLIC Hardhat dev account #0 — test-only, never funded.
RAIL_PAYER_ACCT = Account.from_key(
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
RAIL_PAYER = RAIL_PAYER_ACCT.address
AMOUNT_ATOMIC_497 = 497_000_000  # $497.00 in 6-decimal USDC


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
    monkeypatch.setenv("WEBBUILDER_DATA_DIR", str(tmp_path / "webbuilder"))
    monkeypatch.setattr(
        "sincor2.treasury_inflow._LEDGER_PATH", tmp_path / "inflow.jsonl")
    monkeypatch.setattr("sincor2.agent_billing._log_path",
                        lambda: tmp_path / "platform_payments.jsonl")

    treasury = x402.get_resource("rail-pack-webbuilder_site")["treasury"]
    assert treasury, "rail-pack-webbuilder_site must be configured"
    token_addr = pp.token_address("USDC")

    def _receipt():
        return {
            "status": "0x1",
            "blockNumber": hex(100),
            "logs": [{
                "address": token_addr,
                "topics": [
                    pp.TRANSFER_TOPIC,
                    "0x" + "0" * 24 + RAIL_PAYER[2:],
                    "0x" + "0" * 24 + treasury[2:].lower(),
                ],
                "data": hex(AMOUNT_ATOMIC_497),
            }],
        }

    def _fake_rpc(method, params, attempts=3):
        if method == "eth_getTransactionReceipt":
            assert params == [RAIL_TX_HASH], params
            return _receipt()
        if method == "eth_blockNumber":
            return hex(112)  # 13 confirmations — past finality depth
        raise AssertionError(f"unexpected RPC method {method}")

    monkeypatch.setattr(pp, "_rpc_call", _fake_rpc)
    x402.init_x402_db()
    return {"tx_hash": RAIL_TX_HASH, "payer": RAIL_PAYER,
            "treasury": treasury, "tmp": tmp_path}


@pytest.fixture()
def mock_comms(monkeypatch):
    sent = []
    queued = []

    def fake_send_email(self, to_email, to_name="", subject="",
                        html_content="", text_content=None, metadata=None):
        sent.append({"to": to_email, "to_name": to_name,
                     "subject": subject, "html": html_content,
                     "text": text_content, "metadata": metadata or {}})
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


def test_flagged_lead_completes_rail_webbuilder_purchase(
        client, x402_db, mock_onchain, fetch_stub, rail_env, mock_comms):
    """Spec path (§4 item 6): pay $0.01 for the check -> the lead is
    flagged build_recommended -> follow the response's own next_step
    into the real /rail/checkout + /rail/settle for the $497
    webbuilder pack -> exactly one WebBuilder project is delivered
    for that business."""
    # 1. The paid check flags the lead.
    paid = _pay(client, _tx("d8"))
    r = _serve_paid(client, paid["access_token"],
                    {"url": DEFICIENT_URL,
                     "business_name": "Old Site Co"})
    assert r.status_code == 200, r.get_json()
    verdict = r.get_json()
    assert verdict["verdict"] == "build_recommended"
    step = verdict["next_step"]
    assert step["price_usdc"] == 497.0

    # 2. The flagged lead buys the pack the check pointed at, through
    # the real rail routes (SKU taken from the check's next_step).
    app = Flask(__name__)
    app.config["TESTING"] = True
    rr.init_rail_routes(app)
    rail = app.test_client()

    res = rail.post("/rail/checkout", json={
        "pack_sku": step["sku"],
        "payer_wallet": RAIL_PAYER,
        "buyer_email": "buyer@example.com",
        "buyer_name": "Test Buyer",
    })
    assert res.status_code == 402, res.get_json()
    checkout = res.get_json()
    assert checkout["resource_id"] == step["resource"]
    assert checkout["amount_usdc"] == step["price_usdc"]
    cid = checkout["challenge_id"]

    # Hunt-29: bind the challenge to the payer (EIP-191 ownership
    # proof) before settling — the step the rail pay page performs.
    bind_sig = RAIL_PAYER_ACCT.sign_message(encode_defunct(
        text=x402.challenge_bind_message(
            cid, checkout["resource_id"], RAIL_PAYER))).signature.hex()
    bound = x402.bind_challenge_payer(cid, RAIL_PAYER, bind_sig)
    assert bound.get("ownership_proven") is True, bound

    res = rail.post("/rail/settle", json={
        "challenge_id": cid,
        "tx_hash": RAIL_TX_HASH,
        "payer_wallet": RAIL_PAYER,
        "buyer_email": "buyer@example.com",
        "buyer_name": "Test Buyer",
        "business_name": "Old Site Co",
        "phone": "(555) 123-4567",
        "niche": "dental",
        "territory": "Clinton, IA",
    })
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["ok"] is True
    assert body["status"] == "fulfilled"
    assert body["fulfillment"]["status"] == "delivered"

    # 3. Exactly one WebBuilder project was built for the flagged
    # business, and exactly one rail event / intake exists.
    from sincor2 import webbuilder_studio as wb
    projects = wb.list_projects()
    assert len(projects) == 1, projects
    assert projects[0]["name"] == "Old Site Co"
    html = wb.get_site_html(projects[0]["slug"], "draft")
    assert "Old Site Co" in html

    conn = sqlite3.connect(rail_env["tmp"] / "revenue.db")
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM rail_events").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM pack_intakes").fetchone()[0] == 1
    finally:
        conn.close()
    assert len(mock_comms["sent"]) == 1
    assert mock_comms["queued"] == []
    assert pl.list_alerts(rail_env["tmp"] / "revenue.db") == []
