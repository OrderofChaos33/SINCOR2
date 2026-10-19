"""W-102 regression: x402 front-run claim grief on POST /api/x402/verify.

Attack (red-team, money path): POST /api/x402/verify accepted an OPTIONAL
payer_wallet. An observer of a confirmed treasury transfer could submit the
victim's tx_hash under their OWN challenge_id before the victim's verify
landed; first-claim-wins (atomic UPDATE) granted the attacker the victim's
entitlement.

Fix under test (wired, end-to-end through the Flask route):
  1. The route rejects a missing/blank/malformed payer_wallet with 400
     (error: payer_wallet_required).
  2. verify_challenge binds the claim to the challenge's recorded payer
     (stored at create_challenge time): a claim naming any other wallet —
     or no wallet on a bound challenge — is rejected with
     challenge_payer_mismatch, before any on-chain call.
  3. The on-chain payer must still match the supplied wallet
     (pre-existing payer_wallet_mismatch in verify_treasury_transfer).

Mock pattern reuses tests/pytest/test_auditp1_payments.py: monkeypatch
x402.verify_treasury_transfer with the same semantics (ok + on-chain payer,
payer_wallet_mismatch when a supplied wallet disagrees).
"""
from __future__ import annotations

import pytest
from flask import Flask

# Production import order matters: mvp_app must be imported before the
# billing blueprint so _bind_mvp() binds against the initializing module
# (otherwise route decoration happens on an already-registered blueprint).
import sincor2.mvp_app  # noqa: F401  (imported for binding side effects)
from sincor2.mvp_blueprints import billing

import sincor2.x402_payments as x402

VICTIM = "0x" + "ab" * 20
ATTACKER = "0x" + "cd" * 20


def _tx(seed: str = "aa") -> str:
    return "0x" + seed * 32


@pytest.fixture()
def x402_db(tmp_path, monkeypatch):
    """Throwaway x402 SQLite store (same pattern as test_auditp1_payments)."""
    db = tmp_path / "orders.db"
    monkeypatch.setenv("ORDERS_DB_PATH", str(db))
    x402.init_x402_db()
    return db


@pytest.fixture()
def mock_onchain(monkeypatch):
    """On-chain treasury transfer pays from VICTIM's wallet; the payer check
    mirrors the real verify_treasury_transfer semantics."""
    def _ok(tx_hash, **kwargs):
        supplied = (kwargs.get("payer_wallet") or "").lower()
        if supplied and supplied != VICTIM.lower():
            return {"ok": False, "error": "payer_wallet_mismatch"}
        return {"ok": True, "payer_wallet": VICTIM}

    monkeypatch.setattr(x402, "verify_treasury_transfer", _ok)
    # Keep the test hermetic: payment finalization must not touch real
    # platform/treasury stores.
    monkeypatch.setattr(
        x402, "record_platform_payment",
        lambda **kw: {"payment_id": kw.get("payment_id"), "ok": True})
    monkeypatch.setattr(
        x402, "record_inflow",
        lambda *a, **k: {"ok": True})


@pytest.fixture()
def client():
    app = Flask(__name__)
    app.register_blueprint(billing.bp)
    app.config.update(TESTING=True)
    return app.test_client()


def _verify(client, challenge_id, tx_hash, payer_wallet="__unset__"):
    payload = {"challenge_id": challenge_id, "tx_hash": tx_hash}
    if payer_wallet != "__unset__":
        payload["payer_wallet"] = payer_wallet
    return client.post("/api/x402/verify", json=payload)


class TestPayerWalletRequired:
    def test_missing_wallet_rejected_400(self, client, x402_db, mock_onchain):
        ch = x402.create_challenge("agent_task", payer_wallet=VICTIM)
        r = _verify(client, ch["challenge_id"], _tx())
        assert r.status_code == 400
        assert r.get_json()["error"] == "payer_wallet_required"

    def test_blank_wallet_rejected_400(self, client, x402_db, mock_onchain):
        ch = x402.create_challenge("agent_task", payer_wallet=VICTIM)
        r = _verify(client, ch["challenge_id"], _tx(), payer_wallet="   ")
        assert r.status_code == 400
        assert r.get_json()["error"] == "payer_wallet_required"

    def test_malformed_wallet_rejected_400(self, client, x402_db, mock_onchain):
        ch = x402.create_challenge("agent_task", payer_wallet=VICTIM)
        r = _verify(client, ch["challenge_id"], _tx(), payer_wallet="not-a-wallet")
        assert r.status_code == 400
        assert r.get_json()["error"] == "payer_wallet_required"


