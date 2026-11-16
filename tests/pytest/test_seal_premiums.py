"""Path 9 — SEAL underwriting premiums.

Covers the premium mechanism built in ``sincor2.underwriting.premiums``:

- quote determinism + monotonicity (coverage, duration, risk);
- collection recorded exactly once per issuance, 100% to treasury, no burn;
- zero/unpriced coverage is explicitly not charged (denied envelopes are
  never premium-bearing — no silent free *active* underwriting);
- refund semantics: revoking an unused envelope refunds the premium
  exactly once; a spent envelope's premium is earned (no refund);
- non-interference: premium state never changes Facilitator / Policy
  outcomes, and SigilSeal EIP-712 verification behaves exactly as before.

Claims handling is out of scope: this package has no claims/payout
mechanism — the envelope debit *is* the covered spend.
"""

from __future__ import annotations

from decimal import Decimal

from sincor2.underwriting.envelopes import EnvelopeService
from sincor2.underwriting.engine import TREASURY, UnderwritingEngine
from sincor2.underwriting.facilitator import Facilitator
from sincor2.underwriting.mandates import MandateService
from sincor2.underwriting.premiums import (
    PremiumLedger,
    quote_premium,
)
from sincor2.underwriting.store import UnderwriteStore
from sincor2.underwriting.taps.ledger_sim import LedgerSimTap
from sincor2.underwriting.toa_adapter import SpendUnderwriter
from sincor2.underwriting.types import AuthorizeRequest, REASON


# --------------------------------------------------------------------------
# Quote: determinism + monotonicity + explicit zero behaviour
# --------------------------------------------------------------------------

def test_quote_is_deterministic():
    a = quote_premium(coverage_usd="100.00", duration_seconds=2700, risk_score=0.25)
    b = quote_premium(coverage_usd="100.00", duration_seconds=2700, risk_score=0.25)
    da, db = a.to_dict(), b.to_dict()
    da.pop("premium_id")  # uuid is unique per quote; pricing must not be
    db.pop("premium_id")
    assert da == db
    assert Decimal(a.premium_usd) > 0


def test_quote_formula_anchor():
    # base 100 bps of coverage, reference window 2700s, risk multiplier 1+risk.
    # 100.00 * 1% * (2700/2700) * (1+0) = 1.00 exactly.
    q = quote_premium(coverage_usd="100.00", duration_seconds=2700, risk_score=0.0)
    assert q.premium_usd == "1.000000"
    assert q.risk_class == "standard"


def test_quote_monotonic_in_coverage():
    prev = Decimal("-1")
    for cov in ("1.00", "5.00", "25.00", "100.00", "1000.00"):
        q = quote_premium(coverage_usd=cov, duration_seconds=2700, risk_score=0.1)
        cur = Decimal(q.premium_usd)
        assert cur >= prev
        prev = cur
    assert prev > Decimal("0")


def test_quote_monotonic_in_duration():
    prev = Decimal("-1")
    for ttl in (60, 300, 1200, 2700, 5400, 86400):
        q = quote_premium(coverage_usd="50.00", duration_seconds=ttl, risk_score=0.1)
        cur = Decimal(q.premium_usd)
        assert cur >= prev
        prev = cur


def test_quote_monotonic_in_risk():
    prev = Decimal("-1")
    for risk in (0.0, 0.2, 0.5, 0.9, 1.0):
        q = quote_premium(coverage_usd="50.00", duration_seconds=2700, risk_score=risk)
        cur = Decimal(q.premium_usd)
        assert cur >= prev
        prev = cur


def test_quote_zero_coverage_is_explicitly_not_charged():
    q = quote_premium(coverage_usd="0", duration_seconds=2700, risk_score=0.5)
    assert q.premium_usd == "0.000000"
    assert q.status == "not_charged"


def test_quote_minimum_premium_floor_for_active_coverage():
    # Any positive coverage prices above zero — no silent free underwriting.
    q = quote_premium(coverage_usd="0.10", duration_seconds=60, risk_score=0.0)
    assert Decimal(q.premium_usd) >= Decimal("0.01")
    assert q.status == "quoted"


# --------------------------------------------------------------------------
# Stack helpers (runtime issuance path: mandate -> envelope -> facilitator)
# --------------------------------------------------------------------------

