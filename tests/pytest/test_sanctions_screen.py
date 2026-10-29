"""Wallet Sanctions Screen (G1) — unit + full-loop x402 integration tests.

Unit layer proves the screening verdicts against the REAL vendored OFAC
snapshot (provenance values — publication date, entry/record counts —
are read from the snapshot itself, never hardcoded, so a snapshot
refresh does not stale these tests): flagged fixture,
clean fixture, invalid input, and the honest stale state (a snapshot
past the P20 48h fail-closed window returns ``unknown`` for EVERY
address — never ``clean``, never fake-fresh).

Integration layer proves the complete first-dollar loop through the
real Flask routes: 402 challenge -> EIP-191 bound challenge (W-102r3)
-> on-chain verify (mocked transport, real verify_challenge semantics)
-> settle -> single-use access token -> screen result delivered ->
tx_hash replay rejected -> token replay rejected -> unbound challenge
rejected -> underpayment rejected.

Mock pattern reuses tests/pytest/test_x402_verify_frontrun.py:
monkeypatch x402.verify_treasury_transfer with the same semantics
(ok + on-chain payer, payer mismatch / insufficient amount failures).
Freshness fixtures inject the real snapshot with a controlled
``fetched_at`` through the sanctions_screen loader seam.
"""
from __future__ import annotations

import datetime
import json
import time

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

# Production import order matters: mvp_app must be imported before the
# billing blueprint (see test_x402_verify_frontrun.py).
import sincor2.mvp_app  # noqa: F401  (imported for binding side effects)
from sincor2.mvp_blueprints import billing

import sincor2.sanctions_screen as ss
import sincor2.x402_payments as x402
from sincor2.defi.ofac_sdn import load_snapshot
from sincor2.sig_canonical import is_low_s

RESOURCE = "wallet-sanctions-screen"
# Verified present in the vendored snapshot: MESRI, Behzad (uid 24003),
# programs CYBER2 / HRIT-IR, an ETH digital-currency address.
FLAGGED_ADDR = "0x252a8bd2319d8a555b872990601221b3a2053bce"
CLEAN_ADDR = "0x000000000000000000000000000000000000dEaD"


def _make_key() -> Account:
    while True:
        acct = Account.create()
        probe = acct.sign_message(encode_defunct(text="sincor-low-s-probe"))
        if is_low_s(probe.signature):
            return acct


BUYER_KEY = _make_key()
BUYER = BUYER_KEY.address


def _fresh_real_snapshot() -> dict:
    snap = dict(load_snapshot())
    snap["fetched_at"] = datetime.datetime.now(
        datetime.timezone.utc).isoformat()
    return snap


def _stale_real_snapshot() -> dict:
    snap = dict(load_snapshot())
    old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        days=10)
    snap["fetched_at"] = old.isoformat()
    return snap


# ---------------------------------------------------------------------------
# Unit: screening verdicts
# ---------------------------------------------------------------------------

