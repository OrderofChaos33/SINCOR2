"""Engagement service-fee accounting: 15% of earned LP trading fees.

This module implements the SECONDARY SERVICE FORM of the P22 engagement
terms (see ENGAGEMENT.md): when a counterparty engagement is priced on
the service form, the service fee is 15% of the LP trading fees actually
earned by the managed positions during the engagement period.

Keep this distinct from two other numbers in the extracted core module
(``clmm_manager.py``) — they are different quantities and must never be
conflated in reporting:

- ``TREASURY_CUT_BPS = 15`` in the core module is 15 *basis points*
  (0.15%) of *captured JIT proceeds*, routed to the treasury by
  ``ProceedsDistributor``. It is internal protocol math.
- This module's 15% (1500 bps) is the *engagement-level* service fee on
  *earned LP trading fees*. It is commercial accounting, computed
  off-chain from the counterparty's reported/verified earned-fee totals.

All math is integer-exact (wei): ``fee = floor(earned * 1500 / 10_000)``.
No fee accrues on unearned, projected, or marked amounts — only on fees
recorded as earned via :meth:`ServiceFeeLedger.record_earned`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

SERVICE_FEE_BPS = 1500  # 15% of earned LP trading fees (engagement form)
BPS_DENOMINATOR = 10_000


def service_fee_wei(earned_lp_fees_wei: int) -> int:
    """Service fee owed on a given amount of earned LP trading fees."""
    if earned_lp_fees_wei < 0:
        raise ValueError("earned LP fees cannot be negative")
    return earned_lp_fees_wei * SERVICE_FEE_BPS // BPS_DENOMINATOR


def net_to_counterparty_wei(earned_lp_fees_wei: int) -> int:
    """Earned LP trading fees remaining after the 15% service fee."""
    return earned_lp_fees_wei - service_fee_wei(earned_lp_fees_wei)


@dataclass
class ServiceFeeLedger:
    """Per-pool running ledger of earned LP fees and accrued service fees.

    Recording is append-only per pool; the accrued service fee is always
    exactly ``service_fee_wei(total_earned)`` recomputed from the total,
    so partial recordings never drift from the headline 15% rate.
    """

    _earned_by_pool: Dict[str, int] = field(default_factory=dict)

    def record_earned(self, pool_id: str, earned_wei: int) -> int:
        """Record LP trading fees actually earned. Returns service fee delta."""
        if earned_wei < 0:
            raise ValueError("earned LP fees cannot be negative")
        before = self.service_fee_wei(pool_id)
        self._earned_by_pool[pool_id] = self._earned_by_pool.get(pool_id, 0) + earned_wei
        return self.service_fee_wei(pool_id) - before

    def earned_wei(self, pool_id: str) -> int:
        return self._earned_by_pool.get(pool_id, 0)

    def service_fee_wei(self, pool_id: str) -> int:
        return service_fee_wei(self.earned_wei(pool_id))

    def net_wei(self, pool_id: str) -> int:
        return self.earned_wei(pool_id) - self.service_fee_wei(pool_id)

    def totals(self) -> Dict[str, int]:
        earned = sum(self._earned_by_pool.values())
        fee = service_fee_wei(earned)
        return {
            "earned_lp_fees_wei": earned,
            "service_fee_wei": fee,
            "net_wei": earned - fee,
            "service_fee_bps": SERVICE_FEE_BPS,
        }
