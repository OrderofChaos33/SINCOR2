"""W-102 round 3 — creation-time wallet-ownership proof (money path).

Red-team finding: create_challenge() accepted ANY payer_wallet string with
no ownership proof. The r3 PoC minted a challenge NAMING THE VICTIM'S
WALLET, watched the public treasury for the victim's payment, and
front-ran /api/x402/verify with the victim's real tx_hash. Binding check
passed (recorded == supplied == victim), the on-chain payer check passed
(the victim really paid), and UNIQUE(tx_hash) first-claim-wins handed the
attacker an entitlement access_token — the victim's own verify then failed
tx_hash_already_used. Cost to attempt: zero.

Fix under test (wired end-to-end through the helpers AND the Flask routes):
creation is two-phase — create_challenge() mints an UNBOUND draft (no
payer), and bind_challenge_payer() writes the binding only after
verifying an EIP-191 personal_sign signature by the payer over
(challenge_id | resource_id | payer_wallet), with require_low_s
canonicality. A bare address string is no longer a capability.

Full-loop scenarios:
  (a) r3 attack replay: attacker mints a draft, tries to bind the VICTIM's
      wallet without the victim's key -> rejected; the attacker's
      front-run verify on the unbound draft -> challenge_unbound, NO
      access_token issued; the victim's own signed flow still fulfills.
  (b) legit flow: owner signs at creation, pays on-chain, verifies ->
      fulfills normally with an access_token.
  (c) unsigned payer_wallet binding at creation -> rejected; the draft
      stays unbound and unredeemable.
"""
from __future__ import annotations

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

# Production import order matters: mvp_app must be imported before the
# billing blueprint so _bind_mvp() binds against the initializing module.
import sincor2.mvp_app  # noqa: F401  (imported for binding side effects)
from sincor2.mvp_blueprints import billing

import sincor2.x402_payments as x402
from sincor2.sig_canonical import is_low_s


def _make_key() -> Account:
    while True:
        acct = Account.create()
        if is_low_s(acct.sign_message(
                encode_defunct(text="sincor-w102r3-probe")).signature):
            return acct


VICTIM_KEY = _make_key()
ATTACKER_KEY = _make_key()
VICTIM = VICTIM_KEY.address
ATTACKER = ATTACKER_KEY.address


def _tx(seed: str = "aa") -> str:
    return "0x" + seed * 32


def _sig(acct: Account, challenge_id: str,
         resource_id: str = "agent_task") -> str:
    msg = x402.challenge_bind_message(challenge_id, resource_id, acct.address)
    return acct.sign_message(encode_defunct(text=msg)).signature.hex()


def _high_s_sig(acct: Account, challenge_id: str) -> str:
    """Malleability twin: valid signature with s flipped to n-s (high-s)."""
    from sincor2.sig_canonical import SECP256K1_N
    raw = bytes.fromhex(_sig(acct, challenge_id)[2:])
    r, v = raw[:32], raw[64:]
    s = int.from_bytes(raw[32:64], "big")
    twin = r + ((SECP256K1_N - s).to_bytes(32, "big")) + v
    assert not is_low_s(twin)
    return "0x" + twin.hex()


@pytest.fixture()
def x402_db(tmp_path, monkeypatch):
    db = tmp_path / "orders.db"
    monkeypatch.setenv("ORDERS_DB_PATH", str(db))
    x402.init_x402_db()
    return db


@pytest.fixture()
def mock_onchain(monkeypatch):
    """On-chain treasury transfer pays from VICTIM's wallet."""
    def _ok(tx_hash, **kwargs):
        supplied = (kwargs.get("payer_wallet") or "").lower()
        if supplied and supplied != VICTIM.lower():
            return {"ok": False, "error": "payer_wallet_mismatch"}
        return {"ok": True, "payer_wallet": VICTIM}

    monkeypatch.setattr(x402, "verify_treasury_transfer", _ok)
    monkeypatch.setattr(
        x402, "record_platform_payment",
        lambda **kw: {"payment_id": kw.get("payment_id"), "ok": True})
    monkeypatch.setattr(x402, "record_inflow", lambda *a, **k: {"ok": True})


@pytest.fixture()
def client():
    app = Flask(__name__)
    app.register_blueprint(billing.bp)
    app.config.update(TESTING=True)
    return app.test_client()


def _legit_bind(challenge_id: str, acct: Account,
                resource_id: str = "agent_task") -> dict:
    bound = x402.bind_challenge_payer(
        challenge_id, acct.address, _sig(acct, challenge_id, resource_id))
    assert bound.get("ownership_proven") is True, bound
    return bound


# ---------------------------------------------------------------------------
# (c) unsigned / forged bindings are rejected at creation
# ---------------------------------------------------------------------------