class TestScreenWallet:
    def test_invalid_addresses_rejected(self):
        for bad in ("", "0x123", "not-an-address", None, 123,
                    "252a8bd2319d8a555b872990601221b3a2053bce",
                    "0xZZ2a8bd2319d8a555b872990601221b3a2053b"):
            res = ss.screen_wallet(bad, snapshot=_fresh_real_snapshot())
            assert res["ok"] is False, bad
            assert res["error"] == "invalid_address", bad

    def test_flagged_fixture_from_real_snapshot(self):
        snap = _fresh_real_snapshot()
        # Uppercased input must still match: canonical lowercase compare.
        res = ss.screen_wallet(FLAGGED_ADDR.upper(), snapshot=snap)
        assert res["ok"] is True
        assert res["verdict"] == "flagged"
        assert res["status"] == "flagged"
        assert res["screened"] is True
        assert res["stale"] is False
        match = res["match"]
        assert match["uid"] == "24003"
        assert match["name"] == "MESRI, Behzad"
        assert "CYBER2" in match["programs"]
        assert match["currency"] == "ETH"
        # Provenance is real snapshot metadata, not invented data:
        # the screen must echo THIS snapshot's own declared values.
        assert res["snapshot_publication_date"] == \
            snap["publication_date"]
        assert res["snapshot_entry_count"] == snap["entry_count"]
        assert res["snapshot_record_count"] == snap["record_count"]

    def test_clean_address_from_real_snapshot(self):
        snap = _fresh_real_snapshot()
        res = ss.screen_wallet(CLEAN_ADDR, snapshot=snap)
        assert res["ok"] is True
        assert res["verdict"] == "clean"
        assert res["status"] == "screened"
        assert res["screened"] is True
        assert res["match"] is None
        assert res["snapshot_publication_date"] == \
            snap["publication_date"]

    def test_stale_snapshot_returns_unknown_never_clean_or_flagged(self):
        snap = load_snapshot()
        fetched_epoch = ss._snapshot_epoch(snap["fetched_at"])
        now = fetched_epoch + 49 * 3600  # just past the 48h fail window
        for addr in (FLAGGED_ADDR, CLEAN_ADDR):
            res = ss.screen_wallet(addr, snapshot=snap, now=now)
            assert res["ok"] is True
            assert res["verdict"] == "unknown", addr
            assert res["status"] == "unknown", addr
            assert res["stale"] is True
            assert res["screened"] is False
            assert res["match"] is None

    def test_stale_synthetic_snapshot_unknown(self):
        snap = {
            "source": "OFAC SDN List", "source_url": "https://x",
            "publication_date": "01/01/2020", "record_count": 1,
            "fetched_at": "2020-01-01T00:00:00+00:00", "entry_count": 1,
            "entries": [{
                "uid": "1", "name": "TEST, Flagged", "type": "Individual",
                "programs": ["TEST"],
                "digital_currency_addresses": [
                    {"currency": "ETH", "address": FLAGGED_ADDR}],
                "aliases": [], "ids": [],
            }],
        }
        res = ss.screen_wallet(FLAGGED_ADDR, snapshot=snap,
                               now=time.time())
        assert res["verdict"] == "unknown"
        assert res["stale"] is True

    def test_freshness_alert_window_still_screens(self):
        snap = load_snapshot()
        fetched_epoch = ss._snapshot_epoch(snap["fetched_at"])
        now = fetched_epoch + 25 * 3600  # alert window, inside 48h fail
        res = ss.screen_wallet(CLEAN_ADDR, snapshot=snap, now=now)
        assert res["verdict"] == "clean"
        assert "freshness alert" in res["detail"]

    def test_price_registration(self):
        res = x402.get_resource(RESOURCE)
        assert res is not None
        assert res["token"] == "USDC"
        assert res["amount_usdc"] == 0.005
        assert res["amount_display"] == 0.005
        # USDC has 6 decimals: $0.005 == 5000 atomic units.
        assert res["amount_atomic"] == "5000"
        ids = [r["id"] for r in x402.list_resources()]
        assert RESOURCE in ids


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
def fresh_snapshot(monkeypatch):
    snap = _fresh_real_snapshot()
    monkeypatch.setattr(ss, "load_screen_snapshot", lambda: snap)
    return snap


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
    # Price is server-derived: the 402 demands exactly $0.005 USDC.
    assert ch["payment"]["maxAmountRequired"] == "5000", ch
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


def _serve(client, access_token: str, address: str):
    return client.post(
        f"/api/paid/{RESOURCE}",
        headers={"X-Payment-Token": access_token},
        json={"address": address},
    )


class TestFullLoop:
    def test_paid_settle_delivers_flagged(
            self, client, x402_db, mock_onchain, fresh_snapshot):
        paid = _pay(client, _tx("a1"))
        r = _serve(client, paid["access_token"], FLAGGED_ADDR)
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["verdict"] == "flagged"
        assert body["match"]["uid"] == "24003"
        assert body["snapshot_publication_date"] == \
            fresh_snapshot["publication_date"]

    def test_paid_settle_delivers_clean(
            self, client, x402_db, mock_onchain, fresh_snapshot):
        paid = _pay(client, _tx("a2"))
        r = _serve(client, paid["access_token"], CLEAN_ADDR)
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["verdict"] == "clean"
        assert body["status"] == "screened"

    def test_paid_settle_stale_snapshot_503_token_not_consumed(
            self, client, x402_db, mock_onchain, monkeypatch):
        # P1-3a (audit redefinition): a stale snapshot used to serve a
        # paid 200 "unknown"; it now refuses 503 snapshot_stale
        # BEFORE the token is consumed.
        state = {"snap": _stale_real_snapshot()}
        monkeypatch.setattr(
            ss, "load_screen_snapshot", lambda: state["snap"])
        paid = _pay(client, _tx("a3"))
        r = _serve(client, paid["access_token"], FLAGGED_ADDR)
        assert r.status_code == 503, r.get_json()
        body = r.get_json()
        # Still honest: stale list proves nothing either way.
        assert body["ok"] is False
        assert body["error"] == "snapshot_stale"
        assert body["verdict"] == "unknown"
        assert body["stale"] is True
        # P1-1: the 503 did NOT consume the token — with a fresh
        # snapshot the SAME token delivers the real verdict.
        state["snap"] = _fresh_real_snapshot()
        r2 = _serve(client, paid["access_token"], FLAGGED_ADDR)
        assert r2.status_code == 200, r2.get_json()
        assert r2.get_json()["verdict"] == "flagged"
        assert r2.get_json()["match"]["uid"] == "24003"

    def test_unpaid_serve_rejected(
            self, client, x402_db, mock_onchain, fresh_snapshot):
        r = client.post(f"/api/paid/{RESOURCE}?wallet={BUYER}",
                        json={"address": FLAGGED_ADDR})
        assert r.status_code == 402
        assert "verdict" not in (r.get_json() or {})

    def test_tx_hash_replay_rejected(
            self, client, x402_db, mock_onchain, fresh_snapshot):
        tx = _tx("a4")
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
            self, client, x402_db, mock_onchain, fresh_snapshot):
        paid = _pay(client, _tx("a5"))
        r1 = _serve(client, paid["access_token"], CLEAN_ADDR)
        assert r1.status_code == 200
        r2 = _serve(client, paid["access_token"], CLEAN_ADDR)
        assert r2.status_code != 200
        assert "verdict" not in (r2.get_json() or {})

    def test_unbound_challenge_cannot_settle(
            self, client, x402_db, mock_onchain, fresh_snapshot):
        draft = x402.create_challenge(RESOURCE)
        r = client.post("/api/x402/verify", json={
            "challenge_id": draft["challenge_id"],
            "tx_hash": _tx("a6"),
            "payer_wallet": BUYER,
        })
        assert r.status_code == 400, r.get_json()
        assert r.get_json()["error"] == "challenge_unbound"

    def test_underpayment_rejected(
            self, client, x402_db, mock_onchain, fresh_snapshot,
            paid_amount):
        paid_amount["atomic"] = 1000  # $0.001 against a $0.005 price
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
            "tx_hash": _tx("a7"),
            "payer_wallet": BUYER,
        })
        assert r.status_code == 402, r.get_json()
        assert r.get_json()["error"] == "insufficient_amount"

    def test_invalid_address_after_payment_is_400(
            self, client, x402_db, mock_onchain, fresh_snapshot):
        paid = _pay(client, _tx("a8"))
        r = _serve(client, paid["access_token"], "not-an-address")
        assert r.status_code == 400
        assert r.get_json()["error"] == "invalid_address"
        # P1-1: the 400 must NOT burn the single-use token — the
        # same token still delivers once the payload is valid.
        r2 = _serve(client, paid["access_token"], CLEAN_ADDR)
        assert r2.status_code == 200, r2.get_json()
        assert r2.get_json()["verdict"] == "clean"


