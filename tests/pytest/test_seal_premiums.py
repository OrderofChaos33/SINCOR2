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

from datetime import timedelta
from decimal import Decimal

from sincor2.underwriting import premiums as premiums_mod
from sincor2.underwriting.envelopes import EnvelopeService
from sincor2.underwriting.engine import TREASURY, UnderwritingEngine
from sincor2.underwriting.facilitator import Facilitator
from sincor2.underwriting.mandates import MandateService
from sincor2.underwriting.premiums import (
    PremiumLedger,
    quote_premium,
    resolve_effective_base_bps,
)
from sincor2.underwriting.store import UnderwriteStore
from sincor2.underwriting.taps.ledger_sim import LedgerSimTap
from sincor2.underwriting.toa_adapter import SpendUnderwriter
from sincor2.underwriting.types import AuthorizeRequest, REASON, iso, utcnow


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
    # Premium collection does not alter settlement: the settle runs under
    # the CONFORM canon (founder verdict, 2026-11-16) — 5% fee, all of it
    # to treasury, zero burn.
    assert receipt["underwrite_fee_usd"] == round(10 * 0.05, 6)
    assert receipt["treasury_fee_usd"] == receipt["underwrite_fee_usd"]
    assert receipt["burn_fee_usd"] == 0.0
    assert receipt["envelope_id"] == rec["envelope_id"]


def test_engine_settle_conform_canon(tmp_path):
    """CONFORM verdict (founder, 2026-11-16): settlement fee is exactly
    5% of the settled amount, the treasury receives the full fee, and the
    burn is exactly zero. The legacy model (50 bps, 50% burn split) is
    retired."""
    engine = UnderwritingEngine(UnderwriteStore(tmp_path))
    rec = engine.underwrite(_engine_body())
    receipt = engine.settle(rec["envelope_id"], {"amount_usd": 100})
    assert receipt["underwrite_fee_usd"] == 5.0  # exactly 5% of 100
    assert receipt["treasury_fee_usd"] == 5.0  # full fee to treasury
    assert receipt["burn_fee_usd"] == 0.0  # no burn, ever
    # The persisted receipt carries the same conform figures.
    stored = engine.receipt(receipt["receipt_id"])
    assert stored["underwrite_fee_usd"] == 5.0
    assert stored["treasury_fee_usd"] == 5.0
    assert stored["burn_fee_usd"] == 0.0


# --------------------------------------------------------------------------
# Dynamic adjustment clause (founder-ratified 2026-11-16)
#
# Floor 100 bps, cap 222.5 bps. Trigger 1 (GTV): trailing-30d settled
# volume (receipts.amount_usd) >= $1M -> cap. Trigger 2 (runway):
# reserve / trailing-30d burn < 6 months -> +50 bps steps until the
# projected runway recovers or the cap is reached. Zero burn = infinite
# runway = no step-up. Adjusted quotes append one premium_adjustments
# record; floor quotes append nothing.
# --------------------------------------------------------------------------

def _seed_receipt(store, amount_usd, burn_fee_usd=0.0, *, days_ago=0):
    store.append(
        "receipts",
        {
            "receipt_id": f"seed-{amount_usd}-{burn_fee_usd}-{days_ago}",
            "amount_usd": amount_usd,
            "burn_fee_usd": burn_fee_usd,
            "created_at": iso(utcnow() - timedelta(days=days_ago)),
        },
    )


def _seed_collected_premium(store, premium_usd, base_bps=100):
    store.append(
        "premiums",
        {
            "status": "collected",
            "premium_usd": premium_usd,
            "burn_usd": "0.000000",
            "base_bps": base_bps,
            "created_at": iso(utcnow()),
        },
    )


def _adjustments(store):
    return store.list("premium_adjustments")


def test_adjustment_floor_default_with_no_history(tmp_path):
    # Not-yet-live default state: no records, default reserve "0", zero
    # burn -> infinite runway -> the 100 bps floor, nothing logged.
    store = UnderwriteStore(tmp_path)
    q = quote_premium(
        coverage_usd="100.00", duration_seconds=2700, risk_score=0.0, store=store
    )
    assert q.base_bps == 100
    assert q.base_bps_effective == 100
    assert q.base_bps_floor == 100
    assert q.base_bps_cap == 222.5
    assert q.adjustment_trigger is None
    assert q.premium_usd == "1.000000"
    assert _adjustments(store) == []


