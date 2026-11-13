"""Market-Data Single Read (G6) — unit + full-loop x402 tests.

Unit layer proves the served value is ROUND-MATCHED to the real
Chainlink machinery's data shape: fixture rounds are real
``sincor2.defi.price_oracle.ChainlinkRound`` values injected through
the module's feed seam (the same ``read_round(asset)`` seam
``ChainlinkFeedAdapter`` / ``EthCallChainlinkReader`` use in
production). No live network anywhere.

Hand-computed expectations:
- ETH/USD fresh round: answer 324_150_000_000 at 8 decimals
  ($3,241.50). price_fp = answer * 10**8 // 10**8 = 324_150_000_000,
  rendered exactly as "3241.5".
- USDC/USD fresh round: answer 100_000_000 -> price_fp 100_000_000,
  rendered "1".
- Staleness threshold is the module's stated STALE_AFTER_S (3600s):
  a round 7200s old is stale; a superseded round
  (answered_in_round < round_id) is incomplete AND stale, never
  served as fresh.

Supported pairs are exactly the repo-pinned BASE_MAINNET_FEEDS keys
(ETH/USD, USDC/USD today); BTC/USD is deliberately unpinned in the
repo and must be an unknown pair here too. OHLCV/trending modes are
honestly unavailable (no OHLCV source in the repo) and rejected.

Integration layer proves the complete first-dollar loop through the
real Flask routes (same mock pattern as test_kya_trust_score.py):
402 -> EIP-191 bound challenge -> verify/settle -> single-use token
-> read delivered -> stateless second paid call -> tx_hash replay
rejected -> token replay rejected -> unbound challenge rejected ->
underpayment rejected.
"""
from __future__ import annotations

import time

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

# Production import order matters: mvp_app must be imported before the
# billing blueprint (see test_x402_verify_frontrun.py).
import sincor2.mvp_app  # noqa: F401  (imported for binding side effects)
from sincor2.mvp_blueprints import billing

import sincor2.market_data_read as mdr
import sincor2.x402_payments as x402
from sincor2.defi.price_oracle import BASE_MAINNET_FEEDS, ChainlinkRound
from sincor2.sig_canonical import is_low_s

RESOURCE = "market-data-read"
NOW = 1_800_000_000

FRESH_ETH = ChainlinkRound(
    round_id=12345, answer=324_150_000_000, decimals=8,
    started_at=NOW - 100, updated_at=NOW - 120, answered_in_round=12345)
STALE_ETH = ChainlinkRound(
    round_id=12300, answer=310_000_000_000, decimals=8,
    started_at=NOW - 7300, updated_at=NOW - 7200, answered_in_round=12300)
INCOMPLETE_ETH = ChainlinkRound(
    round_id=12346, answer=324_200_000_000, decimals=8,
    started_at=NOW - 50, updated_at=NOW - 60, answered_in_round=12340)
FRESH_USDC = ChainlinkRound(
    round_id=777, answer=100_000_000, decimals=8,
    started_at=NOW - 30, updated_at=NOW - 30, answered_in_round=777)


def _serve(round_or_none, payload=None, now=NOW):
    return mdr.serve_market_data_read(
        payload if payload is not None else {"pair": "ETH/USD",
                                             "mode": "price"},
        read_round=lambda pair: round_or_none, now=now)


# ---------------------------------------------------------------------------
# Unit: round-matched values + provenance
# ---------------------------------------------------------------------------

