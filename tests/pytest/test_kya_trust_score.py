"""KYA Trust Score (G2) — unit + full-loop x402 integration tests.

Unit layer proves the score is computed ONLY from real records in the
three named stores, seeded through those stores' own public APIs:

- KYA registry (``sincor2.kya.registry.KYARegistry``): list -> bind ->
  apply_stake -> verify.
- Stake ledger (``sincor2.onchain.stake_ledger.StakeLedger``):
  deposit + ``record_completion`` calls.
- Settlement ledger (``sincor2.a2a_integration.ReputationLedger``):
  ``record`` settlement rows.

Hand-computed expectation for the seeded agent (see ``stores``
fixture): identity verified = 30; KYA stake == MIN_STAKE_WEI = 10;
execution R_i = min(1, 25/50) * min(1, 3/3) * min(1, 90d/90d) * 0.5^0
= 0.5 -> round(40 * 0.5) = 20; settlements 5 -> min(20, 2*5) = 10.
Total = 70, tier "established". An agent with no record anywhere is
the honest ``unknown`` (score None), never a fabricated number.

Integration layer proves the complete first-dollar loop through the
real Flask routes (same mock pattern as test_sanctions_screen.py):
402 -> EIP-191 bound challenge -> verify/settle -> single-use token
-> deep read delivered -> tx_hash replay rejected -> token replay
rejected -> unbound challenge rejected -> underpayment rejected.
The free verify route returns tier-level fields only — the score and
its components are paid fields and must never leak there.
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

import sincor2.kya_trust_score as kts
import sincor2.x402_payments as x402
from sincor2.a2a_integration import ReputationLedger
from sincor2.kya.registry import KYARegistry, MIN_STAKE_WEI
from sincor2.onchain.stake_ledger import StakeLedger
from sincor2.sig_canonical import is_low_s

RESOURCE = "kya-trust-score"
AGENT = "agent-veteran"
WALLET = "0x1111111111111111111111111111111111111111"
ONE_AXM = 10**18
DAY = 86400


def _make_key() -> Account:
    while True:
        acct = Account.create()
        probe = acct.sign_message(encode_defunct(text="sincor-low-s-probe"))
        if is_low_s(probe.signature):
            return acct


BUYER_KEY = _make_key()
BUYER = BUYER_KEY.address


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    """Seed the three real stores and wire the module's loader seams
    to them. Returns the store objects + the fixed scoring instant."""
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "kya-data"))
    reg = KYARegistry()
    rec = reg.list_agent({"agent_id": AGENT, "wallet": WALLET})
    reg.bind(AGENT, WALLET)
    reg.apply_stake(rec["kya_id"], str(MIN_STAKE_WEI), "0xstake")
    reg.verify(rec["kya_id"])

    ledger = StakeLedger(path=str(tmp_path / "stake.json"))
    ledger.deposit(AGENT, 20 * ONE_AXM)
    t0 = int(time.time()) - 90 * DAY
    posters = ["poster-1", "poster-2", "poster-3"]
    ledger.record_completion(AGENT, "task-0", poster_id=posters[0], at=t0)
    for i in range(1, 25):
        ledger.record_completion(
            AGENT, f"task-{i}", poster_id=posters[i % 3], at=t0 + 89 * DAY)

    rep = ReputationLedger(db_path=str(tmp_path / "rep.db"))
    for i in range(5):
        rep.record("wallet:" + WALLET, "skill-x", f"settle-{i}", ONE_AXM)

    monkeypatch.setattr(
        kts, "load_kya_record",
        lambda agent_id=None, wallet=None: reg.lookup(
            agent_id=agent_id, wallet=wallet))
    monkeypatch.setattr(
        kts, "load_stake_view",
        lambda agent_id, now=None: kts.stake_view_from_ledger(
            ledger, agent_id, now=now))
    monkeypatch.setattr(
        kts, "load_settlement_count",
        lambda agent_id=None, wallet=None: sum(
            rep.score(k) for k in kts.settlement_keys(agent_id, wallet)))
    return {"reg": reg, "ledger": ledger, "rep": rep,
            "t0": t0, "now": t0 + 90 * DAY, "kya_id": rec["kya_id"]}


def _deep(stores, agent_id=AGENT, wallet=None):
    reg, ledger, rep = stores["reg"], stores["ledger"], stores["rep"]
    kya_record = reg.lookup(agent_id=agent_id, wallet=wallet)
    eff_agent = agent_id or (kya_record or {}).get("agent_id")
    eff_wallet = wallet or (kya_record or {}).get("wallet")
    view = kts.stake_view_from_ledger(ledger, eff_agent, now=stores["now"])
    settlements = sum(
        rep.score(k) for k in kts.settlement_keys(eff_agent, eff_wallet))
    return kts.compute_trust_score(
        agent_id=eff_agent, wallet=eff_wallet, kya_record=kya_record,
        stake_view=view, settlements=settlements)


# ---------------------------------------------------------------------------
# Unit: score traces to real records, hand-computed
# ---------------------------------------------------------------------------

class TestComputeFromRealRecords:
    def test_stake_view_counts_are_the_seeded_records(self, stores):
        view = kts.stake_view_from_ledger(
            stores["ledger"], AGENT, now=stores["now"])
        assert view["completions"] == 25
        assert view["distinct_posters"] == 3
        assert view["upheld_disputes"] == 0
        assert view["tenure_days"] == 90.0
        assert view["r_i"] == 0.5  # ledger's own reputation formula
        assert view["deposited_wei"] == str(20 * ONE_AXM)
        assert view["slashed_wei"] == "0"

    def test_seeded_agent_matches_hand_computed_score(self, stores):
        body = _deep(stores)
        assert body["status"] == "scored"
        assert body["score"] == 70  # 30 + 10 + 20 + 10, see module doc
        assert body["tier"] == "established"
        c = body["components"]
        assert c["identity"]["points"] == 30
        assert c["identity"]["status"] == "verified"
        assert c["identity"]["verified"] is True
        assert c["identity"]["kya_id"] == stores["kya_id"]
        assert c["kya_stake"]["points"] == 10
        assert c["kya_stake"]["stake_axm_wei"] == str(MIN_STAKE_WEI)
        assert c["execution"]["points"] == 20
        assert c["execution"]["r_i"] == 0.5
        assert c["execution"]["completions"] == 25
        assert c["execution"]["distinct_posters"] == 3
        assert c["settlements"]["points"] == 10
        assert c["settlements"]["count"] == 5
        assert body["scope"] == "sincor_registry_only"

    def test_unknown_agent_is_honest_unknown(self, stores):
        body = _deep(stores, agent_id="agent-nobody",
                     wallet="0x9999999999999999999999999999999999999999")
        assert body["status"] == "unknown"
        assert body["score"] is None
        assert body["tier"] == "unknown"
        c = body["components"]
        assert c["identity"]["points"] == 0
        assert c["execution"]["completions"] == 0
        assert c["settlements"]["count"] == 0

    def test_fresh_listed_agent_scores_materially_lower(self, stores):
        stores["reg"].list_agent(
            {"agent_id": "agent-fresh",
             "wallet": "0x2222222222222222222222222222222222222222"})
        body = _deep(stores, agent_id="agent-fresh")
        assert body["status"] == "scored"
        assert body["score"] == 10  # listed identity only, nothing else
        assert body["tier"] == "low"
        assert body["score"] < _deep(stores)["score"]

    def test_revoked_agent_flagged_and_identity_zeroed(self, stores):
        reg = stores["reg"]
        kid = stores["kya_id"]
        # Tombstone state as written by sincor2.kya_registry.revoke().
        reg.records[kid]["revoked"] = True
        reg.records[kid]["status"] = "revoked"
        body = _deep(stores)
        assert body["status"] == "revoked"
        assert body["tier"] == "revoked"
        assert body["components"]["identity"]["points"] == 0
        assert body["components"]["identity"]["revoked"] is True

    def test_free_verify_never_leaks_paid_fields(self, stores):
        body = kts.verify_agent(agent_id=AGENT)
        assert body["known"] is True
        assert body["status"] == "scored"
        assert body["verified"] is True
        assert body["tier"] == "established"
        assert "score" not in body
        assert "components" not in body

    def test_free_verify_unknown_agent(self, stores):
        body = kts.verify_agent(agent_id="agent-nobody")
        assert body["known"] is False
        assert body["status"] == "unknown"
        assert body["tier"] == "unknown"
        assert "score" not in body

    def test_price_registration(self):
        res = x402.get_resource(RESOURCE)
        assert res is not None
        assert res["token"] == "USDC"
        assert res["amount_usdc"] == 0.015
        # USDC has 6 decimals: $0.015 == 15000 atomic units.
        assert res["amount_atomic"] == "15000"
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
    # Price is server-derived: the 402 demands exactly $0.015 USDC.
    assert ch["payment"]["maxAmountRequired"] == "15000", ch
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


def _serve(client, access_token: str, payload: dict):
    return client.post(
        f"/api/paid/{RESOURCE}",
        headers={"X-Payment-Token": access_token},
        json=payload,
    )


class TestFullLoop:
    def test_paid_settle_delivers_hand_computed_score(
            self, client, x402_db, mock_onchain, stores):
        paid = _pay(client, _tx("b1"))
        r = _serve(client, paid["access_token"], {"agent_id": AGENT})
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["score"] == 70
        assert body["tier"] == "established"
        assert body["components"]["execution"]["completions"] == 25
        assert body["components"]["settlements"]["count"] == 5

    def test_paid_settle_by_wallet_resolves_same_agent(
            self, client, x402_db, mock_onchain, stores):
        paid = _pay(client, _tx("b2"))
        r = _serve(client, paid["access_token"], {"wallet": WALLET})
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["agent_id"] == AGENT
        assert body["score"] == 70

    def test_paid_unknown_agent_honest_unknown(
            self, client, x402_db, mock_onchain, stores):
        paid = _pay(client, _tx("b3"))
        r = _serve(client, paid["access_token"],
                   {"agent_id": "agent-nobody"})
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["status"] == "unknown"
        assert body["score"] is None

    def test_free_verify_route_no_paid_fields(
            self, client, x402_db, mock_onchain, stores):
        r = client.get(f"/api/kya/verify?agent_id={AGENT}")
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["known"] is True
        assert body["tier"] == "established"
        assert "score" not in body
        assert "components" not in body

    def test_free_verify_route_requires_identifier(
            self, client, x402_db, mock_onchain, stores):
        r = client.get("/api/kya/verify")
        assert r.status_code == 400

    def test_unpaid_serve_rejected(
            self, client, x402_db, mock_onchain, stores):
        r = client.post(f"/api/paid/{RESOURCE}?wallet={BUYER}",
                        json={"agent_id": AGENT})
        assert r.status_code == 402
        assert "score" not in (r.get_json() or {})

    def test_tx_hash_replay_rejected(
            self, client, x402_db, mock_onchain, stores):
        tx = _tx("b4")
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
            self, client, x402_db, mock_onchain, stores):
        paid = _pay(client, _tx("b5"))
        r1 = _serve(client, paid["access_token"], {"agent_id": AGENT})
        assert r1.status_code == 200
        r2 = _serve(client, paid["access_token"], {"agent_id": AGENT})
        assert r2.status_code != 200
        assert "score" not in (r2.get_json() or {})

    def test_unbound_challenge_cannot_settle(
            self, client, x402_db, mock_onchain, stores):
        draft = x402.create_challenge(RESOURCE)
        r = client.post("/api/x402/verify", json={
            "challenge_id": draft["challenge_id"],
            "tx_hash": _tx("b6"),
            "payer_wallet": BUYER,
        })
        assert r.status_code == 400, r.get_json()
        assert r.get_json()["error"] == "challenge_unbound"

    def test_underpayment_rejected(
            self, client, x402_db, mock_onchain, stores, paid_amount):
        paid_amount["atomic"] = 1000  # $0.001 against a $0.015 price
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

    def test_missing_identifier_after_payment_is_400(
            self, client, x402_db, mock_onchain, stores):
        paid = _pay(client, _tx("b8"))
        r = _serve(client, paid["access_token"], {})
        assert r.status_code == 400
        assert r.get_json()["error"] == "agent_id_or_wallet_required"
        # P1-1: the 400 must NOT burn the single-use token — the
        # same token still delivers once the payload is valid.
        r2 = _serve(client, paid["access_token"], {"agent_id": AGENT})
        assert r2.status_code == 200, r2.get_json()
        assert r2.get_json()["score"] == 70