class TestFrontRunKilled:
    def test_attacker_challenge_with_victim_tx_rejected(
            self, client, x402_db, mock_onchain):
        """The red-team PoC: attacker's own challenge_id + victim's tx_hash.
        The attacker truthfully names the victim's wallet (the on-chain
        payer) — the challenge↔payer binding must still reject it."""
        attacker_ch = x402.create_challenge("agent_task", payer_wallet=ATTACKER)
        tx = _tx("aa")
        r = _verify(client, attacker_ch["challenge_id"], tx, payer_wallet=VICTIM)
        assert r.status_code == 400
        body = r.get_json()
        assert body["ok"] is False
        assert body["error"] == "challenge_payer_mismatch"
        # The attacker's challenge is NOT fulfilled and the tx is unclaimed:
        # the victim can still claim it afterwards.
        with x402._conn() as conn:
            row = conn.execute(
                "SELECT status, tx_hash FROM x402_challenges WHERE challenge_id=?",
                (attacker_ch["challenge_id"],)).fetchone()
        assert row["status"] == "pending"
        assert row["tx_hash"] is None

    def test_attacker_challenge_with_own_wallet_rejected(
            self, client, x402_db, mock_onchain):
        """Attacker names their own wallet: the on-chain payer check rejects."""
        attacker_ch = x402.create_challenge("agent_task", payer_wallet=ATTACKER)
        r = _verify(client, attacker_ch["challenge_id"], _tx("bb"),
                    payer_wallet=ATTACKER)
        assert r.status_code == 400
        assert r.get_json()["error"] == "payer_wallet_mismatch"

    def test_victim_own_challenge_accepted(self, client, x402_db, mock_onchain):
        """Happy path: victim claims their own challenge with their wallet."""
        victim_ch = x402.create_challenge("agent_task", payer_wallet=VICTIM)
        r = _verify(client, victim_ch["challenge_id"], _tx("cc"),
                    payer_wallet=VICTIM)
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["status"] == "fulfilled"
        assert body["access_token"]
        assert body["payer_wallet"] == VICTIM.lower()

    def test_replay_after_victim_fulfill_rejected(
            self, client, x402_db, mock_onchain):
        """Once the victim's claim lands, the attacker's replay of the same
        tx_hash is rejected (pre-existing double-fulfillment guard)."""
        victim_ch = x402.create_challenge("agent_task", payer_wallet=VICTIM)
        tx = _tx("dd")
        first = _verify(client, victim_ch["challenge_id"], tx, payer_wallet=VICTIM)
        assert first.status_code == 200
        attacker_ch = x402.create_challenge("agent_task", payer_wallet=ATTACKER)
        # Attacker now names the victim's wallet (truthful on-chain payer)
        # but the challenge binding rejects before the replay check.
        r = _verify(client, attacker_ch["challenge_id"], tx, payer_wallet=VICTIM)
        assert r.status_code == 400
        assert r.get_json()["error"] == "challenge_payer_mismatch"


class TestHelperLevelBinding:
    def test_anonymous_claim_of_bound_challenge_rejected(
            self, x402_db, mock_onchain):
        """Defense in depth: direct verify_challenge callers cannot claim a
        bound challenge without naming its payer."""
        ch = x402.create_challenge("agent_task", payer_wallet=VICTIM)
        res = x402.verify_challenge(ch["challenge_id"], _tx("ee"), payer_wallet="")
        assert res == {"ok": False, "error": "challenge_payer_mismatch"}

    def test_wallet_case_insensitive_match(self, x402_db, mock_onchain):
        """Checksummed/mixed-case wallets match the lowercased record."""
        ch = x402.create_challenge("agent_task", payer_wallet=VICTIM)
        res = x402.verify_challenge(
            ch["challenge_id"], _tx("ff"), payer_wallet=VICTIM.upper())
        assert res["ok"] is True
        assert res["status"] == "fulfilled"