class TestRoundMatchedPrice:
    def test_fresh_round_served_with_exact_value_and_provenance(self):
        status, body = _serve(FRESH_ETH)
        assert status == 200
        assert body["ok"] is True
        assert body["pair"] == "ETH/USD"
        assert body["mode"] == "price"
        assert body["source"] == "chainlink"
        # Value equals the fixture round EXACTLY (no float drift).
        assert body["answer"] == 324_150_000_000
        assert body["price_fp"] == 324_150_000_000
        assert body["price"] == "3241.5"
        assert body["decimals"] == 8
        # Provenance is the round's own fields, verbatim.
        assert body["round_id"] == 12345
        assert body["answered_in_round"] == 12345
        assert body["started_at"] == NOW - 100
        assert body["updated_at"] == NOW - 120
        assert body["feed_address"] == BASE_MAINNET_FEEDS["ETH/USD"]
        assert body["age_s"] == 120
        assert body["stale"] is False
        assert body["incomplete"] is False
        assert body["stale_after_s"] == mdr.STALE_AFTER_S == 3600

    def test_usdc_round_renders_exactly_one(self):
        status, body = _serve(FRESH_USDC, {"pair": "USDC/USD"})
        assert status == 200
        assert body["price_fp"] == 100_000_000
        assert body["price"] == "1"
        assert body["round_id"] == 777

    def test_pair_is_case_insensitive(self):
        status, body = _serve(FRESH_ETH, {"pair": "eth/usd"})
        assert status == 200
        assert body["pair"] == "ETH/USD"

    def test_mode_defaults_to_price(self):
        status, body = _serve(FRESH_ETH, {"pair": "ETH/USD"})
        assert status == 200
        assert body["mode"] == "price"


# ---------------------------------------------------------------------------
# Unit: staleness / completeness honesty
# ---------------------------------------------------------------------------

class TestStalenessHonesty:
    def test_stale_round_flagged_not_served_as_fresh(self):
        status, body = _serve(STALE_ETH)
        assert status == 200  # real data, honestly labelled
        assert body["stale"] is True
        assert body["incomplete"] is False
        assert body["age_s"] == 7200
        assert body["price_fp"] == 310_000_000_000

    def test_round_at_threshold_boundary_is_not_stale(self):
        edge = ChainlinkRound(
            round_id=1, answer=100_000_000, decimals=8,
            started_at=NOW - 3600, updated_at=NOW - 3600,
            answered_in_round=1)
        status, body = _serve(edge, {"pair": "USDC/USD"})
        assert status == 200
        assert body["stale"] is False

    def test_incomplete_round_flagged_never_fresh(self):
        status, body = _serve(INCOMPLETE_ETH)
        assert status == 200
        assert body["incomplete"] is True
        assert body["stale"] is True  # incomplete is never fresh
        assert body["round_id"] == 12346
        assert body["answered_in_round"] == 12340

    def test_no_round_is_503_never_a_guess(self):
        status, body = _serve(None)
        assert status == 503
        assert body["error"] == "feed_unavailable"
        assert "price" not in body

    def test_feed_exception_is_503(self):
        def _boom(pair):
            raise RuntimeError("rpc down")
        status, body = mdr.serve_market_data_read(
            {"pair": "ETH/USD"}, read_round=_boom, now=NOW)
        assert status == 503
        assert body["error"] == "feed_unavailable"

    def test_non_positive_answer_is_502(self):
        bad = ChainlinkRound(
            round_id=5, answer=0, decimals=8, started_at=NOW - 10,
            updated_at=NOW - 10, answered_in_round=5)
        status, body = _serve(bad)
        assert status == 502
        assert body["error"] == "invalid_round"


# ---------------------------------------------------------------------------
# Unit: payload validation
# ---------------------------------------------------------------------------

class TestPayloadValidation:
    def test_unknown_pair_400_lists_real_supported_set(self):
        status, body = _serve(FRESH_ETH, {"pair": "DOGE/USD"})
        assert status == 400
        assert body["error"] == "unknown_pair"
        assert body["supported_pairs"] == sorted(BASE_MAINNET_FEEDS)
        assert body["supported_pairs"] == ["ETH/USD", "USDC/USD"]

    def test_btc_usd_unpinned_in_repo_is_unknown_here(self):
        status, body = _serve(FRESH_ETH, {"pair": "BTC/USD"})
        assert status == 400
        assert body["error"] == "unknown_pair"

    def test_missing_pair_400(self):
        status, body = _serve(FRESH_ETH, {"mode": "price"})
        assert status == 400
        assert body["error"] == "pair_required"

    def test_ohlcv_mode_honestly_not_available(self):
        status, body = _serve(FRESH_ETH,
                              {"pair": "ETH/USD", "mode": "ohlcv"})
        assert status == 400
        assert body["error"] == "mode_not_available"
        assert body["available_modes"] == ["price"]

    def test_trending_mode_honestly_not_available(self):
        status, body = _serve(FRESH_ETH,
                              {"pair": "ETH/USD", "mode": "trending"})
        assert status == 400
        assert body["error"] == "mode_not_available"

    def test_unknown_fields_400(self):
        status, body = _serve(FRESH_ETH,
                              {"pair": "ETH/USD", "guess": 1})
        assert status == 400
        assert body["error"] == "unknown_payload_fields"

    def test_non_object_payload_400(self):
        status, body = mdr.serve_market_data_read(
            ["ETH/USD"], read_round=lambda p: FRESH_ETH, now=NOW)
        assert status == 400
        assert body["error"] == "payload_must_be_object"

    def test_supported_pairs_derive_from_pinned_feeds(self):
        assert list(mdr.SUPPORTED_PAIRS) == sorted(BASE_MAINNET_FEEDS)

    def test_price_registration(self):
        res = x402.get_resource(RESOURCE)
        assert res is not None
        assert res["token"] == "USDC"
        assert res["amount_usdc"] == 0.0075
        # USDC has 6 decimals: $0.0075 == 7500 atomic units.
        assert res["amount_atomic"] == "7500"
        ids = [r["id"] for r in x402.list_resources()]
        assert RESOURCE in ids
        # No OHLCV resource was invented.
        assert "market-data-ohlcv" not in ids


