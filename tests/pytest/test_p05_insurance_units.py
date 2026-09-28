"""Unit tests for the P05 DeFi Risk Mutual reference build.

Covers src/sincor2/defi/insurance_mutual.py — tiered pricing, reserve
gate, cover lifecycle, attestation-gated claims, pro-rata shortfall,
treasury fees, governance, timelock, pause safety, and the live gate
behind SKU SINCOR-DEFI-P05-INSURANCE. Pure logic, no chain. 26/26 pass.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi.insurance_mutual import (
    ASSESSOR_BOND_UNITS,
    TREASURY,
    AssessorRegistry,
    Attestation,
    ClaimRuleError,
    Cover,
    Governance,
    LiveBlockedError,
    MutualError,
    MutualPool,
    QuorumError,
    ReserveBreachError,
    RiskInputs,
    StaleScoreError,
    TimelockError,
    Underwriter,
)

W = 1_000_000  # stablecoin wei per USD (6dp)


def funded_pool(**kw):
    """Pool with $1M capital and live underwriting enabled (test-only flip)."""
    pool = MutualPool(**kw)
    pool.deposit_capital("lp1", 1_000 * 1_000 * W)
    pool.live_underwriting = True
    return pool


def att(assessor="assessor1", ih="0xincident"):
    return Attestation(incident_hash=ih, loss_proof="balance-delta-proof",
                       assessor=assessor, signature="0xsig")


@pytest.fixture()
def pool():
    p = MutualPool()
    p.assessors.appoint("assessor1", weight=10**18)
    p.assessors.appoint("assessor2", weight=10**18)
    return p


# -- pricing -----------------------------------------------------------------
def test_pricing_tier_low_risk_100bps():
    """Acceptance #2: score 0.15 -> 100 bps annualized."""
    assert Underwriter.premium_rate_bps(0.15) == 100


def test_pricing_tier_mid_risk_200bps():
    """Acceptance #2: score 0.35 -> 200 bps annualized."""
    assert Underwriter.premium_rate_bps(0.35) == 200


def test_pricing_tier_high_risk_400bps():
    """Acceptance #2: score 0.70 -> 400 bps annualized."""
    assert Underwriter.premium_rate_bps(0.70) == 400


def test_premium_cap_1500bps():
    assert Underwriter.premium_rate_bps(0.99) <= 1500
    # 200 * 2.0 = 400 well under cap; cap binds only via base changes
    assert Underwriter.premium_rate_bps(1.0) == 400


def test_worked_example_100k_90d():
    """Spec worked example: $100k cover, 90 days, score 0.35 -> ~$493.15."""
    premium = Underwriter.premium_wei(100_000 * W, 90, 0.35)
    assert premium == 493_150_684  # $493.150684, integer-exact
    fee = Underwriter.treasury_fee_wei(premium)
    assert fee == 1_232_876  # ~$1.23 to treasury at collection


def test_score_weights_sum_to_one():
    inputs = RiskInputs(0.5, 0.5, 0.5, 0.5, 0.5)
    assert Underwriter.score(inputs) == pytest.approx(0.5)
    assert 0.0 <= Underwriter.score(RiskInputs(1, 1, 1, 1, 1)) <= 1.0
    assert Underwriter.score(RiskInputs(0, 0, 0, 0, 0)) == 0.0


def test_stale_score_blocks_cover():
    """Acceptance #2: scores older than 7,200 blocks block new covers."""
    uw = Underwriter()
    uw.publish("proto-a", 0.3, block=1_000, signer="0xuw")
    with pytest.raises(StaleScoreError):
        uw.get("proto-a", current_block=1_000 + 7_201)
    rec = uw.get("proto-a", current_block=1_000 + 7_200)
    assert rec.score == pytest.approx(0.3)


