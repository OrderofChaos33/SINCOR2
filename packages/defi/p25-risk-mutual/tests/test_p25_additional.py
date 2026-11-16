"""Additional PACK-2 tests for P25 (suite P05) DeFi Risk Mutual.

These cover core paths the platform suite (28 tests, kept unchanged in
test_p05_insurance_units.py) does not exercise:

- premium-math edges: tier boundaries, full-year annualization, duration
  linearity, non-positive input rejection, treasury-fee rounding;
- the ENGAGEMENT.md secondary pricing form (cover premium 2.5%-3.25% of
  cover amount per year) as a commercial-term arithmetic check, kept
  explicitly separate from the code's own tier schedule (100/200/400 bps)
  — the code contains no 250/325 bps rate, and this file does not pretend
  otherwise;
- pool accounting: deposit validation, pro-rata share pricing after
  premium accrual, partial redemption, outstanding-cover accounting;
- claims handling: unknown cover, unapproved payout, majority-no vote,
  payout idempotency, empty pending-withdrawal, solvent shortfall factor;
- governance / registry / config-surface defaults.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PKG_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PKG_SRC) not in sys.path:
    sys.path.insert(0, str(PKG_SRC))

import pytest

from sincor_p25_risk_mutual import insurance_mutual as m
from sincor_p25_risk_mutual.insurance_mutual import (
    AssessorRegistry,
    Attestation,
    ClaimRuleError,
    Governance,
    MutualError,
    MutualPool,
    TimelockError,
    UnauthorizedError,
    Underwriter,
)

W = 1_000_000  # stablecoin wei per USD (6dp)
_BLOCK = 1_000_000


def _pool_with_cover(cover_wei=100_000 * W, score=0.3):
    uw = Underwriter()
    uw.publish("proto-a", score, block=_BLOCK, signer="0xuw")
    pool = MutualPool()
    pool.assessors.appoint("assessor1", weight=10**18)
    pool.assessors.appoint("assessor2", weight=10**18)
    pool.deposit_capital("lp1", 1_000_000 * W)
    pool.live_underwriting = True
    cover = pool.buy_cover("buyer", "proto-a", cover_wei, 90,
                           uw.get("proto-a", _BLOCK), _BLOCK)
    return pool, cover


def _att(ih="0xincident"):
    return Attestation(incident_hash=ih, loss_proof="balance-delta-proof",
                       assessor="assessor1", signature="0xsig")


# -- premium math ------------------------------------------------------------

def test_premium_tier_boundaries():
    assert Underwriter.premium_rate_bps(0.0) == 100
    assert Underwriter.premium_rate_bps(0.20) == 100   # <= 0.20: 0.5x tier
    assert Underwriter.premium_rate_bps(0.21) == 200   # 1.0x tier
    assert Underwriter.premium_rate_bps(0.50) == 200   # <= 0.50: 1.0x tier
    assert Underwriter.premium_rate_bps(0.51) == 400   # 2.0x tier


def test_premium_full_year_equals_annual_rate():
    cover = 100_000 * W
    assert Underwriter.premium_wei(cover, 365, 0.15) == cover * 100 // 10_000
    assert Underwriter.premium_wei(cover, 365, 0.35) == cover * 200 // 10_000
    assert Underwriter.premium_wei(cover, 365, 0.70) == cover * 400 // 10_000


def test_premium_scales_linearly_with_duration():
    # cover chosen so the per-day premium is an exact integer (20,000 wei)
    cover = 365_000_000
    per_day = Underwriter.premium_wei(cover, 1, 0.35)
    assert per_day == 20_000
    assert Underwriter.premium_wei(cover, 90, 0.35) == per_day * 90
    assert Underwriter.premium_wei(cover, 365, 0.35) == per_day * 365


def test_premium_rejects_nonpositive_inputs():
    with pytest.raises(MutualError):
        Underwriter.premium_wei(0, 90, 0.35)
    with pytest.raises(MutualError):
        Underwriter.premium_wei(-1, 90, 0.35)
    with pytest.raises(MutualError):
        Underwriter.premium_wei(100_000 * W, 0, 0.35)


def test_treasury_fee_rounding_is_floor():
    assert Underwriter.treasury_fee_wei(100) == 0        # 25 bps floors to 0
    assert Underwriter.treasury_fee_wei(40_000) == 100   # exact 25 bps
    assert Underwriter.treasury_fee_wei(40_001) == 100   # remainder floored


def test_engagement_premium_band_arithmetic():
    """ENGAGEMENT.md secondary form: 2.5%-3.25% of cover amount per year.

    This is a commercial term of the engagement, checked here as plain
    arithmetic on a $100k cover. The module's own tier schedule is a
    separate reference-pricing mechanism (100/200/400 bps); the sweep
    below pins that distinction so the two are never conflated.
    """
    cover = 100_000 * W
    assert cover * 250 // 10_000 == 2_500 * W    # 2.5% of cover per year
    assert cover * 325 // 10_000 == 3_250 * W    # 3.25% of cover per year
    code_rates = {Underwriter.premium_rate_bps(s / 100) for s in range(101)}
    assert code_rates == {100, 200, 400}


# -- pool accounting -----------------------------------------------------------

def test_deposit_rejects_nonpositive():
    pool = MutualPool()
    with pytest.raises(MutualError):
        pool.deposit_capital("lp1", 0)
    with pytest.raises(MutualError):
        pool.deposit_capital("lp1", -5)


def test_second_deposit_priced_pro_rata_after_premium_accrual():
    pool, cover = _pool_with_cover()
    # premium accrual raised reserves without minting shares
    assert pool.reserves_wei == 1_000_000 * W + cover.premium_wei
    assert pool.total_shares == 1_000_000 * W
    amount = 500_000 * W
    expected = amount * pool.total_shares // pool.reserves_wei
    shares = pool.deposit_capital("lp2", amount)
    assert shares == expected
    assert shares < amount  # pool appreciated, so shares price above 1:1


def test_redeem_partial_success_without_cover():
    pool = MutualPool()
    pool.deposit_capital("lp1", 1_000 * W)
    assets = pool.redeem_capital("lp1", 400 * W)
    assert assets == 400 * W
    assert pool.reserves_wei == 600 * W
    assert pool.total_shares == 600 * W


def test_redeem_rejects_overdraw_and_zero():
    pool = MutualPool()
    pool.deposit_capital("lp1", 1_000 * W)
    with pytest.raises(MutualError):
        pool.redeem_capital("lp1", 0)
    with pytest.raises(MutualError):
        pool.redeem_capital("lp1", 1_001 * W)
    with pytest.raises(MutualError):
        pool.redeem_capital("stranger", 1)


def test_buy_cover_accounting_and_cover_fields():
    pool, cover = _pool_with_cover()
    fee = Underwriter.treasury_fee_wei(cover.premium_wei)
    assert pool.treasury_collected_wei == fee
    assert pool.reserves_wei == 1_000_000 * W + cover.premium_wei
    assert pool.outstanding_cover_wei() == cover.cover_wei
    assert cover.buyer == "buyer" and cover.protocol_id == "proto-a"
    assert cover.expiry_ts - cover.start_ts == 90 * 24 * 3600
    assert cover.active


def test_buy_cover_rejects_zero_cover_and_future_score():
    uw = Underwriter()
    uw.publish("proto-a", 0.3, block=_BLOCK, signer="0xuw")
    pool = MutualPool()
    pool.deposit_capital("lp1", 1_000_000 * W)
    pool.live_underwriting = True
    rec = uw.get("proto-a", _BLOCK)
    with pytest.raises(MutualError):
        pool.buy_cover("buyer", "proto-a", 0, 90, rec, _BLOCK)
    from dataclasses import replace as dc_replace
    future = dc_replace(rec, published_block=_BLOCK + 1)
    with pytest.raises(MutualError):
        pool.buy_cover("buyer", "proto-a", 1_000 * W, 90, future, _BLOCK)


def test_outstanding_cover_drops_after_payout():
    pool, cover = _pool_with_cover()
    claim = pool.file_claim(cover.cover_id, _att(), cover.start_ts + 10)
    pool.assess_claim(claim.claim_id, {"assessor1": True, "assessor2": True})
    pool.claim_payout(claim.claim_id)
    assert not cover.active
    assert pool.outstanding_cover_wei() == 0


# -- claims handling -----------------------------------------------------------

def test_file_claim_unknown_cover_reverts():
    pool = MutualPool()
    with pytest.raises(ClaimRuleError):
        pool.file_claim("cover-999", _att(), 0.0)


def test_unapproved_claim_payout_reverts():
    pool, cover = _pool_with_cover()
    claim = pool.file_claim(cover.cover_id, _att(), cover.start_ts + 10)
    with pytest.raises(ClaimRuleError):
        pool.claim_payout(claim.claim_id)


def test_assess_majority_no_rejects_claim():
    pool, cover = _pool_with_cover()
    claim = pool.file_claim(cover.cover_id, _att(), cover.start_ts + 10)
    approved = pool.assess_claim(
        claim.claim_id, {"assessor1": False, "assessor2": True})
    assert approved is False  # 1 yes of 2 is not a majority
    with pytest.raises(ClaimRuleError):
        pool.claim_payout(claim.claim_id)


def test_claim_payout_is_idempotent():
    pool, cover = _pool_with_cover()
    claim = pool.file_claim(cover.cover_id, _att(), cover.start_ts + 10)
    pool.assess_claim(claim.claim_id, {"assessor1": True, "assessor2": True})
    first = pool.claim_payout(claim.claim_id)
    reserves_after_first = pool.reserves_wei
    assert first == cover.cover_wei
    assert pool.claim_payout(claim.claim_id) == 0
    assert pool.reserves_wei == reserves_after_first


def test_withdraw_pending_empty_is_zero():
    pool = MutualPool()
    assert pool.withdraw_pending("nobody") == 0


def test_shortfall_factor_solvent_is_one_one():
    pool, cover = _pool_with_cover()
    assert pool.shortfall_factor() == (1, 1)
    claim = pool.file_claim(cover.cover_id, _att(), cover.start_ts + 10)
    pool.assess_claim(claim.claim_id, {"assessor1": True, "assessor2": True})
    assert pool.shortfall_factor() == (1, 1)  # claim well below reserves


# -- governance / registry / misc ----------------------------------------------

def test_execute_unknown_timelock_raises():
    gov = Governance(MutualPool())
    with pytest.raises(TimelockError):
        gov.execute_param_change("never-scheduled")


def test_commitment_id_deterministic_and_sensitive():
    assert m.commitment_id("a", "b") == m.commitment_id("a", "b")
    assert m.commitment_id("a", "b") != m.commitment_id("a", "c")
    assert len(m.commitment_id("a")) == 16


def test_underwriter_publish_versions_increment():
    uw = Underwriter()
    r1 = uw.publish("proto-a", 0.3, block=100, signer="0xuw")
    r2 = uw.publish("proto-a", 0.4, block=200, signer="0xuw")
    assert (r1.version, r2.version) == (1, 2)
    assert uw.get("proto-a", 200).score == pytest.approx(0.4)
    with pytest.raises(MutualError):
        uw.get("proto-unknown", 200)


def test_assessor_registry_membership_and_weight():
    reg = AssessorRegistry()
    assert not reg.is_assessor("a1")
    reg.appoint("a1", weight=7)
    reg.appoint("a2", weight=11)
    assert reg.is_assessor("a1")
    assert reg.quorum_weight(["a1", "a2", "ghost"]) == 18


def test_pause_requires_guardian_role():
    pool = MutualPool()
    with pytest.raises(UnauthorizedError):
        pool.pause_underwriting(["UNDERWRITER_ROLE"])
    with pytest.raises(UnauthorizedError):
        pool.unpause_underwriting([])
    pool.pause_underwriting(["GUARDIAN_ROLE"])
    assert pool.paused
    pool.unpause_underwriting(["GUARDIAN_ROLE"])
    assert not pool.paused


def test_config_surface_defaults():
    # Values read at import time from the environment (see DEPLOY.md).
    assert m.RESERVE_RATIO_NUM == 130 and m.RESERVE_RATIO_DEN == 100
    assert m.MIN_CAPITAL_WEI == 200_000_000  # $200 in 6dp stablecoin wei
    assert m.FEE_BPS == 25
    assert m.TREASURY == os.getenv(
        "TREASURY_ADDRESS", "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac")
    assert m.DRY_RUN == (os.getenv("MUTUAL_DRY_RUN", "1").strip() != "0")