# ---------------------------------------------------------------------------
# Integration: full paid loop through the real routes
# ---------------------------------------------------------------------------

def _make_key() -> Account:
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
        supplied = (kwargs.get("payer_wallet") or "").lower()
        if supplied and supplied != BUYER.lower():
            return {"ok": False, "error": "payer_wallet_mismatch"}
        if paid < expected * 9995 // 10000:  # mirror AMOUNT_TOLERANCE_BPS
            return {"ok": False, "error": "insufficient_amount",
                    "paid_display": paid / 1e6,
                    "required_display": expected / 1e6, "token": "USDC"}
        return {"ok": True, "payer_wallet": BUYER}

    monkeypatch.setattr(x402, "verify_treasury_transfer", _verify)
    monkeypatch.setattr(
        x402, "record_platform_payment",
        lambda **kw: {"payment_id": kw.get("payment_id"), "ok": True})
    monkeypatch.setattr(
        x402, "record_inflow", lambda *a, **k: {"ok": True})


@pytest.fixture()
def feed(monkeypatch):
    """Stub the module's production loader seam with a fresh round
    stamped against the real clock (the serve path uses time.time()
    when driven through the routes)."""
    now = int(time.time())
    state = {"round": ChainlinkRound(
        round_id=424242, answer=324_150_000_000, decimals=8,
        started_at=now - 90, updated_at=now - 90,
        answered_in_round=424242)}
    monkeypatch.setattr(mdr, "load_latest_round",
                        lambda pair: state["round"])
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
    # Price is server-derived: the 402 demands exactly $0.0075 USDC.
    assert ch["payment"]["maxAmountRequired"] == "7500", ch
    sig = BUYER_KEY.sign_message(
        encode_defunct(text=ch["sign_message"])).signature.hex()
    r = client.post("/api/x402/challenge-bind", json={
        "challenge_id": ch["challenge_id"],
        "payer_wallet": BUYER,
        "signature": sig,
    })
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["ok"] is True
    assert r.get_json()["ownership_proven"] is True
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
    def test_paid_settle_delivers_round_matched_price(
            self, client, x402_db, mock_onchain, feed):
        paid = _pay(client, _tx("c1"))
        r = _serve_paid(client, paid["access_token"],
                        {"pair": "ETH/USD", "mode": "price"})
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["price_fp"] == 324_150_000_000
        assert body["price"] == "3241.5"
        assert body["round_id"] == 424242
        assert body["answered_in_round"] == 424242
        assert body["stale"] is False
        assert body["incomplete"] is False

    def test_stateless_two_sequential_paid_calls_both_succeed(
            self, client, x402_db, mock_onchain, feed):
        # No session state: each call is its own challenge, payment
        # and read. The second call re-reads the feed seam and sees
        # the NEW round — proof nothing was cached from the first.
        p1 = _pay(client, _tx("c2"))
        r1 = _serve_paid(client, p1["access_token"], {"pair": "ETH/USD"})
        assert r1.status_code == 200, r1.get_json()
        assert r1.get_json()["round_id"] == 424242

        now = int(time.time())
        feed["round"] = ChainlinkRound(
            round_id=424243, answer=325_000_000_000, decimals=8,
            started_at=now - 30, updated_at=now - 30,
            answered_in_round=424243)
        p2 = _pay(client, _tx("c3"))
        r2 = _serve_paid(client, p2["access_token"], {"pair": "ETH/USD"})
        assert r2.status_code == 200, r2.get_json()
        assert r2.get_json()["round_id"] == 424243
        assert r2.get_json()["price_fp"] == 325_000_000_000

    def test_paid_stale_round_flagged_through_routes(
            self, client, x402_db, mock_onchain, feed):
        now = int(time.time())
        feed["round"] = ChainlinkRound(
            round_id=424200, answer=300_000_000_000, decimals=8,
            started_at=now - 7300, updated_at=now - 7200,
            answered_in_round=424200)
        paid = _pay(client, _tx("c4"))
        r = _serve_paid(client, paid["access_token"], {"pair": "ETH/USD"})
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["stale"] is True
        assert body["age_s"] >= 7200

    def test_paid_unknown_pair_400_with_supported_set(
            self, client, x402_db, mock_onchain, feed):
        paid = _pay(client, _tx("c5"))
        r = _serve_paid(client, paid["access_token"], {"pair": "BTC/USD"})
        assert r.status_code == 400, r.get_json()
        body = r.get_json()
        assert body["error"] == "unknown_pair"
        assert body["supported_pairs"] == ["ETH/USD", "USDC/USD"]
        # P1-1: the 400 must NOT burn the single-use token — the
        # same token still delivers once the payload is valid.
        r2 = _serve_paid(client, paid["access_token"],
                         {"pair": "ETH/USD"})
        assert r2.status_code == 200, r2.get_json()
        assert r2.get_json()["round_id"] == 424242

    def test_unpaid_serve_rejected(
            self, client, x402_db, mock_onchain, feed):
        r = client.post(f"/api/paid/{RESOURCE}?wallet={BUYER}",
                        json={"pair": "ETH/USD"})
        assert r.status_code == 402
        assert "price" not in (r.get_json() or {})

    def test_tx_hash_replay_rejected(
            self, client, x402_db, mock_onchain, feed):
        tx = _tx("c6")
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
            self, client, x402_db, mock_onchain, feed):
        # This test must exercise the paid path, so exhaust the buyer's
        # Wave 1 free ration for this resource first (same pattern as
        # test_wave1_hook_funnel's ration-exhausted case); otherwise the
        # serves below can be answered from the free ration and the
        # token's single-use is never exercised. The single-use
        # assertions below are unchanged.
        from sincor2.free_ration import daily_ration_tracker
        while daily_ration_tracker.remaining(BUYER.lower(), RESOURCE) > 0:
            assert daily_ration_tracker.try_consume(
                BUYER.lower(), RESOURCE, ip="203.0.113.9")
        paid = _pay(client, _tx("c7"))
        r1 = _serve_paid(client, paid["access_token"], {"pair": "ETH/USD"})
        assert r1.status_code == 200
        r2 = _serve_paid(client, paid["access_token"], {"pair": "ETH/USD"})
        assert r2.status_code != 200
        # R2 is the item-7a wallet-less 402 offer envelope: its "price"
        # key is the public list price (an offer term), not served data.
        # The single-use guarantee is that no SERVED market-data keys
        # leak in the refused second serve.
        assert "price_fp" not in (r2.get_json() or {})
        assert "answer" not in (r2.get_json() or {})

    def test_unbound_challenge_cannot_settle(
            self, client, x402_db, mock_onchain, feed):
        draft = x402.create_challenge(RESOURCE)
        r = client.post("/api/x402/verify", json={
            "challenge_id": draft["challenge_id"],
            "tx_hash": _tx("c8"),
            "payer_wallet": BUYER,
        })
        assert r.status_code == 400, r.get_json()
        assert r.get_json()["error"] == "challenge_unbound"

    def test_underpayment_rejected(
            self, client, x402_db, mock_onchain, feed, paid_amount):
        paid_amount["atomic"] = 1000  # $0.001 against a $0.0075 price
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
            "tx_hash": _tx("c9"),
            "payer_wallet": BUYER,
        })
        assert r.status_code == 402, r.get_json()
        assert r.get_json()["error"] == "insufficient_amount"