def test_adjustment_quote_without_store_matches_pre_clause():
    q = quote_premium(coverage_usd="100.00", duration_seconds=2700, risk_score=0.0)
    assert q.base_bps == 100
    assert q.base_bps_effective == 100
    assert q.base_bps_floor == 100
    assert q.base_bps_cap == 222.5
    assert q.adjustment_trigger is None
    # Canonical case is byte-identical to the pre-change formula output.
    assert q.premium_usd == "1.000000"


def test_adjustment_floor_premium_byte_identical_with_zero_burn_history(tmp_path):
    store = UnderwriteStore(tmp_path)
    _seed_receipt(store, 5000.0, 0.0)  # history exists, but no burn, no GTV
    q = quote_premium(
        coverage_usd="100.00", duration_seconds=2700, risk_score=0.0, store=store
    )
    assert q.base_bps_effective == 100
    assert q.adjustment_trigger is None
    assert q.premium_usd == "1.000000"
    assert _adjustments(store) == []


def test_adjustment_gtv_trigger_fires_at_threshold(tmp_path):
    store = UnderwriteStore(tmp_path)
    _seed_receipt(store, 600_000.0)
    _seed_receipt(store, 400_000.0)  # trailing settled volume = exactly $1M
    q = quote_premium(
        coverage_usd="100.00", duration_seconds=2700, risk_score=0.0, store=store
    )
    assert q.base_bps_effective == 222.5
    assert q.base_bps == 222.5
    assert q.adjustment_trigger == "gtv"
    # 100.00 * 2.225% * (2700/2700) * (1+0) = 2.225 exactly.
    assert q.premium_usd == "2.225000"
    rows = _adjustments(store)
    assert len(rows) == 1
    assert rows[0]["effective_bps"] == 222.5
    assert rows[0]["trigger"] == "gtv"
    assert rows[0]["inputs"]["settled_volume_30d_usd"] == "1000000.000000"
    assert rows[0]["timestamp"]


def test_adjustment_gtv_trigger_silent_below_threshold(tmp_path):
    store = UnderwriteStore(tmp_path)
    _seed_receipt(store, 999_999.0)  # one dollar under the GTV threshold
    q = quote_premium(
        coverage_usd="100.00", duration_seconds=2700, risk_score=0.0, store=store
    )
    assert q.base_bps_effective == 100
    assert q.adjustment_trigger is None
    assert q.premium_usd == "1.000000"
    assert _adjustments(store) == []


def test_adjustment_gtv_ignores_receipts_outside_window(tmp_path):
    store = UnderwriteStore(tmp_path)
    _seed_receipt(store, 2_000_000.0, days_ago=31)  # outside trailing 30d
    q = quote_premium(
        coverage_usd="100.00", duration_seconds=2700, risk_score=0.0, store=store
    )
    assert q.base_bps_effective == 100
    assert q.adjustment_trigger is None
    assert _adjustments(store) == []


def test_adjustment_runway_steps_up_in_increments(tmp_path):
    # Reserve $5,000, trailing burn $1,000 -> runway 5.0 months (< 6).
    # Collected premiums of $900 at 100 bps: at 150 bps the projected
    # income is $1,350, the drain drops to $550, and the projected
    # runway is ~9.09 months — one +50 step suffices.
    store = UnderwriteStore(tmp_path)
    _seed_receipt(store, 10_000.0, 1_000.0)
    _seed_collected_premium(store, "900.000000", base_bps=100)
    res = resolve_effective_base_bps(store, reserve_usd="5000")
    assert res.trigger == "runway"
    assert res.effective_bps == Decimal("150")
    assert res.inputs["runway_months"] == "5.0000"
    assert res.inputs["burn_rate_30d_usd"] == "1000.000000"


def test_adjustment_runway_caps_at_market_median(tmp_path):
    # No premium income to project: the drain never improves, so the
    # step-up walks the whole way to the 222.5 bps cap and stops there.
    store = UnderwriteStore(tmp_path)
    _seed_receipt(store, 10_000.0, 1_000.0)
    res = resolve_effective_base_bps(store, reserve_usd="5000")
    assert res.trigger == "runway"
    assert res.effective_bps == Decimal("222.5")


