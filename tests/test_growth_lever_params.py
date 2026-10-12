"""Internal-consistency checks for the growth-lever PROPOSAL constants.

These tests assert only that the proposal numbers in
``src/sincor2/distribution/growth_lever_params.py`` are internally
consistent with ``docs/distribution/growth-levers-proposal.md``. They test
no behavior — the module is inert by design (nothing imports it).
"""

from sincor2.distribution.growth_lever_params import (
    NET_FEE_FLOOR_BPS,
    PLATFORM_FEE_BPS,
    REBATE_TIERS,
    REBATE_EXPENSE_CEILING_RATIO,
    REFERRAL_EXPENSE_CEILING_RATIO,
    REFERRAL_TERMS,
    SPONSORED_STAKE_TRANCHE,
)


def test_tranche_math():
    t = SPONSORED_STAKE_TRANCHE
    assert t.per_agent_front_axm == 15.0            # 10 KYA + 5 stake allowance
    assert t.pilot_cost_axm == 3_750.0             # 250 agents x 15
    assert t.max_agents_outstanding == 1_000       # 15,000 / 15
    assert t.pilot_cost_axm <= t.tranche_cap_outstanding_axm
    assert t.pool_available_axm == 18_916.50       # 20,000 - 1,083.50 catalog
    assert t.tranche_cap_outstanding_axm <= t.pool_available_axm


def test_rebate_tiers_monotonic():
    # higher volume -> lower-or-equal effective fee, never below the floor
    prev_min = -1
    prev_fee = PLATFORM_FEE_BPS + 1
    for tier in REBATE_TIERS:
        assert tier.min_trailing_30d_settled_axm > prev_min
        assert tier.effective_fee_bps <= prev_fee
        assert tier.effective_fee_bps >= NET_FEE_FLOOR_BPS
        assert 0 <= tier.rebate_bps <= PLATFORM_FEE_BPS - NET_FEE_FLOOR_BPS
        prev_min, prev_fee = tier.min_trailing_30d_settled_axm, tier.effective_fee_bps
    # gross fee is untouched by rebates
    assert PLATFORM_FEE_BPS == 500


def test_rebate_ceiling_below_max_marginal_rebate():
    # worst marginal rebate is 300 bps of the 500 bps fee = 60% of marginal;
    # the monthly kill ceiling must be stricter than the degenerate case
    max_marginal = (PLATFORM_FEE_BPS - NET_FEE_FLOOR_BPS) / PLATFORM_FEE_BPS
    assert REBATE_EXPENSE_CEILING_RATIO < max_marginal


def test_referral_self_funding_bound():
    # expense = 50% of fees from referred agents <= 50% of gross even if every
    # agent was referred; the kill ceiling must sit below that degenerate bound
    degenerate_max = REFERRAL_TERMS.credit_rate_of_fees
    assert REFERRAL_EXPENSE_CEILING_RATIO < degenerate_max
    # cap reachable exactly when referred agent generated 50 AXM in fees
    # (1,000 AXM settled volume x 5%)
    fees_to_hit_cap = (
        REFERRAL_TERMS.cap_axm_per_referral / REFERRAL_TERMS.credit_rate_of_fees
    )
    assert fees_to_hit_cap == 50.0
    assert fees_to_hit_cap / 0.05 == 1_000.0


def test_scenario_c_projection():
    # §3.3 scenario C: 100,000 AXM volume, mix 50/30/15/5 across T0..T3
    tiers = {t.name: t for t in REBATE_TIERS}
    rebate = (
        30_000 * tiers["T1_active"].rebate_bps
        + 15_000 * tiers["T2_power"].rebate_bps
        + 5_000 * tiers["T3_anchor"].rebate_bps
    ) / 10_000
    assert rebate == 750.0
    assert rebate / (100_000 * 0.05) == 0.15
