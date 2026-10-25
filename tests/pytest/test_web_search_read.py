"""Web Search + Page Read (G5 / gap-map #8) — unit + full-loop tests.

Unit layer proves the product's honesty + margin rules against
fixture results injected through the module's provider seam
(``serve_web_search_read(..., search=... / read=...)`` — the same
seams the production Exa adapters fill). No live network, no API
keys anywhere in the unit layer: the repo has NO search provider
on main (Scout wraps keyed Places/Yelp + a keyless page scrape),
so production search/read run behind the seam and fail closed
with an honest 503 when ``EXA_API_KEY`` is unset — that failure is
itself tested below with the real adapter and no seam stub.

Margin gate (gap-map §4 item 8): the documented Exa cost model
(search $0.007/call, contents $0.001/page — published pricing)
must sit below the real $0.01 price, and the guard must REFUSE
(503 margin_guard) when modeled cost reaches the price. Both
directions are proven.

Integration layer proves the complete first-dollar loop through
the real Flask routes (same mock pattern as
test_site_check.py): 402 -> EIP-191 bound challenge ->
verify/settle -> single-use token -> results delivered ->
tx_hash replay rejected -> token replay rejected -> unbound
challenge rejected -> underpayment rejected.
"""
from __future__ import annotations

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

# Production import order matters: mvp_app must be imported before the
# billing blueprint (see test_x402_verify_frontrun.py).
import sincor2.mvp_app  # noqa: F401  (imported for binding side effects)
from sincor2.mvp_blueprints import billing

import sincor2.web_search_read as ws
import sincor2.x402_payments as x402

RESOURCE = "web-search-read"

SEARCH_FIXTURE = [
    {"title": "Base — L2 for Ethereum",
     "url": "https://base.org/",
     "text": "Base is a secure, low-cost, builder-friendly Ethereum L2.",
     "source": "base.org"},
    {"title": "x402 Protocol",
     "url": "https://x402.org/",
     "highlights": ["x402 is an open payment protocol over HTTP."]},
    {"title": "", "url": "", "text": "no URL — must be dropped"},
]

LONG_TEXT = "alpha beta gamma delta " * 1250  # 30,000 chars
READ_FIXTURE = {"url": "https://example.com/article",
                "title": "A Long Article",
                "text": LONG_TEXT}


def _search_ok(query):
    return [dict(r) for r in SEARCH_FIXTURE]


def _read_ok(url):
    return dict(READ_FIXTURE)


# ---------------------------------------------------------------------------
# Unit: search — sourced, timestamped, honest
# ---------------------------------------------------------------------------

class TestSearchServe:
    def test_search_fixture_served_with_sources_and_timestamps(self):
        status, body = ws.serve_web_search_read(
            {"mode": "search", "query": "what is x402"},
            search=_search_ok)
        assert status == 200
        assert body["ok"] is True
        assert body["mode"] == "search"
        assert body["query"] == "what is x402"
        # The URL-less fixture row is dropped, never served.
        assert body["count"] == 2
        assert len(body["results"]) == 2
        first, second = body["results"]
        assert first["title"] == "Base — L2 for Ethereum"
        assert first["url"] == "https://base.org/"
        assert first["snippet"].startswith("Base is a secure")
        assert first["source"] == "base.org"
        # Source falls back to the result's own host when the
        # provider supplied none; snippet falls back to highlights.
        assert second["source"] == "x402.org"
        assert second["snippet"] == "x402 is an open payment protocol over HTTP."
        # EVERY item is timestamped, and the stamps match the
        # response's retrieved_at.
        assert body["retrieved_at"]
        for item in body["results"]:
            assert item["retrieved_at"] == body["retrieved_at"]

    def test_search_empty_results_honest(self):
        status, body = ws.serve_web_search_read(
            {"mode": "search", "query": "nothing matches this"},
            search=lambda q: [])
        assert status == 200
        assert body["results"] == []
        assert body["count"] == 0
        assert body["retrieved_at"]

    def test_search_response_carries_cost_accounting(self):
        status, body = ws.serve_web_search_read(
            {"mode": "search", "query": "q"}, search=_search_ok)
        assert status == 200
        assert body["provider"] == "exa"
        assert body["price_usd"] == 0.01
        assert body["provider_cost_usd"] == 0.007
        assert body["margin_usd"] == pytest.approx(0.003)


