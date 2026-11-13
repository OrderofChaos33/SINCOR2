"""Polymarket Whale Signal (G3) — unit + full-loop x402 integration tests.

Hermetic by design: the tracked-whale registry is a fixture dict and
the trade feed is a stubbed ``fresh_entries`` returning fixture
``WhaleSignal`` objects built from a stubbed trade — no live network.

Unit layer proves:
- a fixture signal is served with FULL provenance (wallet, market,
  question, side, whale trade size, detected_at) and the wallet's
  resolved-market stats exactly as scored (registry values verbatim);
- the wallet stats recompute exactly under the FIXED settlement math
  via the real ``whale_flow.score_wallet`` (winning share pays 1,
  losing share pays 0 — the PR #515 invariant; a winning 100-share
  position bought at 0.40 realizes exactly +$60, never ~100x that);
- empty registry -> paid serve refused 503 ``registry_unavailable``
  BEFORE the token is consumed (P1-3b);
- registry present, no fresh entries -> honest empty
  (``no_fresh_signals``) with the track record still served;
- the claim-bearing fields carry no profitability claim (Mission 1A
  whale-flow trading verdict was NO-GO, 1/60 episodes — the data read
  must never imply following the signal pays).

Integration layer proves the complete first-dollar loop through the
real Flask routes: 402 challenge -> EIP-191 bound challenge (W-102r3)
-> on-chain verify (mocked transport, real verify_challenge
semantics) -> settle -> single-use access token -> signal delivered
-> tx_hash replay rejected -> token replay rejected -> unbound
challenge rejected -> underpayment rejected.

Mock pattern reuses tests/pytest/test_sanctions_screen.py.
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

import sincor2.whale_flow as wf
import sincor2.whale_signal as ws
import sincor2.x402_payments as x402
from sincor2.sig_canonical import is_low_s

RESOURCE = "polymarket-whale-signal"

WHALE_A = "0xf0318c32136c2db7fec88b84869aee6a1106c80c"
WHALE_B = "0x1f3a646ce5cbe2c70270e7a46161d89b7c7b895d"

REGISTRY = {
    WHALE_A: {
        "realized_pnl_usd": 54321.09,
        "win_rate": 0.62,
        "resolved_trades": 87,
        "resolved_markets": 41,
        "scored_at": 1761000000.0,
    },
    WHALE_B: {
        "realized_pnl_usd": 1234.5,
        "win_rate": 0.55,
        "resolved_trades": 34,
        "resolved_markets": 22,
        "scored_at": 1761000000.0,
    },
}

DETECTED_AT = 1761100000.0


def _signal(wallet=WHALE_A, market_id="558934"):
    return wf.WhaleSignal(
        wallet=wallet,
        market_id=market_id,
        question="Will Spain win the 2026 FIFA World Cup?",
        token_id="987654321",
        side="BUY_YES",
        whale_pnl_usd=REGISTRY[wallet]["realized_pnl_usd"],
        whale_win_rate=REGISTRY[wallet]["win_rate"],
        whale_trade_usdc=1234.56,
        detected_at=DETECTED_AT,
    )


def _make_key() -> Account:
    while True:
        acct = Account.create()
        probe = acct.sign_message(encode_defunct(text="sincor-low-s-probe"))
        if is_low_s(probe.signature):
            return acct


BUYER_KEY = _make_key()
BUYER = BUYER_KEY.address


@pytest.fixture()
def fixture_feed(monkeypatch):
    """Fixture registry + stubbed trade feed (hermetic)."""
    state = {"registry": dict(REGISTRY), "signals": [_signal()]}
    monkeypatch.setattr(ws, "load_tracked_registry",
                        lambda: state["registry"])
    monkeypatch.setattr(ws, "_fresh_entries",
                        lambda window_min: list(state["signals"]))
    return state


# ---------------------------------------------------------------------------
# Unit: the read itself
# ---------------------------------------------------------------------------

class TestCollectWhaleSignals:
    def test_fixture_signal_served_with_full_provenance(self, fixture_feed):
        status, body = ws.serve_whale_signal({})
        assert status == 200
        assert body["ok"] is True
        assert body["status"] == "signals_available"
        assert body["tracked_wallet_count"] == 2
        assert len(body["signals"]) == 1
        sig = body["signals"][0]
        # Provenance: every field traces to the fixture trade/registry.
        assert sig["wallet"] == WHALE_A
        assert sig["market_id"] == "558934"
        assert sig["question"] == "Will Spain win the 2026 FIFA World Cup?"
        assert sig["side"] == "BUY_YES"
        assert sig["token_id"] == "987654321"
        assert sig["whale_trade_size_usdc"] == 1234.56
        assert sig["detected_at"] == DETECTED_AT
        assert sig["detected_at_iso"].startswith("2025-")
        # Resolved-market stats exactly as scored (registry verbatim).
        stats = sig["whale_stats"]
        assert stats["realized_pnl_usd"] == 54321.09
        assert stats["win_rate"] == 0.62
        assert stats["resolved_trades"] == 87
        assert stats["resolved_markets"] == 41
        assert stats["scored_at"] == 1761000000.0
        # Track record (free-preview element) covers every tracked wallet.
        record = {r["wallet"]: r for r in body["track_record"]}
        assert set(record) == {WHALE_A, WHALE_B}
        assert record[WHALE_B]["realized_pnl_usd"] == 1234.5
        assert record[WHALE_B]["resolved_trades"] == 34

    def test_empty_registry_refused_503(self, fixture_feed):
        # P1-3b (audit redefinition): an empty registry used to serve
        # a paid 200 with an empty list; it now refuses 503
        # registry_unavailable so the buyer is never charged for it.
        fixture_feed["registry"] = {}
        status, body = ws.serve_whale_signal({})
        assert status == 503
        assert body["ok"] is False
        assert body["error"] == "registry_unavailable"
        assert body["status"] == "registry_empty"
        assert body["signals"] == []
        assert body["track_record"] == []
        assert body["tracked_wallet_count"] == 0

    def test_no_fresh_signals_honest_empty_with_track_record(
            self, fixture_feed):
        fixture_feed["signals"] = []
        status, body = ws.serve_whale_signal({})
        assert status == 200
        assert body["status"] == "no_fresh_signals"
        assert body["signals"] == []
        # No signals — but the track record is still served honestly.
        assert len(body["track_record"]) == 2

    def test_wallet_and_market_filters(self, fixture_feed):
        fixture_feed["signals"] = [_signal(), _signal(WHALE_B, "777")]
        status, body = ws.serve_whale_signal({"wallet": WHALE_B})
        assert status == 200
        assert [s["wallet"] for s in body["signals"]] == [WHALE_B]
        status, body = ws.serve_whale_signal({"market_id": "558934"})
        assert [s["market_id"] for s in body["signals"]] == ["558934"]
        status, body = ws.serve_whale_signal({"wallet": WHALE_B,
                                              "market_id": "558934"})
        assert body["status"] == "no_fresh_signals"
        assert body["signals"] == []

    def test_invalid_inputs_rejected(self, fixture_feed):
        for payload in ({"wallet": "not-an-address"},
                        {"wallet": "0x123"},
                        {"window_minutes": 0},
                        {"window_minutes": 99999},
                        {"window_minutes": "soon"}):
            status, body = ws.serve_whale_signal(payload)
            assert status == 400, payload
            assert body["ok"] is False, payload

    def test_feed_failure_is_honest_503(self, fixture_feed, monkeypatch):
        def _boom(window_min):
            raise RuntimeError("feed down")
        monkeypatch.setattr(ws, "_fresh_entries", _boom)
        status, body = ws.serve_whale_signal({})
        assert status == 503
        assert body["error"] == "signal_feed_unavailable"
        assert "signals" not in body

    def test_no_profitability_claims_in_claim_fields(self, fixture_feed):
        status, body = ws.serve_whale_signal({})
        assert status == 200
        # Principled check on the claim-bearing fields — the response
        # shape and the seller-authored notice — not a brittle wordlist
        # over the whole payload (a whale's historical realized P&L is a
        # labelled statistic, asserted separately above).
        assert "no claim about future results" in body["notice"]
        assert "guarantee" not in body["notice"].lower()
        assert "guarantee" not in body["data_delay_note"].lower()
        forbidden_keys = {"recommendation", "expected_return",
                          "expected_pnl", "confidence", "win_probability",
                          "guaranteed_profit", "advice"}
        for sig in body["signals"]:
            assert forbidden_keys.isdisjoint(sig.keys())
            assert forbidden_keys.isdisjoint(sig["whale_stats"].keys())
        assert forbidden_keys.isdisjoint(body.keys())

    def test_price_registration(self):
        res = x402.get_resource(RESOURCE)
        assert res is not None
        assert res["token"] == "USDC"
        assert res["amount_usdc"] == 0.02
        assert res["amount_display"] == 0.02
        # USDC has 6 decimals: $0.02 == 20000 atomic units.
        assert res["amount_atomic"] == "20000"
        ids = [r["id"] for r in x402.list_resources()]
        assert RESOURCE in ids


# ---------------------------------------------------------------------------
# Unit: wallet stats recompute under the FIXED settlement math
# ---------------------------------------------------------------------------

class TestFixedSettlementMath:
    """score_wallet over a stubbed trade feed + stubbed resolution:
    a winning share pays exactly 1, a losing share exactly 0 (PR #515
    invariant — no ~100x payout inflation)."""

    def _score(self, monkeypatch, *, yes_won, side_asset="yes"):
        yes_tok, no_tok = "111", "222"
        trades = [{
            "conditionId": "cond-1", "slug": "mkt", "eventSlug": "ev",
            "price": 0.40, "size": 100, "asset": yes_tok, "side": "BUY",
            "timestamp": 1761000000,
        }]
        monkeypatch.setattr(wf, "_wallet_trades",
                            lambda wallet, limit=500: trades)
        monkeypatch.setattr(wf, "_market_resolution", lambda *a, **k: {
            "yes_won": yes_won, "token_id_yes": yes_tok,
            "token_id_no": no_tok, "question": "Q?", "market_id": "1",
        })
        return wf.score_wallet(WHALE_A)

    def test_winning_position_realizes_exact_settlement(self, monkeypatch):
        stats = self._score(monkeypatch, yes_won=True)
        # Bought 100 shares at 0.40 ($40 cost); winner pays 100 x $1.
        assert stats.realized_pnl_usd == 60.0
        assert stats.win_rate == 1.0
        assert stats.resolved_trades == 1
        assert stats.resolved_markets == 1
        assert stats.total_volume_usd == 40.0

    def test_losing_position_pays_zero(self, monkeypatch):
        stats = self._score(monkeypatch, yes_won=False)
        assert stats.realized_pnl_usd == -40.0
        assert stats.win_rate == 0.0


# ---------------------------------------------------------------------------
# Integration: full paid loop through the real routes
# ---------------------------------------------------------------------------

@pytest.fixture()
def x402_db(tmp_path, monkeypatch):
    db = tmp_path / "orders.db"
    monkeypatch.setenv("ORDERS_DB_PATH", str(db))
    x402.init_x402_db()
    return db


@pytest.fixture()
def paid_amount():
    """Test-controlled on-chain paid amount (atomic). Default: exact."""
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
    # Price is server-derived: the 402 demands exactly $0.02 USDC.
    assert ch["payment"]["maxAmountRequired"] == "20000", ch
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


def _serve(client, access_token: str, payload=None):
    return client.post(
        f"/api/paid/{RESOURCE}",
        headers={"X-Payment-Token": access_token},
        json=payload or {},
    )


class TestFullLoop:
    def test_paid_settle_delivers_signal_with_provenance(
            self, client, x402_db, mock_onchain, fixture_feed):
        paid = _pay(client, _tx("b1"))
        r = _serve(client, paid["access_token"])
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["status"] == "signals_available"
        sig = body["signals"][0]
        assert sig["wallet"] == WHALE_A
        assert sig["question"] == "Will Spain win the 2026 FIFA World Cup?"
        assert sig["whale_stats"]["realized_pnl_usd"] == 54321.09
        assert sig["whale_stats"]["resolved_trades"] == 87
        assert len(body["track_record"]) == 2

    def test_paid_settle_empty_registry_503_token_not_consumed(
            self, client, x402_db, mock_onchain, fixture_feed):
        # P1-3b (audit redefinition): empty registry -> 503, not a
        # charged 200. P1-1: the 503 leaves the token unconsumed.
        fixture_feed["registry"] = {}
        paid = _pay(client, _tx("b2"))
        r = _serve(client, paid["access_token"])
        assert r.status_code == 503, r.get_json()
        body = r.get_json()
        assert body["error"] == "registry_unavailable"
        assert body["status"] == "registry_empty"
        assert body["signals"] == []
        # Registry populated: the SAME token now delivers.
        fixture_feed["registry"] = dict(REGISTRY)
        r2 = _serve(client, paid["access_token"])
        assert r2.status_code == 200, r2.get_json()
        assert r2.get_json()["status"] == "signals_available"

    def test_paid_settle_no_fresh_signals_honest_empty(
            self, client, x402_db, mock_onchain, fixture_feed):
        fixture_feed["signals"] = []
        paid = _pay(client, _tx("b3"))
        r = _serve(client, paid["access_token"])
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["status"] == "no_fresh_signals"
        assert body["signals"] == []
        assert len(body["track_record"]) == 2

    def test_unpaid_serve_rejected(
            self, client, x402_db, mock_onchain, fixture_feed):
        r = client.post(f"/api/paid/{RESOURCE}?wallet={BUYER}", json={})
        assert r.status_code == 402
        assert "signals" not in (r.get_json() or {})

    def test_tx_hash_replay_rejected(
            self, client, x402_db, mock_onchain, fixture_feed):
        tx = _tx("b4")
        _pay(client, tx)
        # Second, separately bound challenge; same tx_hash must fail.
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
            self, client, x402_db, mock_onchain, fixture_feed):
        paid = _pay(client, _tx("b5"))
        r1 = _serve(client, paid["access_token"])
        assert r1.status_code == 200
        r2 = _serve(client, paid["access_token"])
        assert r2.status_code != 200
        assert "signals" not in (r2.get_json() or {})

    def test_unbound_challenge_cannot_settle(
            self, client, x402_db, mock_onchain, fixture_feed):
        draft = x402.create_challenge(RESOURCE)
        r = client.post("/api/x402/verify", json={
            "challenge_id": draft["challenge_id"],
            "tx_hash": _tx("b6"),
            "payer_wallet": BUYER,
        })
        assert r.status_code == 400, r.get_json()
        assert r.get_json()["error"] == "challenge_unbound"

    def test_underpayment_rejected(
            self, client, x402_db, mock_onchain, fixture_feed,
            paid_amount):
        paid_amount["atomic"] = 1000  # $0.001 against a $0.02 price
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
            "tx_hash": _tx("b7"),
            "payer_wallet": BUYER,
        })
        assert r.status_code == 402, r.get_json()
        assert r.get_json()["error"] == "insufficient_amount"
