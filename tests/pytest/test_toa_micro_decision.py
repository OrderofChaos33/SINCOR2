"""TOA Micro-Decision (G7) — unit + full-loop x402 integration tests.

The paid read wraps ``sincor2.toa_decide.toa_decide`` UNCHANGED.
Unit layer proves, against the REAL TOA machinery (no mocks of the
forecast path):

- deterministic ranking: the same payload derives the same
  decision_id (sha256 of the canonical payload) and replays to the
  identical action / recommendation / confidence / scores;
- a clear-spread fixture returns ``recommend`` whose pick is the
  argmax of TOA's own returned scores, confidence >= threshold;
- a low-spread fixture returns ``escalate`` (recommended None,
  confidence below the 0.55 threshold) — a legitimate paid result;
- hard-gated wording returns ``refused`` — also a legitimate paid
  result (the product working), served 200, never an error;
- malformed payloads are rejected 400 (strict validation: 2-5
  options, unique ids, finite numeric signals, bounded fields);
- the paid path NEVER records an outcome (no ``type: outcome`` line
  is ever written by a serve), while the outcome loop itself closes
  through the existing ``toa_record_outcome`` interface: a buyer-side
  outcome for a served decision_id lands in the decision log and is
  ingested by TOA's feedback loop;
- the price is registered: $0.05 USDC == 50000 atomic units.

Every test points TOA_DECISION_LOG at a tmp path — the repo's real
decision log is never touched.

Integration layer proves the complete first-dollar loop through the
real Flask routes (same mock pattern as test_kya_trust_score.py):
402 -> EIP-191 bound challenge -> verify/settle -> single-use token
-> decision delivered -> tx_hash replay rejected -> token replay
rejected -> unbound challenge rejected -> underpayment rejected.
"""
from __future__ import annotations

import json
import math

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

# Production import order matters: mvp_app must be imported before the
# billing blueprint (see test_x402_verify_frontrun.py).
import sincor2.mvp_app  # noqa: F401  (imported for binding side effects)
from sincor2.mvp_blueprints import billing

import sincor2.toa_micro_decision as tmd
import sincor2.x402_payments as x402
from sincor2 import toa_decide as td
from sincor2.sig_canonical import is_low_s

RESOURCE = "toa-micro-decision"

WINNER = {
    "options": [
        {"id": "strong", "label": "strong performer",
         "signals": [0.80, 0.82, 0.81, 0.83, 0.84]},
        {"id": "weak", "label": "weak performer",
         "signals": [0.20, 0.21, 0.19, 0.22, 0.20]},
    ],
    "context": {"horizon": 4},
}

CLOSE_CALL = {
    "options": [
        {"id": "a", "label": "option A",
         "signals": [0.50, 0.52, 0.51, 0.53, 0.52]},
        {"id": "b", "label": "option B",
         "signals": [0.51, 0.50, 0.52, 0.51, 0.53]},
    ],
    "context": {"horizon": 4},
}

HARD_GATED = {
    "options": [
        {"id": "x", "label": "deploy the hotfix to production",
         "signals": [0.9, 0.92, 0.95]},
        {"id": "y", "label": "wait a day", "signals": [0.4, 0.42, 0.41]},
    ],
    "context": {"horizon": 4},
}


@pytest.fixture(autouse=True)
def toa_log(tmp_path, monkeypatch):
    """Hermetic TOA decision log for every test in this file."""
    p = tmp_path / "toa_decisions.jsonl"
    monkeypatch.setenv("TOA_DECISION_LOG", str(p))
    return p