# ---------------------------------------------------------------------------
# P1-3a: serve-level stale gate + refresh CLI wiring
# ---------------------------------------------------------------------------

class TestServeStaleGate:
    def test_serve_stale_snapshot_is_503(self, monkeypatch):
        monkeypatch.setattr(
            ss, "load_screen_snapshot", _stale_real_snapshot)
        status, body = ss.serve_sanctions_screen(
            {"address": CLEAN_ADDR})
        assert status == 503
        assert body["ok"] is False
        assert body["error"] == "snapshot_stale"
        assert body["stale"] is True

    def test_serve_fresh_snapshot_is_200(self, monkeypatch):
        monkeypatch.setattr(
            ss, "load_screen_snapshot", _fresh_real_snapshot)
        status, body = ss.serve_sanctions_screen(
            {"address": CLEAN_ADDR})
        assert status == 200
        assert body["verdict"] == "clean"


class TestRefreshOfacSdn:
    """The refresh CLI wires the verified updater machinery to the
    SINCOR_DATA_DIR-preferred snapshot path. Hermetic: the network
    fetch and XML verification are stubbed; the atomic write, the
    status semantics and the screen's read preference are real."""

    def _stub_updater(self, monkeypatch, snap):
        import sincor2.refresh_ofac_sdn as refresh_mod
        monkeypatch.setattr(refresh_mod, "fetch_sdn_xml",
                            lambda url: b"<sdn/>")
        monkeypatch.setattr(
            refresh_mod, "verify_and_parse",
            lambda raw, fetched_at: {**snap,
                                     "fetched_at": fetched_at})
        return refresh_mod

    def test_active_path_prefers_sincor_data_dir(
            self, tmp_path, monkeypatch):
        import sincor2.refresh_ofac_sdn as refresh_mod
        from sincor2.defi.ofac_sdn import snapshot_path
        monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
        assert refresh_mod.active_snapshot_path() == str(
            tmp_path / "p20_ofac_sdn.json")
        monkeypatch.delenv("SINCOR_DATA_DIR")
        assert refresh_mod.active_snapshot_path() == snapshot_path()

    def test_refresh_writes_atomically_under_data_dir(
            self, tmp_path, monkeypatch):
        snap = _fresh_real_snapshot()
        refresh_mod = self._stub_updater(monkeypatch, snap)
        monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
        result = refresh_mod.refresh()
        assert result["status"] == "updated"
        written = json.loads(
            (tmp_path / "p20_ofac_sdn.json").read_text())
        assert written["publication_date"] == snap["publication_date"]
        assert written["entry_count"] == snap["entry_count"]
        assert written["fetched_at"] == result["fetched_at"]
        # Same publication again: a verified re-download truthfully
        # advances fetched_at -> "refreshed", never "updated".
        result2 = refresh_mod.refresh()
        assert result2["status"] == "refreshed"
        assert result2["previous_publication_date"] == \
            snap["publication_date"]

    def test_screen_prefers_refreshed_data_dir_snapshot(
            self, tmp_path, monkeypatch):
        snap = _fresh_real_snapshot()
        snap["source"] = "REFRESHED-COPY-MARKER"
        (tmp_path / "p20_ofac_sdn.json").write_text(json.dumps(snap))
        monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
        loaded = ss.load_screen_snapshot()
        assert loaded["source"] == "REFRESHED-COPY-MARKER"