# ---------------------------------------------------------------------------
# W-102 round 2 — blank-recorded challenge bypass (adversarial rejection)
# ---------------------------------------------------------------------------

class TestBlankChallengeBypassKilled:
    def test_blank_challenge_claim_rejected_at_route(
            self, client, x402_db, mock_onchain):
        """The reviewer's exact PoC: a blank-recorded challenge (as the old
        public endpoints allowed) claimed via the route with the victim's
        tx_hash and the victim's wallet must be rejected — the on-chain
        payer check alone cannot stop it, because the attacker truthfully
        names the victim's wallet."""
        blank_ch = x402.create_challenge("agent_task", payer_wallet="")
        tx = _tx("aa")
        r = _verify(client, blank_ch["challenge_id"], tx, payer_wallet=VICTIM)
        assert r.status_code == 400
        body = r.get_json()
        assert body["ok"] is False
        assert body["error"] == "challenge_unbound"
        # The blank challenge is NOT fulfilled and the tx is unclaimed:
        # the victim can still claim it under their own bound challenge.
        with x402._conn() as conn:
            row = conn.execute(
                "SELECT status, tx_hash FROM x402_challenges WHERE challenge_id=?",
                (blank_ch["challenge_id"],)).fetchone()
        assert row["status"] == "pending"
        assert row["tx_hash"] is None
        victim_ch = x402.create_challenge("agent_task", payer_wallet=VICTIM)
        ok = _verify(client, victim_ch["challenge_id"], tx, payer_wallet=VICTIM)
        assert ok.status_code == 200
        assert ok.get_json()["ok"] is True

    def test_helper_blank_challenge_behavior_preserved(self, x402_db, mock_onchain):
        """Pinned blank-blank helper flows (test_auditp1_payments) bypass
        the route: direct verify_challenge calls on blank-recorded
        challenges keep the on-chain-only binding."""
        ch = x402.create_challenge("agent_task", payer_wallet="")
        res = x402.verify_challenge(ch["challenge_id"], _tx("bb"))
        assert res["ok"] is True
        assert res["status"] == "fulfilled"


class TestChallengeCreationRequiresWallet:
    def test_challenge_endpoint_rejects_missing_wallet(
            self, client, x402_db, mock_onchain):
        r = client.get("/x402/agent_task")
        assert r.status_code == 400
        assert r.get_json()["error"] == "payer_wallet_required"

    def test_challenge_endpoint_rejects_malformed_wallet(
            self, client, x402_db, mock_onchain):
        r = client.get("/x402/agent_task?wallet=not-a-wallet")
        assert r.status_code == 400
        assert r.get_json()["error"] == "payer_wallet_required"

    def test_challenge_endpoint_binds_wallet(
            self, client, x402_db, mock_onchain):
        r = client.get(f"/x402/agent_task?wallet={VICTIM}")
        assert r.status_code == 402
        body = r.get_json()
        with x402._conn() as conn:
            row = conn.execute(
                "SELECT payer_wallet FROM x402_challenges WHERE challenge_id=?",
                (body["challenge_id"],)).fetchone()
        assert row["payer_wallet"] == VICTIM.lower()

    def test_paid_fallback_rejects_missing_wallet(
            self, client, x402_db, mock_onchain):
        r = client.get("/api/paid/agent_task")
        assert r.status_code == 400
        assert r.get_json()["error"] == "payer_wallet_required"

    def test_paid_fallback_binds_wallet(
            self, client, x402_db, mock_onchain):
        r = client.get(f"/api/paid/agent_task?wallet={VICTIM}")
        assert r.status_code == 402
        body = r.get_json()
        with x402._conn() as conn:
            row = conn.execute(
                "SELECT payer_wallet FROM x402_challenges WHERE challenge_id=?",
                (body["challenge_id"],)).fetchone()
        assert row["payer_wallet"] == VICTIM.lower()
        # ...and the bound challenge is redeemable through the route.
        ok = _verify(client, body["challenge_id"], _tx("cc"),
                     payer_wallet=VICTIM)
        assert ok.status_code == 200
        assert ok.get_json()["ok"] is True