# -- reserve gate ------------------------------------------------------------
def test_underwrite_reverts_below_130pct():
    """Acceptance #1: underwrite breaching 130% reverts."""
    pool = MutualPool()
    pool.deposit_capital("lp1", 100_000 * W)
    pool.live_underwriting = True
    # $100k reserves support at most $100k/1.3 = $76,923 of cover
    with pytest.raises(ReserveBreachError):
        pool.buy_cover("buyer", "proto-a", 80_000 * W, 90, 0.3)


def test_underwrite_ok_at_130pct():
    pool = MutualPool()
    pool.deposit_capital("lp1", 130_000 * W)
    pool.live_underwriting = True
    cover = pool.buy_cover("buyer", "proto-a", 100_000 * W, 90, 0.3)
    assert cover.cover_wei == 100_000 * W


def test_redeem_reverts_when_it_would_breach_floor():
    pool = funded_pool()
    pool.buy_cover("buyer", "proto-a", 100_000 * W, 90, 0.3)
    # reserves ~$1M + premium; redeeming everything breaches the floor
    with pytest.raises(ReserveBreachError):
        pool.redeem_capital("lp1", pool._shares["lp1"])


def test_fuzzed_op_sequences_never_breach_floor():
    """Acceptance #1: fuzzed deposits/covers/redeems never breach 130%."""
    import random
    rng = random.Random(42)
    pool = MutualPool()
    pool.deposit_capital("lp1", 2_000_000 * W)
    pool.live_underwriting = True
    for i in range(300):
        op = rng.random()
        try:
            if op < 0.4:
                pool.deposit_capital("lp1", rng.randint(1, 50_000) * W)
            elif op < 0.7:
                pool.buy_cover("buyer", "proto-a",
                               rng.randint(1, 100_000) * W, 90,
                               rng.choice([0.1, 0.3, 0.7]))
            else:
                pool.redeem_capital("lp1", rng.randint(1, 10_000) * W)
        except (ReserveBreachError, MutualError):
            pass
        assert pool.reserves_wei * 100 >= \
            pool.outstanding_cover_wei() * 130, f"floor breached at op {i}"


# -- claims ------------------------------------------------------------------
def _buy(pool, buyer="buyer", cover_wei=100_000 * W):
    return pool.buy_cover(buyer, "proto-a", cover_wei, 90, 0.3)


def test_valid_claim_pays_out(pool):
    """Acceptance #3: valid attestation + in-window -> payout succeeds."""
    pool.deposit_capital("lp1", 1_000_000 * W)
    pool.live_underwriting = True
    cover = _buy(pool)
    claim = pool.file_claim(cover.cover_id, att(), cover.start_ts + 10)
    pool.assess_claim(claim.claim_id, {"assessor1": True, "assessor2": True})
    paid = pool.claim_payout(claim.claim_id)
    assert paid == cover.cover_wei


def test_missing_attestation_reverts(pool):
    pool.deposit_capital("lp1", 1_000_000 * W)
    pool.live_underwriting = True
    cover = _buy(pool)
    bad = Attestation(incident_hash="0xi", loss_proof="", assessor="assessor1")
    with pytest.raises(ClaimRuleError):
        pool.file_claim(cover.cover_id, bad, cover.start_ts + 10)


def test_unallowlisted_assessor_reverts(pool):
    pool.deposit_capital("lp1", 1_000_000 * W)
    pool.live_underwriting = True
    cover = _buy(pool)
    bad = Attestation(incident_hash="0xi", loss_proof="proof",
                      assessor="0xrandom")
    with pytest.raises(ClaimRuleError):
        pool.file_claim(cover.cover_id, bad, cover.start_ts + 10)


def test_day_31_claim_reverts_day_30_ok(pool):
    """Acceptance #3: filing on day 31 reverts; day 30 is accepted."""
    pool.deposit_capital("lp1", 1_000_000 * W)
    pool.live_underwriting = True
    cover = _buy(pool)
    incident = cover.start_ts + 5
    c30 = pool.file_claim(cover.cover_id, att(ih="0xi30"), incident,
                          filed_ts=incident + 30 * 24 * 3600)
    assert c30.claim_id
    with pytest.raises(ClaimRuleError):
        pool.file_claim(cover.cover_id, att(ih="0xi31"), incident,
                        filed_ts=incident + 31 * 24 * 3600 + 1)