class TestUnsignedBindingRejected:
    def test_draft_records_no_payer(self, x402_db):
        """create_challenge mints an unbound draft — no binding, no sig."""
        draft = x402.create_challenge("agent_task")
        assert draft["error"] == "ownership_proof_required"
        with x402._conn() as conn:
            row = conn.execute(
                "SELECT payer_wallet, status FROM x402_challenges "
                "WHERE challenge_id=?",
                (draft["challenge_id"],)).fetchone()
        assert row["payer_wallet"] == ""
        assert row["status"] == "pending"

    def test_bind_without_signature_rejected(self, x402_db):
        draft = x402.create_challenge("agent_task")
        res = x402.bind_challenge_payer(
            draft["challenge_id"], VICTIM, "")
        assert res == {"ok": False, "error": "ownership_signature_required"}

    def test_bind_with_garbage_signature_rejected(self, x402_db):
        draft = x402.create_challenge("agent_task")
        res = x402.bind_challenge_payer(
            draft["challenge_id"], VICTIM, "0xdeadbeef")
        assert res == {"ok": False, "error": "ownership_proof_failed"}

    def test_bind_with_high_s_signature_rejected(self, x402_db):
        """Signature malleability twin: recovers to the same address but is
        non-canonical — the codebase standard rejects it."""
        draft = x402.create_challenge("agent_task")
        res = x402.bind_challenge_payer(
            draft["challenge_id"], VICTIM,
            _high_s_sig(VICTIM_KEY, draft["challenge_id"]))
        assert res == {"ok": False, "error": "ownership_proof_failed"}

    def test_bind_wrong_signer_rejected(self, x402_db):
        """Attacker signs the victim's bind message with the ATTACKER key:
        recovered address != claimed payer."""
        draft = x402.create_challenge("agent_task")
        attacker_sig = _sig(ATTACKER_KEY, draft["challenge_id"])
        # Sanity: the attacker's own signature verifies for the attacker.
        assert x402._recover_bind_signer(
            x402.challenge_bind_message(
                draft["challenge_id"], "agent_task", ATTACKER),
            attacker_sig).lower() == ATTACKER.lower()
        res = x402.bind_challenge_payer(
            draft["challenge_id"], VICTIM, attacker_sig)
        assert res == {"ok": False, "error": "ownership_proof_failed"}

    def test_bind_signature_for_other_challenge_rejected(self, x402_db):
        """The victim's signature is bound to THEIR challenge_id: replaying
        it onto the attacker's draft fails verification."""
        victim_draft = x402.create_challenge("agent_task")
        _legit_bind(victim_draft["challenge_id"], VICTIM_KEY)
        attacker_draft = x402.create_challenge("agent_task")
        victim_sig = _sig(VICTIM_KEY, victim_draft["challenge_id"])
        res = x402.bind_challenge_payer(
            attacker_draft["challenge_id"], VICTIM, victim_sig)
        assert res == {"ok": False, "error": "ownership_proof_failed"}

    def test_bind_signature_for_other_resource_rejected(self, x402_db):
        draft = x402.create_challenge("agent_task")
        sig_other_resource = _sig(
            VICTIM_KEY, draft["challenge_id"], resource_id="hook_status")
        res = x402.bind_challenge_payer(
            draft["challenge_id"], VICTIM, sig_other_resource)
        assert res == {"ok": False, "error": "ownership_proof_failed"}

    def test_bind_twice_rejected(self, x402_db):
        """A draft binds exactly once — no rebinding to another wallet."""
        draft = x402.create_challenge("agent_task")
        _legit_bind(draft["challenge_id"], VICTIM_KEY)
        res = x402.bind_challenge_payer(
            draft["challenge_id"], ATTACKER,
            _sig(ATTACKER_KEY, draft["challenge_id"]))
        assert res == {"ok": False, "error": "challenge_already_bound"}
        with x402._conn() as conn:
            row = conn.execute(
                "SELECT payer_wallet FROM x402_challenges WHERE challenge_id=?",
                (draft["challenge_id"],)).fetchone()
        assert row["payer_wallet"] == VICTIM.lower()

    def test_bind_unknown_challenge_rejected(self, x402_db):
        res = x402.bind_challenge_payer(
            "x402-doesnotexist", VICTIM, "0x" + "ab" * 65)
        assert res == {"ok": False, "error": "challenge_not_found"}


# ---------------------------------------------------------------------------
# (b) legit full loop: sign at creation -> pay -> verify -> fulfilled
# ---------------------------------------------------------------------------

