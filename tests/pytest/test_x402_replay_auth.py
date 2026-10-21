"""W-103: fulfilled-challenge replay authentication.

Finding: ``verify_challenge`` re-issued the victim's live access token for
an already-fulfilled challenge with zero authentication — anyone who
observed the challenge_id (402 responses, logs) could race the victim to
consume their single-use token via ``access_granted``.

Fix: replay/re-issue of a fulfilled challenge requires a fresh EIP-191
signature over ``SINCOR-X402-REPLAY\\n{challenge_id}\\n{ts_ms}`` from the
wallet recorded on the original challenge, with a 10-minute freshness
window. Every failure mode (missing challenge, blank recorded wallet,
bad/expired/mismatched signature) returns the same generic
``replay_auth_failed`` — no existence oracle, no distinguishing errors.

All keys below are throwaway ``Account.create()`` keys — never real keys.
All on-chain verification is mocked; no network, no real funds.
"""
from __future__ import annotations

import threading
import time

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

import sincor2.x402_payments as x402
from sincor2.x402_payments import build_replay_message


@pytest.fixture()
def x402_db(tmp_path, monkeypatch):
    """Point the x402 SQLite store at a throwaway DB for the test."""
    db = tmp_path / "orders.db"
    monkeypatch.setenv("ORDERS_DB_PATH", str(db))
    x402.init_x402_db()
    return db


def _mock_onchain_payer(monkeypatch, payer_addr: str):
    """Pretend every treasury-transfer check succeeds, crediting payer_addr."""
    def _ok(tx_hash, **kwargs):
        return {"ok": True, "payer_wallet": payer_addr}
    monkeypatch.setattr(x402, "verify_treasury_transfer", _ok)


def _real_hash(seed: str = "aa") -> str:
    return "0x" + seed * 32


def _sign(acct, challenge_id: str, ts_ms: int) -> str:
    """EIP-191 personal_sign of the canonical replay message — throwaway key."""
    msg = encode_defunct(text=build_replay_message(challenge_id, ts_ms))
    return acct.sign_message(msg).signature.hex()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _fulfill(x402_db, monkeypatch, payer_addr: str,
             resource_id: str = "agent_task",
             tx_seed: str = "aa") -> dict:
    """Fulfill a challenge as payer_addr (mocked on-chain success)."""
    _mock_onchain_payer(monkeypatch, payer_addr)
    ch = x402.create_challenge(resource_id, payer_wallet=payer_addr)
    assert ch["ok"] is False and ch["http_status"] == 402, ch
    res = x402.verify_challenge(
        ch["challenge_id"], _real_hash(tx_seed), payer_wallet=payer_addr)
    assert res["ok"] is True and res["status"] == "fulfilled", res
    return res


# ---------------------------------------------------------------------------
# 1. Legitimate payer CAN re-issue with a valid signature
# ---------------------------------------------------------------------------

class TestLegitimateReissue:
    def test_valid_signature_reissues_token(self, x402_db, monkeypatch):
        payer = Account.create()
        res = _fulfill(x402_db, monkeypatch, payer.address, tx_seed="a1")
        cid = res["challenge_id"]
        token = res["access_token"]
        ts = _now_ms()
        sig = _sign(payer, cid, ts)

        replay = x402.verify_challenge(
            cid, res["tx_hash"],
            replay_signature=sig, replay_ts_ms=ts)
        assert replay["ok"] is True
        assert replay["status"] == "already_fulfilled"
        # The SAME live token is re-issued to its owner — not a new mint.
        assert replay["access_token"] == token

    def test_message_format_is_domain_challenge_ts(self):
        assert build_replay_message("x402-abc", 123) == \
            "SINCOR-X402-REPLAY\nx402-abc\n123"

    def test_signature_accepts_hex_prefix(self, x402_db, monkeypatch):
        """0x-prefixed hex (the common wallet serialization) is accepted."""
        payer = Account.create()
        res = _fulfill(x402_db, monkeypatch, payer.address, tx_seed="a2")
        cid = res["challenge_id"]
        ts = _now_ms()
        sig = "0x" + _sign(payer, cid, ts)
        replay = x402.verify_challenge(
            cid, res["tx_hash"],
            replay_signature=sig, replay_ts_ms=ts)
        assert replay["ok"] is True
        assert replay["status"] == "already_fulfilled"