def test_adjustment_runway_zero_reserve_positive_burn_caps(tmp_path):
    # Reserve 0 with any positive burn is a 0-month runway: full step-up.
    store = UnderwriteStore(tmp_path)
    _seed_receipt(store, 10_000.0, 100.0)
    res = resolve_effective_base_bps(store, reserve_usd="0")
    assert res.trigger == "runway"
    assert res.effective_bps == Decimal("222.5")


def test_adjustment_runway_healthy_no_trigger(tmp_path):
    store = UnderwriteStore(tmp_path)
    _seed_receipt(store, 10_000.0, 100.0)  # runway = 1000 / 100 = 10 months
    res = resolve_effective_base_bps(store, reserve_usd="1000")
    assert res.trigger is None
    assert res.effective_bps == Decimal("100")


def test_adjustment_zero_burn_never_steps_up(tmp_path):
    # Zero trailing burn -> infinite runway, even with a zero reserve:
    # no step-up and no division by zero.
    store = UnderwriteStore(tmp_path)
    _seed_receipt(store, 10_000.0, 0.0)
    res = resolve_effective_base_bps(store, reserve_usd="0")
    assert res.trigger is None
    assert res.effective_bps == Decimal("100")
    assert res.inputs["runway_months"] is None


def test_adjustment_both_triggers(tmp_path):
    store = UnderwriteStore(tmp_path)
    _seed_receipt(store, 1_500_000.0, 1_000.0)  # GTV over threshold + burn
    res = resolve_effective_base_bps(store, reserve_usd="5000")
    assert res.trigger == "both"
    assert res.effective_bps == Decimal("222.5")


def test_adjustment_runway_quote_logs_exactly_once(tmp_path, monkeypatch):
    monkeypatch.setattr(premiums_mod, "UNDERWRITING_RESERVE_USD", Decimal("5000"))
    store = UnderwriteStore(tmp_path)
    _seed_receipt(store, 10_000.0, 1_000.0)  # runway 5 months, no income
    q = quote_premium(
        coverage_usd="100.00", duration_seconds=2700, risk_score=0.0, store=store
    )
    assert q.adjustment_trigger == "runway"
    assert q.base_bps_effective == 222.5
    rows = _adjustments(store)
    assert len(rows) == 1
    assert rows[0]["trigger"] == "runway"
    assert rows[0]["inputs"]["reserve_usd"] == "5000.000000"


def test_adjustment_log_written_exactly_on_adjusted_quotes(tmp_path):
    store = UnderwriteStore(tmp_path)
    _seed_receipt(store, 1_000_000.0)  # GTV at threshold, zero burn
    quote_premium(coverage_usd="10.00", duration_seconds=2700, risk_score=0.0, store=store)
    quote_premium(coverage_usd="10.00", duration_seconds=2700, risk_score=0.0, store=store)
    assert len(_adjustments(store)) == 2  # one record per adjusted quote

    empty = UnderwriteStore(tmp_path / "empty")
    quote_premium(coverage_usd="10.00", duration_seconds=2700, risk_score=0.0, store=empty)
    assert _adjustments(empty) == []  # floor quotes write nothing


def test_engine_issuance_applies_adjusted_rate(tmp_path):
    engine = UnderwritingEngine(UnderwriteStore(tmp_path))
    _seed_receipt(engine.store, 1_000_000.0)  # GTV trigger armed
    rec = engine.underwrite(_engine_body())
    assert rec["decision"] == "allow"
    premium = rec["premium"]
    assert premium["status"] == "collected"
    assert premium["base_bps"] == 222.5
    assert premium["base_bps_effective"] == 222.5
    assert premium["adjustment_trigger"] == "gtv"
    # Score 1.0 -> risk 0.0; 100 * 2.225% * (2700/2700) * 1 = 2.225.
    assert premium["premium_usd"] == "2.225000"
    assert len(_adjustments(engine.store)) == 1


def test_envelope_issuance_applies_adjusted_rate(tmp_path):
    store, _, envelopes, _, m = _stack(tmp_path)
    _seed_receipt(store, 1_000_000.0)  # GTV trigger armed
    env = _issue(envelopes, m)
    assert env.premium_status == "collected"
    rows = [r for r in _premiums(store) if r["envelope_id"] == env.envelope_id]
    assert len(rows) == 1
    assert rows[0]["base_bps_effective"] == 222.5
    assert rows[0]["adjustment_trigger"] == "gtv"
    assert len(_adjustments(store)) == 1