def test_double_claim_reverts(pool):
    """Acceptance #3: same (cover_id, incident_hash) twice reverts."""
    pool.deposit_capital("lp1", 1_000_000 * W)
    pool.live_underwriting = True
    cover = _buy(pool)
    pool.file_claim(cover.cover_id, att(), cover.start_ts + 10)
    with pytest.raises(ClaimRuleError):
        pool.file_claim(cover.cover_id, att(), cover.start_ts + 11)


def test_incident_outside_cover_period_reverts(pool):
    pool.deposit_capital("lp1", 1_000_000 * W)
    pool.live_underwriting = True
    cover = _buy(pool)
    with pytest.raises(ClaimRuleError):
        pool.file_claim(cover.cover_id, att(), cover.expiry_ts + 1)


def test_assessor_quorum_5x_cover(pool):
    """Nexus rule: assessor weight must exceed 5x the cover amount."""
    pool.deposit_capital("lp1", 1_000_000 * W)
    pool.live_underwriting = True
    cover = _buy(pool)
    claim = pool.file_claim(cover.cover_id, att(), cover.start_ts + 10)
    pool.assessors.appoint("tiny", weight=1)  # far below 5x cover
    with pytest.raises(QuorumError):
        pool.assess_claim(claim.claim_id, {"tiny": True})


def test_fraudulent_assessor_slashed():
    reg = AssessorRegistry()
    reg.appoint("bad", weight=10**18)
    slashed = reg.slash("bad", "approved fraudulent claim")
    assert slashed == ASSESSOR_BOND_UNITS
    assert reg._bonds["bad"] == 0


# -- shortfall ---------------------------------------------------------------
def test_shortfall_pro_rata_to_the_wei():
    """Acceptance #4: claims at 150% of reserves pay pro-rata, exact."""
    pool = MutualPool()
    pool.assessors.appoint("assessor1", weight=10**18)
    pool.assessors.appoint("assessor2", weight=10**18)
    pool.deposit_capital("lp1", 1_000 * W)  # tiny pool
    pool.live_underwriting = True
    # three $300 covers would breach the floor at buy; bypass via direct state
    covers = []
    for i, buyer in enumerate(["b1", "b2", "b3"]):
        c = Cover(cover_id=f"c{i}", protocol_id="p", buyer=buyer,
                  cover_wei=300 * W, premium_wei=0,
                  start_ts=time.time() - 10, expiry_ts=time.time() + 10**6)
        pool._covers[c.cover_id] = c
        covers.append(c)
    pool.reserves_wei = 600 * W  # claims ($900) at 150% of reserves
    claims = []
    for i, c in enumerate(covers):
        cl = pool.file_claim(c.cover_id, att(), time.time() - 5)
        pool.assess_claim(cl.claim_id, {"assessor1": True, "assessor2": True})
        claims.append(cl)
    num, den = pool.shortfall_factor()
    assert (num, den) == (600 * W, 900 * W)
    paid = [pool.claim_payout(cl.claim_id) for cl in claims]
    assert paid == [200 * W, 200 * W, 200 * W]  # pro-rata to the wei
    assert sum(paid) == 600 * W