# ---------------------------------------------------------------------------
# 2. No signature -> generic rejection, no token
# ---------------------------------------------------------------------------

class TestNoSignatureRejected:
    def test_replay_without_signature_rejected(self, x402_db, monkeypatch):
        payer = Account.create()
        res = _fulfill(x402_db, monkeypatch, payer.address, tx_seed="b1")
        replay = x402.verify_challenge(res["challenge_id"], res["tx_hash"])
        assert replay == {"ok": False, "error": "replay_auth_failed"}
        assert "access_token" not in replay

    def test_replay_with_empty_fields_rejected(self, x402_db, monkeypatch):
        payer = Account.create()
        res = _fulfill(x402_db, monkeypatch, payer.address, tx_seed="b2")
        replay = x402.verify_challenge(
            res["challenge_id"], res["tx_hash"],
            replay_signature="", replay_ts_ms=None)
        assert replay == {"ok": False, "error": "replay_auth_failed"}
        assert "access_token" not in replay

    def test_missing_challenge_with_replay_creds_is_generic(self, x402_db):
        """No existence oracle: a replay against a nonexistent challenge_id
        fails exactly like any other replay-auth failure."""
        ts = _now_ms()
        res = x402.verify_challenge(
            "x402-does-not-exist", _real_hash("b3"),
            replay_signature="0x" + "ab" * 65, replay_ts_ms=ts)
        assert res == {"ok": False, "error": "replay_auth_failed"}

    def test_missing_challenge_without_creds_keeps_old_error(self, x402_db):
        """Normal (non-replay) first-verify UX is unchanged."""
        res = x402.verify_challenge("x402-does-not-exist", _real_hash("b4"))
        assert res == {"ok": False, "error": "challenge_not_found"}


# ---------------------------------------------------------------------------
# 3. Wrong signer's signature -> rejected
# ---------------------------------------------------------------------------

class TestWrongSignerRejected:
    def test_attacker_signature_rejected(self, x402_db, monkeypatch):
        victim = Account.create()
        attacker = Account.create()
        res = _fulfill(x402_db, monkeypatch, victim.address, tx_seed="c1")
        cid = res["challenge_id"]
        ts = _now_ms()
        # Attacker knows the challenge_id and signs with their OWN key.
        sig = _sign(attacker, cid, ts)
        replay = x402.verify_challenge(
            cid, res["tx_hash"],
            replay_signature=sig, replay_ts_ms=ts)
        assert replay == {"ok": False, "error": "replay_auth_failed"}
        assert "access_token" not in replay


# ---------------------------------------------------------------------------
# 4. Signature is bound to the challenge_id (cross-challenge replay)
# ---------------------------------------------------------------------------

class TestCrossChallengeReplayRejected:
    def test_sig_for_challenge_a_rejected_on_challenge_b(
            self, x402_db, monkeypatch):
        payer = Account.create()
        a = _fulfill(x402_db, monkeypatch, payer.address, tx_seed="d1")
        b = _fulfill(x402_db, monkeypatch, payer.address, tx_seed="d2")
        ts = _now_ms()
        sig_for_a = _sign(payer, a["challenge_id"], ts)
        replay = x402.verify_challenge(
            b["challenge_id"], b["tx_hash"],
            replay_signature=sig_for_a, replay_ts_ms=ts)
        assert replay == {"ok": False, "error": "replay_auth_failed"}
        assert "access_token" not in replay


# ---------------------------------------------------------------------------
# 5. Blank recorded payer_wallet -> rejected even with a valid signature
# ---------------------------------------------------------------------------

