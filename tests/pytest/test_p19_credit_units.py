"""Unit tests for the P19 Decentralized Credit Underwriting reference build.

Covers src/sincor2/defi/credit_underwriting.py — behavioral scoring,
the score->LTV table, attestation freshness/replay, the no-unsecured-book
draw rule, health-factor circuit breaker, concentration caps, and fee
routing behind SKU SINCOR-DEFI-P19-CREDIT. Pure logic, no chain.

Each test cites the rule it guards (spec p19 acceptance criteria).
24/24 must pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi.credit_underwriting import (
    ATTEST_TTL_SECONDS,
    CONC_BAND_PCT,
    CONC_BORROWER_PCT,
    FEE_ACCRUAL_BPS,
    FEE_ORIG_BPS,
    HF_HEDGE,
    HF_LIQUIDATE,
    LTV_TABLE,
    MODEL_VERSION,
    SCORE_FLOOR,
    SCORE_MAX,
    SCORE_MIN,
    TREASURY,
    AttestationInvalid,
    AttestationRegistry,
    BorrowerFeatures,
    ConcentrationBreach,
    CreditBook,
    CreditError,
    CreditLine,
    LTVTableInvalid,
    ScoreAttestation,
    UnsecuredDraw,
    accrual_fee_usd,
    is_fresh,
    max_ltv_bps,
    origination_fee_usd,
    score_band,
    score_wallet,
    treasury_take,
    validate_ltv_table,
)


def good_features(**over):
    base = dict(
        wallet_age_blocks=31_536_000,  # ~2 years on 2s blocks
        repaid=20,
        liquidated=0,
        avg_utilization=0.3,
        tx_count=2_000,
        protocol_diversity=8,
        stablecoin_ratio=0.6,
    )
    base.update(over)
    return BorrowerFeatures(**base)


NOW = 5_000_000.0


def att(borrower="b1", score=700, issued_at=NOW, nonce=1, attestor="a1"):
    return ScoreAttestation(borrower, score, issued_at, nonce, attestor)


# -- scoring --------------------------------------------------------------------------------
def test_score_bounded_300_850():
    """Score range is always [300, 850]."""
    assert SCORE_MIN <= score_wallet(good_features()) <= SCORE_MAX
    thin = BorrowerFeatures(0, 0, 0, 0.0, 0, 0, 0.0)
    assert SCORE_MIN <= score_wallet(thin) <= SCORE_MAX


def test_mixer_clamps_to_300():
    """Negative signal: mixer interaction clamps the score to 300."""
    f = good_features(mixer_interaction=True)
    assert score_wallet(f) == 300


def test_repayment_history_earns_top_bands():
    """Proven repayment is the main score driver (Spectral-style)."""
    good = score_wallet(good_features(repaid=50, liquidated=0))
    bad = score_wallet(good_features(repaid=2, liquidated=5))
    assert good > bad
    assert max_ltv_bps(good) >= max_ltv_bps(bad)


def test_liquidation_penalized():
    a = score_wallet(good_features(liquidated=0))
    b = score_wallet(good_features(liquidated=3))
    assert a > b


def test_rug_interaction_penalized():
    a = score_wallet(good_features())
    b = score_wallet(good_features(rug_interaction=True))
    assert a - b >= 100


def test_deterministic():
    f = good_features()
    assert score_wallet(f) == score_wallet(f)


# -- LTV table ----------------------------------------------------------------------------------
@pytest.mark.parametrize("score,expected", [
    (850, 7_500), (750, 7_500), (749, 6_500), (650, 6_500),
    (600, 5_500), (500, 4_500), (400, 3_500), (350, 3_500),
])
def test_ltv_table_exact(score, expected):
    """AC2: LTV lookups match the spec table exactly."""
    assert max_ltv_bps(score) == expected


def test_below_floor_ineligible():
    """score_floor = 350: below it, max LTV is 0 (ineligible)."""
    assert max_ltv_bps(349) == 0
    assert max_ltv_bps(300) == 0
    assert score_band(200) == "ineligible"


def test_non_monotonic_table_reverts():
    """AC2: any non-non-decreasing table update reverts."""
    bad = ((750, 6_500), (650, 7_500), (550, 5_500), (450, 4_500), (350, 3_500))
    with pytest.raises(LTVTableInvalid):
        validate_ltv_table(bad)


def test_valid_table_passes():
    validate_ltv_table()  # no raise
    validate_ltv_table(LTV_TABLE)


# -- credit line: no unsecured book ------------------------------------------------------------------
def test_draw_within_cap():
    """Draw <= collateral * maxLTV succeeds."""
    line = CreditLine("b1", collateral_usd=10_000.0, score=700)  # 65%
    assert line.max_draw_usd == 6_500.0
    assert line.draw(6_500.0) == 6_500.0


def test_zero_collateral_draw_reverts():
    """AC1: a draw with zero collateral reverts — no unsecured book."""
    line = CreditLine("b1", collateral_usd=0.0, score=800)
    with pytest.raises(UnsecuredDraw):
        line.draw(100.0)


def test_over_cap_draw_reverts():
    """AC1: draw above collateral * maxLTV(live score) reverts."""
    line = CreditLine("b1", collateral_usd=10_000.0, score=700)
    with pytest.raises(UnsecuredDraw):
        line.draw(6_500.01)


def test_below_floor_score_cannot_draw():
    line = CreditLine("b1", collateral_usd=10_000.0, score=300)
    with pytest.raises(UnsecuredDraw):
        line.draw(1.0)


# -- health factor + circuit breaker ---------------------------------------------------------------------
def test_health_factor_math():
    line = CreditLine("b1", 10_000.0, 700, debt_usd=5_000.0)
    # HF = 10_000 * 0.65 / 5_000 = 1.30
    assert abs(line.health_factor() - 1.30) < 1e-9


def test_health_factor_infinite_without_debt():
    assert CreditLine("b1", 10_000.0, 700).health_factor() == float("inf")


@pytest.mark.parametrize("debt,expected", [
    (4_000.0, "ok"),        # HF = 1.625
    (5_800.0, "hedge"),     # HF = 1.121 < 1.15
    (6_300.0, "liquidate"), # HF = 1.032 < 1.05
])
def test_circuit_breaker_thresholds(debt, expected):
    """AC4 (python leg): hedge < 1.15, liquidation < 1.05."""
    line = CreditLine("b1", 10_000.0, 700, debt_usd=debt)
    assert line.circuit_action() == expected


def test_hedge_routes_25pct_collateral():
    line = CreditLine("b1", 10_000.0, 700, debt_usd=5_800.0)
    assert line.hedge_amount_usd() == 2_500.0
    ok_line = CreditLine("b1", 10_000.0, 700, debt_usd=4_000.0)
    assert ok_line.hedge_amount_usd() == 0.0


# -- attestations --------------------------------------------------------------------------------------------
def test_stale_attestation_rejected():
    """AC2: borrows with stale (>72h) scores revert."""
    reg = AttestationRegistry()
    old = att(issued_at=NOW - ATTEST_TTL_SECONDS - 1)
    with pytest.raises(AttestationInvalid):
        reg.register(old, NOW)


def test_replayed_nonce_rejected():
    """AC2: replayed attestation nonces revert."""
    reg = AttestationRegistry()
    reg.register(att(nonce=7), NOW)
    with pytest.raises(AttestationInvalid):
        reg.register(att(nonce=7), NOW + 10)


def test_below_floor_attestation_rejected():
    reg = AttestationRegistry()
    with pytest.raises(AttestationInvalid):
        reg.register(att(score=340), NOW)


def test_live_score_missing_reverts():
    reg = AttestationRegistry()
    with pytest.raises(AttestationInvalid):
        reg.live_score("nobody", NOW)


def test_fresh_attestation_ok():
    reg = AttestationRegistry()
    reg.register(att(score=720), NOW)
    assert reg.live_score("b1", NOW + 3_600) == 720


# -- concentration -------------------------------------------------------------------------------------------------
def _seed_book(n=10):
    """Seed a $1,000 book across 3 score bands, every borrower at 10%."""
    book = CreditBook()
    scores = [800, 700, 600]
    for i in range(n):
        book.originate(f"b{i}", 100.0, scores[i % 3])
    return book


def test_per_borrower_cap_boundary():
    """AC3: origination breaching the 10% per-borrower cap reverts."""
    book = _seed_book()
    assert book.total_usd == 1_000.0
    with pytest.raises(ConcentrationBreach):
        book.originate("b0", 100.0, 800)  # b0 would hold 200/1100 = 18%


def test_per_band_cap():
    """AC3: origination breaching the 40% per-band cap reverts."""
    book = _seed_book()  # 750+ band at exactly 400/1000 = 40%
    with pytest.raises(ConcentrationBreach):
        book.originate("bX", 100.0, 800)  # band would be 500/1100 = 45%


def test_concentration_ok_within_caps():
    book = _seed_book()
    assert book.total_usd == 1_000.0
    # A small in-cap top-up: every borrower stays <= 10%, bands <= 40%.
    book.originate("b10", 50.0, 700)
    assert book.total_usd == 1_050.0


def test_bootstrap_phase_exempt():
    """Caps bind at $1,000 of book; bootstrapping originations are allowed."""
    book = CreditBook()
    out = book.originate("first", 500.0, 800)
    assert out["caps_enforced"] is False
    assert book.total_usd == 500.0


# -- fees --------------------------------------------------------------------------------------------------------------
def test_fee_rates():
    assert FEE_ORIG_BPS == 20 and FEE_ACCRUAL_BPS == 20


def test_worked_fee_example():
    """AC5: $10,000 at 8% APR for 1 year -> $20 + $1.60 = $21.60 to treasury.

    NOTE: the spec's worked example claims $16 interest fee, but 20 bps of
    $800 interest is $1.60 (20/10000 * 800). The implementation follows the
    normative fee_bps=20; the spec example is arithmetically wrong ($16
    would be 200 bps). Flagged for spec correction.
    """
    take = treasury_take(10_000.0, 0.08, 1.0)
    assert take["origination_fee_usd"] == 20.0
    assert take["accrual_fee_usd"] == 1.6
    assert take["total_to_treasury_usd"] == 21.6
    assert take["treasury"] == TREASURY


def test_origination_fee_exact():
    assert origination_fee_usd(10_000.0) == 20.0
    assert accrual_fee_usd(800.0) == 1.6