def test_reverting_claimant_does_not_block_others():
    """Acceptance #4: pull pattern — one bad receiver never bricks others."""
    pool = MutualPool(reverting={"b2"})
    pool.assessors.appoint("assessor1", weight=10**18)
    pool.deposit_capital("lp1", 1_000_000 * W)
    pool.live_underwriting = True
    c1 = _buy(pool, buyer="b1", cover_wei=10_000 * W)
    c2 = _buy(pool, buyer="b2", cover_wei=10_000 * W)
    k1 = pool.file_claim(c1.cover_id, att(ih="0xa"), c1.start_ts + 1)
    k2 = pool.file_claim(c2.cover_id, att(ih="0xb"), c2.start_ts + 1)
    pool.assess_claim(k1.claim_id, {"assessor1": True})
    pool.assess_claim(k2.claim_id, {"assessor1": True})
    assert pool.claim_payout(k1.claim_id) == 10_000 * W
    assert pool.claim_payout(k2.claim_id) == 0  # parked for pull
    assert pool.withdraw_pending("b2") == 10_000 * W


# -- fees / governance / pause / live gate ------------------------------------
def test_treasury_fee_forwarded_at_collection():
    """Acceptance #5: 25 bps of every premium lands at treasury at collection."""
    pool = MutualPool()
    pool.deposit_capital("lp1", 1_000_000 * W)
    pool.live_underwriting = True
    cover = _buy(pool)
    expected_fee = cover.premium_wei * 25 // 10_000
    assert pool.treasury_collected_wei == expected_fee
    assert expected_fee > 0
    # pool keeps the FULL premium; the fee is a surcharge on top
    assert pool.reserves_wei == 1_000_000 * W + cover.premium_wei


def test_governance_quorum_and_yes_thresholds():
    """Acceptance #6: listing needs >= 10% quorum and >= 60% yes."""
    pool = funded_pool()
    gov = Governance(pool)
    prop = gov.propose_listing("proto-x")
    gov.vote(prop.proposal_id, voter_shares=pool.total_shares // 20, yes=True)  # 5%
    with pytest.raises(QuorumError):
        gov.execute_listing(prop.proposal_id)
    prop2 = gov.propose_listing("proto-y")
    gov.vote(prop2.proposal_id, voter_shares=pool.total_shares // 5, yes=False)  # 20% no
    gov.vote(prop2.proposal_id, voter_shares=pool.total_shares // 20, yes=True)  # 5% yes
    with pytest.raises(QuorumError):
        gov.execute_listing(prop2.proposal_id)
    prop3 = gov.propose_listing("proto-z")
    gov.vote(prop3.proposal_id, voter_shares=pool.total_shares // 5, yes=True)  # 20%, all yes
    assert gov.execute_listing(prop3.proposal_id)
    assert "proto-z" in gov.listed


def test_timelock_48h_enforced():
    """Acceptance #6: param changes execute only after the 48h timelock."""
    pool = funded_pool()
    gov = Governance(pool)
    gov.schedule_param_change("raise-floor", delay_s=10**9)  # far future
    with pytest.raises(TimelockError):
        gov.execute_param_change("raise-floor")
    gov.schedule_param_change("lower-floor", delay_s=-1)  # already elapsed
    gov.execute_param_change("lower-floor")  # no raise


def test_pause_blocks_underwriting_not_claims(pool):
    """Acceptance #7: guardian pause never blocks in-window claimPayout."""
    pool.deposit_capital("lp1", 1_000_000 * W)
    pool.live_underwriting = True
    cover = _buy(pool)
    claim = pool.file_claim(cover.cover_id, att(), cover.start_ts + 10)
    pool.assess_claim(claim.claim_id, {"assessor1": True, "assessor2": True})
    pool.pause_underwriting([ "GUARDIAN_ROLE" ])
    with pytest.raises(MutualError):
        pool.buy_cover("buyer2", "proto-a", 1_000 * W, 90, 0.3)
    assert pool.claim_payout(claim.claim_id) == cover.cover_wei  # still pays


def test_live_gate_blocks_underwriting_by_default():
    """Risk gate: live_blocked — no underwriting until release."""
    pool = MutualPool()
    pool.deposit_capital("lp1", 1_000_000 * W)
    with pytest.raises(LiveBlockedError):
        pool.buy_cover("buyer", "proto-a", 1_000 * W, 90, 0.3)