def _log_lines(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# Unit: real TOA outcomes through the serve path
# ---------------------------------------------------------------------------

class TestServeOutcomes:
    def test_clear_spread_recommends_argmax_of_toa_scores(self):
        status, body = tmd.serve_toa_micro_decision(WINNER)
        assert status == 200
        assert body["ok"] is True
        assert body["resource"] == RESOURCE
        assert body["action"] == "recommend"
        assert body["recommended"] in ("strong", "weak")
        # the pick is TOA's own argmax — the wrapper adds no ranking
        assert body["recommended"] == max(
            body["scores"], key=lambda k: body["scores"][k])
        assert body["confidence"] >= body["threshold"]
        assert body["threshold"] == pytest.approx(0.55)
        for oid in ("strong", "weak"):
            assert body["forecast_detail"][oid]["forecast_paths"] > 0
            assert body["forecast_detail"][oid]["evaluated_paths"] > 0

    def test_deterministic_ranking_same_input_same_output(self):
        s1, b1 = tmd.serve_toa_micro_decision(WINNER)
        s2, b2 = tmd.serve_toa_micro_decision(WINNER)
        assert s1 == s2 == 200
        # derived decision_id is a pure function of the payload
        assert b1["decision_id"] == b2["decision_id"]
        assert b1["decision_id"].startswith("toa-micro-")
        assert b1["action"] == b2["action"]
        assert b1["recommended"] == b2["recommended"]
        assert b1["confidence"] == b2["confidence"]
        assert b1["scores"] == b2["scores"]

    def test_supplied_decision_id_used_verbatim(self):
        payload = dict(WINNER, decision_id="buyer-decision-42")
        status, body = tmd.serve_toa_micro_decision(payload)
        assert status == 200
        assert body["decision_id"] == "buyer-decision-42"

    def test_low_spread_escalates_instead_of_forcing_a_pick(self):
        status, body = tmd.serve_toa_micro_decision(CLOSE_CALL)
        assert status == 200  # escalate is a real paid result
        assert body["action"] == "escalate"
        assert body["recommended"] is None
        assert body["confidence"] < body["threshold"]

    def test_hard_gated_wording_refused_as_paid_result(self):
        status, body = tmd.serve_toa_micro_decision(HARD_GATED)
        assert status == 200  # refused is the product working
        assert body["action"] == "refused"
        assert body["recommended"] is None
        assert body["confidence"] == 0.0
        assert body["forecast_detail"] == {}
        assert "founder-gated" in body["rationale"]

    def test_hard_gate_word_in_label_of_any_option_refuses(self):
        payload = {
            "options": [
                {"id": "p", "label": "merge the branch now",
                 "signals": [0.6, 0.62, 0.61]},
                {"id": "q", "label": "keep reviewing",
                 "signals": [0.5, 0.51, 0.52]},
            ],
            "context": {"horizon": 4},
        }
        status, body = tmd.serve_toa_micro_decision(payload)
        assert status == 200
        assert body["action"] == "refused"

    def test_price_registration(self):
        res = x402.get_resource(RESOURCE)
        assert res is not None
        assert res["token"] == "USDC"
        assert res["amount_usdc"] == 0.05
        # USDC has 6 decimals: $0.05 == 50000 atomic units.
        assert res["amount_atomic"] == "50000"
        ids = [r["id"] for r in x402.list_resources()]
        assert RESOURCE in ids


# ---------------------------------------------------------------------------
# Unit: strict validation (400 after payment in production)
# ---------------------------------------------------------------------------

def _opt(oid="a", label="opt", signals=(0.5, 0.6, 0.55)):
    return {"id": oid, "label": label, "signals": list(signals)}


MALFORMED = [
    None,
    "not-an-object",
    {},
    {"options": "nope"},
    {"options": [_opt("a")]},                                  # 1 option
    {"options": [_opt(str(i)) for i in range(6)]},             # 6 options
    {"options": [_opt("a"), _opt("a")]},                        # duplicate id
    {"options": [_opt("a"), {"label": "x", "signals": [0.1, 0.2]}]},
    {"options": [_opt("a"), {"id": "b", "signals": [0.1, 0.2]}]},
    {"options": [_opt("a"), _opt("b", signals=(0.5,))]},        # <2 signals
    {"options": [_opt("a"), _opt("b", signals=("0.5", 0.6))]},  # string signal
    {"options": [_opt("a"), _opt("b", signals=(True, 0.6))]},  # bool signal
    {"options": [_opt("a"), _opt("b", signals=(float("nan"), 0.6))]},
    {"options": [_opt("a"), _opt("b", signals=(float("inf"), 0.6))]},
    {"options": [_opt("a"), _opt("b")], "surprise": 1},         # unknown field
    {"options": [_opt("a"), dict(_opt("b"), extra=1)]},        # unknown opt field
    {"options": [_opt("a"), _opt("b")], "decision_id": ""},
    {"options": [_opt("a"), _opt("b")], "decision_id": 7},
    {"options": [_opt("a"), _opt("b")], "context": "soon"},
    {"options": [_opt("a"), _opt("b")], "context": {"horizon": 0}},
    {"options": [_opt("a"), _opt("b")], "context": {"horizon": 99}},
    {"options": [_opt("a"), _opt("b")], "context": {"horizon": 2.5}},
    {"options": [_opt("a"), _opt("b")], "context": {"speed": "fast"}},
    {"options": [_opt("a"), _opt("b", label="")]},
    {"options": [_opt("a"), _opt("b", label="x" * 201)]},
]


class TestValidation:
    @pytest.mark.parametrize("payload", MALFORMED)
    def test_malformed_payloads_are_400(self, payload):
        status, body = tmd.serve_toa_micro_decision(payload)
        assert status == 400
        assert body["ok"] is False
        assert body["error"]
        assert "action" not in body  # no decision is produced

    def test_malformed_payload_writes_no_decision_log(self, toa_log):
        tmd.serve_toa_micro_decision({"options": [_opt("a")]})
        assert _log_lines(toa_log) == []

    def test_five_options_accepted(self):
        payload = {
            "options": [_opt(f"o{i}", signals=(0.4 + i * 0.05, 0.5, 0.45))
                        for i in range(5)],
            "context": {"horizon": 4},
        }
        status, body = tmd.serve_toa_micro_decision(payload)
        assert status == 200
        assert body["action"] in ("recommend", "escalate")
        assert len(body["scores"]) == 5

    def test_integer_signals_accepted_and_coerced(self):
        payload = {
            "options": [_opt("a", signals=(1, 2, 3)), _opt("b", signals=(3, 2, 1))],
            "context": {"horizon": 4},
        }
        status, body = tmd.serve_toa_micro_decision(payload)
        assert status == 200
        assert all(isinstance(v, float) for v in body["scores"].values())


# ---------------------------------------------------------------------------
# Unit: decision log + outcome loop closure
# ---------------------------------------------------------------------------

class TestOutcomeLoop:
    def test_paid_path_never_records_outcome(self, toa_log):
        tmd.serve_toa_micro_decision(WINNER)
        tmd.serve_toa_micro_decision(HARD_GATED)
        lines = _log_lines(toa_log)
        assert len(lines) == 2
        assert all(l["type"] == "decision" for l in lines)
        assert all(l["outcome"] is None for l in lines)

    def test_outcome_loop_closes_for_a_served_decision(self, toa_log):
        status, body = tmd.serve_toa_micro_decision(WINNER)
        assert status == 200
        decision_id = body["decision_id"]
        before = td._get_orchestrator().feedback_agent.event_count
        out = td.toa_record_outcome(
            decision_id, {"success": True, "quality": 4,
                          "acted_on": body["recommended"]})
        assert out["feedback_ingested"] is True
        after = td._get_orchestrator().feedback_agent.event_count
        assert after == before + 1
        lines = _log_lines(toa_log)
        outcomes = [l for l in lines if l["type"] == "outcome"
                    and l["decision_id"] == decision_id]
        assert len(outcomes) == 1
        assert outcomes[0]["outcome"]["success"] is True


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
    # Price is server-derived: the 402 demands exactly $0.05 USDC.
    assert ch["payment"]["maxAmountRequired"] == "50000", ch
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


def _serve(client, access_token: str, payload):
    return client.post(
        f"/api/paid/{RESOURCE}",
        headers={"X-Payment-Token": access_token},
        json=payload,
    )


class TestFullLoop:
    def test_paid_settle_delivers_decision(
            self, client, x402_db, mock_onchain):
        paid = _pay(client, _tx("c1"))
        r = _serve(client, paid["access_token"], WINNER)
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["action"] == "recommend"
        assert body["recommended"] == max(
            body["scores"], key=lambda k: body["scores"][k])
        assert body["confidence"] >= body["threshold"]

    def test_paid_deterministic_across_two_payments(
            self, client, x402_db, mock_onchain):
        p1 = _pay(client, _tx("c2"))
        b1 = _serve(client, p1["access_token"], WINNER).get_json()
        p2 = _pay(client, _tx("c3"))
        b2 = _serve(client, p2["access_token"], WINNER).get_json()
        assert b1["decision_id"] == b2["decision_id"]
        assert b1["recommended"] == b2["recommended"]
        assert b1["confidence"] == b2["confidence"]
        assert b1["scores"] == b2["scores"]

    def test_paid_escalate_delivered_as_result(
            self, client, x402_db, mock_onchain):
        paid = _pay(client, _tx("c4"))
        r = _serve(client, paid["access_token"], CLOSE_CALL)
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["action"] == "escalate"
        assert body["recommended"] is None

    def test_paid_hard_gate_refused_delivered_as_result(
            self, client, x402_db, mock_onchain):
        paid = _pay(client, _tx("c5"))
        r = _serve(client, paid["access_token"], HARD_GATED)
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["action"] == "refused"
        assert body["recommended"] is None

    def test_paid_malformed_payload_is_400(
            self, client, x402_db, mock_onchain):
        paid = _pay(client, _tx("c6"))
        r = _serve(client, paid["access_token"],
                   {"options": [_opt("only")]})
        assert r.status_code == 400
        assert r.get_json()["ok"] is False
        assert "action" not in r.get_json()

    def test_unpaid_serve_rejected(
            self, client, x402_db, mock_onchain):
        r = client.post(f"/api/paid/{RESOURCE}?wallet={BUYER}",
                        json=WINNER)
        assert r.status_code == 402
        assert "scores" not in (r.get_json() or {})

    def test_tx_hash_replay_rejected(
            self, client, x402_db, mock_onchain):
        tx = _tx("c7")
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
            self, client, x402_db, mock_onchain):
        paid = _pay(client, _tx("c8"))
        r1 = _serve(client, paid["access_token"], WINNER)
        assert r1.status_code == 200
        r2 = _serve(client, paid["access_token"], WINNER)
        assert r2.status_code != 200
        assert "scores" not in (r2.get_json() or {})

    def test_unbound_challenge_cannot_settle(
            self, client, x402_db, mock_onchain):
        draft = x402.create_challenge(RESOURCE)
        r = client.post("/api/x402/verify", json={
            "challenge_id": draft["challenge_id"],
            "tx_hash": _tx("c9"),
            "payer_wallet": BUYER,
        })
        assert r.status_code == 400, r.get_json()
        assert r.get_json()["error"] == "challenge_unbound"

    def test_underpayment_rejected(
            self, client, x402_db, mock_onchain, paid_amount):
        paid_amount["atomic"] = 1000  # $0.001 against a $0.05 price
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
            "tx_hash": _tx("ca"),
            "payer_wallet": BUYER,
        })
        assert r.status_code == 402, r.get_json()
        assert r.get_json()["error"] == "insufficient_amount"