def _stack(tmp_path):
    store = UnderwriteStore(tmp_path)
    tap = LedgerSimTap("100.00")
    mandates = MandateService(store)
    envelopes = EnvelopeService(store, SpendUnderwriter(tap_name="ledger_sim"))
    fac = Facilitator(store, mandates, envelopes, tap)
    m = mandates.issue(
        operator_id="court",
        controller_wallet="0x" + "ab" * 20,
        agent_id="E-altair-04",
        max_notional_usd="25.00",
        max_single_tx_usd="5.00",
        allowed_skills=["underwrite.echo"],
        allowed_payees=["sincor:skill:underwrite.echo"],
    )
    return store, mandates, envelopes, fac, m


def _issue(envelopes, m, requested="5.00"):
    return envelopes.request(
        m,
        requested_usd=requested,
        skill_id="underwrite.echo",
        payee="sincor:skill:underwrite.echo",
    )


def _premiums(store):
    return store.list("premiums")


# --------------------------------------------------------------------------
# Collection at issuance
# --------------------------------------------------------------------------

def test_issuance_collects_premium_exactly_once(tmp_path):
    store, _, envelopes, _, m = _stack(tmp_path)
    env = _issue(envelopes, m)
    assert env.status == "active"
    assert env.premium_status == "collected"
    assert Decimal(env.premium_usd) > 0

    rows = [r for r in _premiums(store) if r["envelope_id"] == env.envelope_id]
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "collected"
    assert row["premium_usd"] == env.premium_usd
    assert row["payee"] == TREASURY
    # Premiums are revenue, never burned.
    assert row["burn_usd"] == "0.000000"
    assert Decimal(row["treasury_usd"]) == Decimal(row["premium_usd"])

    audits = [e for e in store.iter_audit() if e["kind"] == "premium.collected"]
    assert len(audits) == 1
    assert audits[0]["envelope_id"] == env.envelope_id


def test_collection_is_idempotent_per_envelope(tmp_path):
    store = UnderwriteStore(tmp_path)
    ledger = PremiumLedger(store)
    q = quote_premium(coverage_usd="10.00", duration_seconds=2700, risk_score=0.0)
    r1 = ledger.collect(q, envelope_id="env-1", agent_id="a-1", mandate_id="m-1")
    r2 = ledger.collect(q, envelope_id="env-1", agent_id="a-1", mandate_id="m-1")
    assert r1["premium_id"] == r2["premium_id"]
    assert len([r for r in _premiums(store) if r["status"] == "collected"]) == 1


def test_denied_envelope_collects_no_premium(tmp_path):
    store, mandates, envelopes, _, m = _stack(tmp_path)
    mandates.revoke(m.mandate_id, "operator")  # kill switch -> issuance denied
    # The real API flow reloads the mandate from the store (active_for
    # filters killed mandates); mirror that here.
    killed = store.get_mandate(m.mandate_id)
    assert killed is not None and killed.killed
    env = _issue(envelopes, killed)
    assert env.denied
    assert env.premium_status == "not_charged"
    assert env.premium_usd == "0.000000"
    assert _premiums(store) == []


def test_each_issuance_gets_its_own_premium(tmp_path):
    store, _, envelopes, _, m = _stack(tmp_path)
    e1 = _issue(envelopes, m, "5.00")
    e2 = _issue(envelopes, m, "5.00")
    assert e1.envelope_id != e2.envelope_id
    assert len(_premiums(store)) == 2


# --------------------------------------------------------------------------
# Refund semantics
# --------------------------------------------------------------------------

def test_revoke_unused_envelope_refunds_premium_once(tmp_path):
    store, _, envelopes, _, m = _stack(tmp_path)
    env = _issue(envelopes, m)
    envelopes.revoke(env.envelope_id, "operator")
    refunds = [r for r in _premiums(store) if r["status"] == "refunded"]
    assert len(refunds) == 1
    assert refunds[0]["premium_usd"] == env.premium_usd
    # Second revoke attempt / duplicate refund call stays at one refund.
    ledger = PremiumLedger(store)
    assert ledger.refund(env.envelope_id, reason="duplicate") is None
    assert len([r for r in _premiums(store) if r["status"] == "refunded"]) == 1


def test_revoke_after_spend_earns_premium_no_refund(tmp_path):
    store, _, envelopes, fac, m = _stack(tmp_path)
    env = _issue(envelopes, m)
    r = fac.authorize(
        AuthorizeRequest(env.envelope_id, "sincor:skill:underwrite.echo", "0.05", "underwrite.echo")
    )
    assert r.allowed
    envelopes.revoke(env.envelope_id, "operator")
    assert [x for x in _premiums(store) if x["status"] == "refunded"] == []