# ---------------------------------------------------------------------------
# Unit: read — bounded extraction, cap stated
# ---------------------------------------------------------------------------

class TestReadServe:
    def test_read_fixture_bounded_extraction(self):
        status, body = ws.serve_web_search_read(
            {"mode": "read", "url": "https://example.com/article"},
            read=_read_ok)
        assert status == 200
        assert body["mode"] == "read"
        assert body["url"] == "https://example.com/article"
        assert body["title"] == "A Long Article"
        # Bounded: exactly the cap, with the cap + full size stated.
        assert len(body["text"]) == ws.MAX_TEXT_CHARS == 20_000
        assert body["text"] == LONG_TEXT[:20_000]
        assert body["text_chars"] == len(LONG_TEXT)
        assert body["truncated"] is True
        assert body["text_cap_chars"] == 20_000
        assert body["source"] == "example.com"
        assert body["retrieved_at"]
        assert body["provider_cost_usd"] == 0.001
        assert body["margin_usd"] == pytest.approx(0.009)

    def test_read_short_text_not_truncated(self):
        page = {"url": "https://example.com/short", "title": "Short",
                "text": "hello world"}
        status, body = ws.serve_web_search_read(
            {"mode": "read", "url": "https://example.com/short"},
            read=lambda u: page)
        assert status == 200
        assert body["text"] == "hello world"
        assert body["text_chars"] == 11
        assert body["truncated"] is False


# ---------------------------------------------------------------------------
# Unit: provider failures are honest 503s, never fake results
# ---------------------------------------------------------------------------

class TestProviderFailures:
    def test_search_provider_not_configured_503(self):
        def _unconfigured(query):
            raise ws.ProviderNotConfigured("EXA_API_KEY is not set")
        status, body = ws.serve_web_search_read(
            {"mode": "search", "query": "q"}, search=_unconfigured)
        assert status == 503
        assert body["ok"] is False
        assert body["error"] == "provider_not_configured"
        assert "results" not in body

    def test_read_provider_not_configured_503(self):
        def _unconfigured(url):
            raise ws.ProviderNotConfigured("EXA_API_KEY is not set")
        status, body = ws.serve_web_search_read(
            {"mode": "read", "url": "https://example.com/"},
            read=_unconfigured)
        assert status == 503
        assert body["error"] == "provider_not_configured"
        assert "text" not in body

    def test_real_adapter_fails_closed_without_key(self, monkeypatch):
        """No seam stub, no env key: the production Exa adapter
        itself must fail closed — this is the launch-state honesty
        test for a deployment that has not configured a provider."""
        monkeypatch.delenv("EXA_API_KEY", raising=False)
        with pytest.raises(ws.ProviderNotConfigured):
            ws.search_provider("anything")
        with pytest.raises(ws.ProviderNotConfigured):
            ws.read_provider("https://example.com/")
        status, body = ws.serve_web_search_read(
            {"mode": "search", "query": "q"})
        assert status == 503
        assert body["error"] == "provider_not_configured"

    def test_provider_transport_failure_503_not_empty(self):
        def _down(query):
            raise RuntimeError("connection reset")
        status, body = ws.serve_web_search_read(
            {"mode": "search", "query": "q"}, search=_down)
        assert status == 503
        assert body["error"] == "provider_unavailable"
        assert "results" not in body


# ---------------------------------------------------------------------------
# Unit: the margin gate — both directions
# ---------------------------------------------------------------------------

