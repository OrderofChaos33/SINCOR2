"""Growth-lever PROPOSAL constants — design only, NOT wired to any live path.

This module is a frozen mirror of the numbers proposed in
``docs/distribution/growth-levers-proposal.md`` (§§2–4), for adversarial
reviewer cross-checking. It is intentionally inert:

* no module in the codebase imports it,
* no route, ledger, or scheduler reads it,
* it changes zero runtime behavior.

Do NOT import this from production code until the founder ratifies the
proposal and a real implementation plan exists. When that happens, delete
this file or promote its values through the normal config path
(environment variables + code review), never by wiring this module in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


# ---------------------------------------------------------------------------
# Mechanic A — sponsored-stake first-task budget (§2)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SponsoredStakeTranche:
    kya_front_axm: float = 10.0          # fixed KYA verification stake
    first_task_stake_allowance_axm: float = 5.0  # covers 50% lock on tasks ≤ 10 AXM bounty
    pilot_agents: int = 250
    tranche_cap_outstanding_axm: float = 15_000.0
    pool_reserve_axm: float = 20_000.0
    pool_catalog_earmarked_axm: float = 1_083.50

    @property
    def per_agent_front_axm(self) -> float:
        return self.kya_front_axm + self.first_task_stake_allowance_axm

    @property
    def pilot_cost_axm(self) -> float:
        return self.pilot_agents * self.per_agent_front_axm

    @property
    def max_agents_outstanding(self) -> int:
        return int(self.tranche_cap_outstanding_axm // self.per_agent_front_axm)

    @property
    def pool_available_axm(self) -> float:
        return self.pool_reserve_axm - self.pool_catalog_earmarked_axm


SPONSORED_STAKE_TRANCHE = SponsoredStakeTranche()


# ---------------------------------------------------------------------------
# Mechanic B — fee rebates (§3)
# ---------------------------------------------------------------------------

PLATFORM_FEE_BPS = 500  # locked policy; rebates never change this


@dataclass(frozen=True)
class RebateTier:
    name: str
    min_trailing_30d_settled_axm: float
    effective_fee_bps: int  # what the agent effectively pays after rebate

    @property
    def rebate_bps(self) -> int:
        return PLATFORM_FEE_BPS - self.effective_fee_bps


REBATE_TIERS: Tuple[RebateTier, ...] = (
    RebateTier("T0_standard", 0, 500),
    RebateTier("T1_active", 100, 400),
    RebateTier("T2_power", 1_000, 300),
    RebateTier("T3_anchor", 10_000, 200),  # + founder approval per agent
)

NET_FEE_FLOOR_BPS = 200          # no agent below 2.00% without founder exception
REBATE_VESTING_DAYS = 30
REBATE_EXPENSE_CEILING_RATIO = 0.20   # kill: rebates > 20% of gross fees / month
T3_VOLUME_CONCENTRATION_KILL = 0.40   # kill: > 40% of settled volume in T3
WASH_VOLUME_KILL_RATIO = 0.05         # kill: wash/self-dealt > 5% of tier volume


# ---------------------------------------------------------------------------
# Mechanic C — referral credit (§4)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReferralTerms:
    credit_rate_of_fees: float = 0.50   # 50% of platform fees from referred agent
    cap_axm_per_referral: float = 25.0
    vesting: str = "fees_recorded_on_settled_tasks_only"
    payout_cadence: str = "monthly"
    release_vesting_days: int = 30


REFERRAL_TERMS = ReferralTerms()

REFERRAL_SETTLE_THROUGH_KILL = 0.10  # kill: < 10% referred registered -> settled
REFERRAL_EXPENSE_CEILING_RATIO = 0.25  # kill: referral expense > 25% of gross / month
REFERRAL_MANUAL_REVIEW_PER_MONTH = 50  # > 50 referrals/mo by one referrer -> review