class TestBlankPayerWalletRejected:
    def test_blank_recorded_wallet_cannot_reissue(self, x402_db, monkeypatch):
        # Challenge fulfilled with NO recorded payer (allowed on main today).
        stranger = Account.create()
        res = _fulfill(x402_db, monkeypatch, "", tx_seed="e1")
        ts = _now_ms()
        sig = _sign(stranger, res["challenge_id"], ts)
        replay = x402.verify_challenge(
            res["challenge_id"], res["tx_hash"],
            replay_signature=sig, replay_ts_ms=ts)
        assert replay == {"ok": False, "error": "replay_auth_failed"}
        assert "access_token" not in replay


# ---------------------------------------------------------------------------
# 6. Stale timestamp -> rejected
# ---------------------------------------------------------------------------

class TestStaleTimestampRejected:
    def test_one_hour_old_signature_rejected(self, x402_db, monkeypatch):
        payer = Account.create()
        res = _fulfill(x402_db, monkeypatch, payer.address, tx_seed="f1")
        cid = res["challenge_id"]
        stale_ts = _now_ms() - 3_600_000  # 1 hour old
        sig = _sign(payer, cid, stale_ts)
        replay = x402.verify_challenge(
            cid, res["tx_hash"],
            replay_signature=sig, replay_ts_ms=stale_ts)
        assert replay == {"ok": False, "error": "replay_auth_failed"}
        assert "access_token" not in replay

    def test_future_timestamp_beyond_window_rejected(
            self, x402_db, monkeypatch):
        payer = Account.create()
        res = _fulfill(x402_db, monkeypatch, payer.address, tx_seed="f2")
        cid = res["challenge_id"]
        future_ts = _now_ms() + 3_600_000  # 1 hour in the future
        sig = _sign(payer, cid, future_ts)
        replay = x402.verify_challenge(
            cid, res["tx_hash"],
            replay_signature=sig, replay_ts_ms=future_ts)
        assert replay == {"ok": False, "error": "replay_auth_failed"}
        assert "access_token" not in replay

    def test_malformed_signature_rejected(self, x402_db, monkeypatch):
        payer = Account.create()
        res = _fulfill(x402_db, monkeypatch, payer.address, tx_seed="f3")
        replay = x402.verify_challenge(
            res["challenge_id"], res["tx_hash"],
            replay_signature="not-a-signature", replay_ts_ms=_now_ms())
        assert replay == {"ok": False, "error": "replay_auth_failed"}


# ---------------------------------------------------------------------------
# 7. W-102 vector must not regress: attacker replays the VICTIM's tx_hash
#    under the attacker's OWN challenge_id -> still tx_hash_already_used
# ---------------------------------------------------------------------------

class TestW102VectorStillBlocked:
    def test_victim_tx_reused_under_attacker_challenge_blocked(
            self, x402_db, monkeypatch):
        victim = Account.create()
        vres = _fulfill(x402_db, monkeypatch, victim.address, tx_seed="77")
        victim_tx = vres["tx_hash"]

        attacker = Account.create()
        ach = x402.create_challenge("agent_task", payer_wallet=attacker.address)
        _mock_onchain_payer(monkeypatch, attacker.address)
        attack = x402.verify_challenge(
            ach["challenge_id"], victim_tx, payer_wallet=attacker.address)
        assert attack["ok"] is False
        assert attack["error"] == "tx_hash_already_used"

    def test_unauthenticated_replay_on_victim_challenge_blocked(
            self, x402_db, monkeypatch):
        """The original W-103 exploit: attacker knows the victim's
        challenge_id and calls verify with NO signature — must no longer
        receive the victim's live token."""
        victim = Account.create()
        vres = _fulfill(x402_db, monkeypatch, victim.address, tx_seed="78")
        attack = x402.verify_challenge(vres["challenge_id"], vres["tx_hash"])
        assert attack == {"ok": False, "error": "replay_auth_failed"}
        assert "access_token" not in attack