class TestMarginGuard:
    def test_documented_costs_pass_at_real_price(self):
        # The launch figures: Exa search $0.007 and contents
        # $0.001 against the real $0.01 price — positive margin.
        assert ws.per_call_cost_usd("search") == 0.007
        assert ws.per_call_cost_usd("read") == 0.001
        assert ws.margin_guard_tripped("search") is False
        assert ws.margin_guard_tripped("read") is False
        assert ws.PRICE_USD == 0.01

    def test_margin_guard_fires_when_search_cost_reaches_price(self, monkeypatch):
        monkeypatch.setattr(ws, "SEARCH_COST_USD", 0.01)  # cost == price
        status, body = ws.serve_web_search_read(
            {"mode": "search", "query": "q"}, search=_search_ok)
        assert status == 503
        assert body["error"] == "margin_guard"
        assert body["price_usd"] == 0.01
        assert body["provider_cost_usd"] == 0.01

    def test_margin_guard_fires_when_read_cost_exceeds_price(self, monkeypatch):
        monkeypatch.setattr(ws, "READ_COST_USD", 0.015)  # cost > price
        status, body = ws.serve_web_search_read(
            {"mode": "read", "url": "https://example.com/article"},
            read=_read_ok)
        assert status == 503
        assert body["error"] == "margin_guard"

    def test_margin_guard_fires_before_provider_spend(self, monkeypatch):
        monkeypatch.setattr(ws, "SEARCH_COST_USD", 0.02)

        def _boom(query):
            raise AssertionError("provider must not be called "
                                 "when the margin guard trips")
        status, body = ws.serve_web_search_read(
            {"mode": "search", "query": "q"}, search=_boom)
        assert status == 503
        assert body["error"] == "margin_guard"


# ---------------------------------------------------------------------------
# Unit: payload validation + price registration
# ---------------------------------------------------------------------------

class TestPayloadAndRegistration:
    def test_mode_required(self):
        status, body = ws.serve_web_search_read(
            {"query": "q"}, search=_search_ok)
        assert status == 400 and body["error"] == "mode_invalid"

    def test_unknown_mode_400(self):
        status, body = ws.serve_web_search_read(
            {"mode": "crawl", "query": "q"}, search=_search_ok)
        assert status == 400 and body["error"] == "mode_invalid"

    def test_search_query_required(self):
        status, body = ws.serve_web_search_read(
            {"mode": "search"}, search=_search_ok)
        assert status == 400 and body["error"] == "query_required"

    def test_read_url_required(self):
        status, body = ws.serve_web_search_read(
            {"mode": "read"}, read=_read_ok)
        assert status == 400 and body["error"] == "url_required"

    def test_read_bad_scheme_400(self):
        status, body = ws.serve_web_search_read(
            {"mode": "read", "url": "ftp://x.example.com"}, read=_read_ok)
        assert status == 400 and body["error"] == "url_invalid"

    def test_unknown_fields_400(self):
        status, body = ws.serve_web_search_read(
            {"mode": "search", "query": "q", "depth": 3},
            search=_search_ok)
        assert status == 400 and body["error"] == "unknown_payload_fields"

    def test_non_object_payload_400(self):
        status, body = ws.serve_web_search_read(
            ["search", "q"], search=_search_ok)
        assert status == 400 and body["error"] == "payload_must_be_object"

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
        return {"ok": True, "payer_wallet": supplied or BUYER}

    monkeypatch.setattr(x402, "verify_treasury_transfer", _verify)
    monkeypatch.setattr(
        x402, "record_platform_payment",
        lambda **kw: {"payment_id": kw.get("payment_id"), "ok": True})
    monkeypatch.setattr(
        x402, "record_inflow", lambda *a, **k: {"ok": True})