# --------------------------------------------------------------------------
# Non-interference: premium state never alters gates / verification
# --------------------------------------------------------------------------

def test_premium_state_never_alters_facilitator_outcomes(tmp_path):
    store, mandates, envelopes, fac, m = _stack(tmp_path)
    env = _issue(envelopes, m)
    assert env.premium_status == "collected"

    # Tamper: pretend the premium was never paid. Authorization must not care.
    store.append("premiums", {"envelope_id": env.envelope_id, "status": "unpaid"})
    ok = fac.authorize(
        AuthorizeRequest(env.envelope_id, "sincor:skill:underwrite.echo", "0.05", "underwrite.echo")
    )
    assert ok.allowed

    denied = fac.authorize(
        AuthorizeRequest(env.envelope_id, "0xevil", "0.05", "underwrite.echo")
    )
    assert not denied.allowed
    assert denied.reason == REASON.DENY_UNKNOWN_PAYEE

    mandates.revoke(m.mandate_id, "operator")
    killed = fac.authorize(
        AuthorizeRequest(env.envelope_id, "sincor:skill:underwrite.echo", "0.05", "underwrite.echo")
    )
    assert not killed.allowed
    assert killed.reason == REASON.DENY_KILL_SWITCH


def test_sigil_seal_verification_unchanged():
    # Regression: the SEAL EIP-712 envelope verification on main
    # (marketplace/contract_net/sigil_seal.py, ex cnp-01-sigil-seal branch)
    # behaves exactly as before the premium build.
    from eth_account import Account

    from marketplace.contract_net.sigil_seal import sign_seal, verify_seal
    from marketplace.contract_net.types import BASE_SEPOLIA_CHAIN_ID, ContractNetConfig

    config = ContractNetConfig(chain_id=BASE_SEPOLIA_CHAIN_ID)
    master = Account.from_key("0x" + "11" * 32)
    fields = {
        "agent_did": "did:sincor:e-vega-02",
        "agent_address": master.address,
        "capabilities": ["auction:bid"],
        "expiry": 1_800_000_000 + 10 * 24 * 3600,
        "nonce": 1,
    }
    sig, sig_type, _digest = sign_seal(config, fields, private_key="0x" + "11" * 32)
    ok, _reason = verify_seal(
        config, fields, sig, sig_type=sig_type,
        expected_address=master.address, now=1_800_000_000,
    )
    assert ok
    tampered = dict(fields, capabilities=["auction:bid", "treasury:drain"])
    ok2, _reason2 = verify_seal(
        config, tampered, sig, sig_type=sig_type,
        expected_address=master.address, now=1_800_000_000,
    )
    assert not ok2


# --------------------------------------------------------------------------
# Engine (/v1) issuance path
# --------------------------------------------------------------------------

def _engine_body(**over):
    body = {
        "agent_id": "agent-p9",
        "principal_wallet": "0x" + "34" * 20,
        "agent_wallet": "0x" + "12" * 20,
        "requested_usd": "100",
        "cap_usd": "500",
        "erc8004_registered": True,
    }
    body.update(over)
    return body


def test_engine_underwrite_collects_premium_on_allow(tmp_path):
    engine = UnderwritingEngine(UnderwriteStore(tmp_path))
    rec = engine.underwrite(_engine_body())
    assert rec["decision"] == "allow"
    assert rec["premium"]["status"] == "collected"
    assert Decimal(rec["premium"]["premium_usd"]) > 0
    rows = [r for r in engine.store.list("premiums") if r["envelope_id"] == rec["envelope_id"]]
    assert len(rows) == 1
    assert rows[0]["payee"] == TREASURY
    assert rows[0]["burn_usd"] == "0.000000"


def test_engine_underwrite_denied_collects_no_premium(tmp_path):
    engine = UnderwritingEngine(UnderwriteStore(tmp_path))
    rec = engine.underwrite(_engine_body(requested_usd="9999"))  # exceeds cap
    assert rec["decision"] == "deny"
    assert rec["premium"]["status"] == "not_charged"
    assert engine.store.list("premiums") == []


def test_engine_settle_unchanged_after_premium(tmp_path):
    engine = UnderwritingEngine(UnderwriteStore(tmp_path))
    rec = engine.underwrite(_engine_body())
    receipt = engine.settle(rec["envelope_id"], {"amount_usd": 10})
    # Pre-existing settlement-fee behaviour is untouched by the premium build.
    assert receipt["underwrite_fee_usd"] == round(10 * 0.005, 6)
    assert receipt["envelope_id"] == rec["envelope_id"]