# ---------------------------------------------------------------------------
# 8. Payer's own concurrent second verify with a valid signature succeeds
# ---------------------------------------------------------------------------

class TestNoSelfDos:
    def test_concurrent_valid_replays_all_succeed(
            self, x402_db, monkeypatch):
        payer = Account.create()
        res = _fulfill(x402_db, monkeypatch, payer.address, tx_seed="91")
        cid = res["challenge_id"]
        token = res["access_token"]
        ts = _now_ms()
        sig = _sign(payer, cid, ts)

        results = []
        def _try():
            results.append(x402.verify_challenge(
                cid, res["tx_hash"],
                replay_signature=sig, replay_ts_ms=ts))

        threads = [threading.Thread(target=_try) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 4, results
        assert all(r["ok"] is True for r in results), results
        assert all(r["status"] == "already_fulfilled" for r in results)
        assert all(r["access_token"] == token for r in results)


# ---------------------------------------------------------------------------
# Route: /api/x402/verify — pass-through, sanitization, HTTP status
# ---------------------------------------------------------------------------

@pytest.fixture()
def billing_client():
    """Minimal Flask app with ONLY the mvp billing blueprint registered.

    The repo's shared ``client`` fixture builds ``sincor2.app.create_app()``,
    which does not register the mvp billing blueprint — the /api/x402/verify
    route 404s there. This fixture registers the real blueprint (the real
    ``x402_verify`` view, including its sanitization and HTTP-status logic).

    NOTE: ``sincor2.mvp_app`` must be imported BEFORE the billing module:
    billing's ``_bind_mvp()`` copies mvp_app globals at import time, and on
    origin/main importing billing first triggers a circular re-registration
    AssertionError (pre-existing import-order quirk, unrelated to W-103).
    """
    import sincor2.mvp_app  # noqa: F401  (import order matters; see NOTE)
    from flask import Flask
    from sincor2.mvp_blueprints import billing as billing_mod
    app = Flask("x402_replay_auth_test")
    app.register_blueprint(billing_mod.bp)
    app.config.update(TESTING=True)
    return app.test_client()


class TestVerifyRoute:
    def test_route_reissues_with_valid_signature(self, billing_client, x402_db,
                                                 monkeypatch):
        payer = Account.create()
        res = _fulfill(x402_db, monkeypatch, payer.address, tx_seed="r1")
        ts = _now_ms()
        sig = _sign(payer, res["challenge_id"], ts)
        resp = billing_client.post("/api/x402/verify", json={
            "challenge_id": res["challenge_id"],
            "tx_hash": res["tx_hash"],
            "replay_signature": sig,
            "replay_ts_ms": str(ts),
        })
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["ok"] is True
        assert body["status"] == "already_fulfilled"
        assert body["access_token"] == res["access_token"]

    def test_route_accepts_numeric_ts_ms(self, billing_client, x402_db,
                                         monkeypatch):
        """replay_ts_ms as a JSON number (not just a string) works."""
        payer = Account.create()
        res = _fulfill(x402_db, monkeypatch, payer.address, tx_seed="r3")
        ts = _now_ms()
        sig = _sign(payer, res["challenge_id"], ts)
        resp = billing_client.post("/api/x402/verify", json={
            "challenge_id": res["challenge_id"],
            "tx_hash": res["tx_hash"],
            "replay_signature": sig,
            "replay_ts_ms": ts,
        })
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["access_token"] == res["access_token"]

    def test_route_rejects_replay_without_signature(self, billing_client,
                                                    x402_db, monkeypatch):
        payer = Account.create()
        res = _fulfill(x402_db, monkeypatch, payer.address, tx_seed="r2")
        resp = billing_client.post("/api/x402/verify", json={
            "challenge_id": res["challenge_id"],
            "tx_hash": res["tx_hash"],
        })
        assert resp.status_code == 400, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body == {"ok": False, "error": "replay_auth_failed"}
        assert "access_token" not in body