@pytest.fixture()
def provider_stub(monkeypatch):
    """Stub BOTH provider seams with fixtures (mutable state so a
    test can switch the provider into not-configured mode)."""
    state = {"mode": "ok"}

    def _search(query):
        if state["mode"] == "not_configured":
            raise ws.ProviderNotConfigured("EXA_API_KEY is not set")
        return [dict(r) for r in SEARCH_FIXTURE]

    def _read(url):
        if state["mode"] == "not_configured":
            raise ws.ProviderNotConfigured("EXA_API_KEY is not set")
        return dict(READ_FIXTURE)

    monkeypatch.setattr(ws, "search_provider", _search)
    monkeypatch.setattr(ws, "read_provider", _read)
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


def _serve_paid(client, tok, payload):
    return client.post(
        f"/api/paid/{RESOURCE}",
        headers={"X-Payment-Token": tok},
        json=payload,
    )


class TestFullLoop:
    def test_paid_search_delivers_sourced_timestamped_results(
            self, client, x402_db, mock_onchain, provider_stub):
        paid = _pay(client, _tx("e1"))
        r = _serve_paid(client, paid["access_token"],
                        {"mode": "search", "query": "what is x402"})
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["count"] == 2
        assert body["results"][0]["source"] == "base.org"
        assert body["results"][0]["retrieved_at"] == body["retrieved_at"]
        assert body["margin_usd"] == pytest.approx(0.003)

    def test_paid_read_delivers_bounded_text(
            self, client, x402_db, mock_onchain, provider_stub):
        paid = _pay(client, _tx("e2"))
        r = _serve_paid(client, paid["access_token"],
                        {"mode": "read",
                         "url": "https://example.com/article"})
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert len(body["text"]) == 20_000
        assert body["truncated"] is True

    def test_paid_call_with_unconfigured_provider_503_honest(
            self, client, x402_db, mock_onchain, provider_stub):
        provider_stub["mode"] = "not_configured"
        paid = _pay(client, _tx("e3"))
        r = _serve_paid(client, paid["access_token"],
                        {"mode": "search", "query": "q"})
        assert r.status_code == 503, r.get_json()
        assert r.get_json()["error"] == "provider_not_configured"
        assert "results" not in (r.get_json() or {})
        # P1-1: the 503 must NOT burn the single-use token — with
        # the provider configured, the SAME token delivers.
        provider_stub["mode"] = "ok"
        r2 = _serve_paid(client, paid["access_token"],
                         {"mode": "search", "query": "q"})
        assert r2.status_code == 200, r2.get_json()
        assert r2.get_json()["count"] == 2

    def test_unpaid_serve_rejected(
            self, client, x402_db, mock_onchain, provider_stub):
        r = client.post(f"/api/paid/{RESOURCE}?wallet={BUYER}",
                        json={"mode": "search", "query": "q"})
        assert r.status_code == 402
        assert "results" not in (r.get_json() or {})

    def test_tx_hash_replay_rejected(
            self, client, x402_db, mock_onchain, provider_stub):
        tx = _tx("e4")
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
            self, client, x402_db, mock_onchain, provider_stub):
        paid = _pay(client, _tx("e5"))
        r1 = _serve_paid(client, paid["access_token"],
                         {"mode": "search", "query": "q"})
        assert r1.status_code == 200
        r2 = _serve_paid(client, paid["access_token"],
                         {"mode": "search", "query": "q"})
        assert r2.status_code != 200
        assert "results" not in (r2.get_json() or {})

    def test_unbound_challenge_cannot_settle(
            self, client, x402_db, mock_onchain, provider_stub):
        draft = x402.create_challenge(RESOURCE)
        r = client.post("/api/x402/verify", json={
            "challenge_id": draft["challenge_id"],
            "tx_hash": _tx("e6"),
            "payer_wallet": BUYER,
        })
        assert r.status_code == 400, r.get_json()
        assert r.get_json()["error"] == "challenge_unbound"

    def test_underpayment_rejected(
            self, client, x402_db, mock_onchain, provider_stub,
            paid_amount):
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
            "tx_hash": _tx("e7"),
            "payer_wallet": BUYER,
        })
        assert r.status_code == 402, r.get_json()
        assert r.get_json()["error"] == "insufficient_amount"