class TestLegitFlowFulfilled:
    def test_full_loop_helper_level(self, x402_db, mock_onchain):
        draft = x402.create_challenge("agent_task")
        bound = _legit_bind(draft["challenge_id"], VICTIM_KEY)
        assert bound["error"] == "payment_required"
        assert bound["payer_wallet"] == VICTIM.lower()
        res = x402.verify_challenge(
            draft["challenge_id"], _tx("aa"), payer_wallet=VICTIM,
            require_bound_challenge=True)
        assert res["ok"] is True
        assert res["status"] == "fulfilled"
        assert res["access_token"]
        assert res["payer_wallet"].lower() == VICTIM.lower()

    def test_full_loop_route_level(self, client, x402_db, mock_onchain):
        """End-to-end through the HTTP routes: GET draft -> POST bind ->
        POST verify -> 200 fulfilled."""
        r = client.get(f"/x402/agent_task?wallet={VICTIM}")
        assert r.status_code == 402
        draft = r.get_json()
        assert draft["error"] == "ownership_proof_required"
        b = client.post("/api/x402/challenge-bind", json={
            "challenge_id": draft["challenge_id"],
            "payer_wallet": VICTIM,
            "signature": _sig(VICTIM_KEY, draft["challenge_id"]),
        })
        assert b.status_code == 402
        assert b.get_json()["ownership_proven"] is True
        v = client.post("/api/x402/verify", json={
            "challenge_id": draft["challenge_id"],
            "tx_hash": _tx("bb"),
            "payer_wallet": VICTIM,
        })
        assert v.status_code == 200
        body = v.get_json()
        assert body["ok"] is True
        assert body["status"] == "fulfilled"
        assert body["access_token"]


# ---------------------------------------------------------------------------
# (a) r3 attack replay: attacker cannot mint a victim-bound challenge
# ---------------------------------------------------------------------------

class TestR3AttackKilled:
    def test_attacker_cannot_bind_victim_wallet(self, x402_db, mock_onchain):
        """The r3 PoC step 2 — minting a challenge naming the victim's
        wallet — is impossible: binding requires the victim's signature."""
        attacker_draft = x402.create_challenge("agent_task")
        # Attacker's best forgery: their own signature, victim's address.
        res = x402.bind_challenge_payer(
            attacker_draft["challenge_id"], VICTIM,
            _sig(ATTACKER_KEY, attacker_draft["challenge_id"]))
        assert res == {"ok": False, "error": "ownership_proof_failed"}

    def test_frontrun_verify_on_unbound_draft_fails(
            self, client, x402_db, mock_onchain):
        """Red-team PoC replay at the route: the attacker mints a draft,
        the victim pays on-chain, the attacker front-runs verify with the
        victim's tx_hash naming the victim's wallet. The draft is unbound
        -> challenge_unbound, NO access_token; the victim's own signed
        challenge then fulfills normally on the same tx."""
        tx = _tx("cc")
        # Attacker mints a draft (the most they can do without the key).
        attacker_draft = x402.create_challenge("agent_task")
        # Victim runs the legit flow on their own draft.
        victim_draft = x402.create_challenge("agent_task")
        _legit_bind(victim_draft["challenge_id"], VICTIM_KEY)
        # Attacker front-runs: victim's tx_hash, victim's wallet, their draft.
        r = client.post("/api/x402/verify", json={
            "challenge_id": attacker_draft["challenge_id"],
            "tx_hash": tx,
            "payer_wallet": VICTIM,
        })
        assert r.status_code == 400
        body = r.get_json()
        assert body["ok"] is False
        assert body["error"] == "challenge_unbound"
        assert not body.get("access_token")
        # The attacker's draft is not fulfilled and the tx is unclaimed.
        with x402._conn() as conn:
            row = conn.execute(
                "SELECT status, tx_hash FROM x402_challenges "
                "WHERE challenge_id=?",
                (attacker_draft["challenge_id"],)).fetchone()
        assert row["status"] == "pending"
        assert row["tx_hash"] is None
        # Victim verifies afterwards: fulfills normally, gets the token.
        v = client.post("/api/x402/verify", json={
            "challenge_id": victim_draft["challenge_id"],
            "tx_hash": tx,
            "payer_wallet": VICTIM,
        })
        assert v.status_code == 200
        vbody = v.get_json()
        assert vbody["ok"] is True
        assert vbody["status"] == "fulfilled"
        assert vbody["access_token"]
        # And the attacker's late replay of the same tx is now double-claim.
        r2 = client.post("/api/x402/verify", json={
            "challenge_id": attacker_draft["challenge_id"],
            "tx_hash": tx,
            "payer_wallet": VICTIM,
        })
        assert r2.get_json()["error"] in (
            "challenge_unbound", "tx_hash_already_used")

    def test_bind_route_rejects_wrong_signer(self, client, x402_db):
        """Route-level: bind with a signature from a different key than the
        claimed payer -> 403, row stays unbound."""
        draft = client.get(f"/x402/agent_task?wallet={VICTIM}").get_json()
        r = client.post("/api/x402/challenge-bind", json={
            "challenge_id": draft["challenge_id"],
            "payer_wallet": VICTIM,
            "signature": _sig(ATTACKER_KEY, draft["challenge_id"]),
        })
        assert r.status_code == 403
        assert r.get_json()["error"] == "ownership_proof_failed"
