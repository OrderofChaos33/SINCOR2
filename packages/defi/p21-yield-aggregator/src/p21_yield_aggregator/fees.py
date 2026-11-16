"""Engagement fee accounting for P21 — Yield Aggregator Vault (suite P01).

These are the *commercial* terms of a counterparty engagement, kept
deliberately separate from the allocator's internal protocol fee:

- Engagement performance fee: 10% of positive gross yield (profit only).
- Engagement management fee: 0% — no charge on assets under management,
  for any period.
- One-time engagement price ($120,000) is a contract term, not computed
  here; see ENGAGEMENT.md.

Distinct from the code: ``YieldAggregator.fee_to_treasury_bps`` is 10 bps
(0.10%), an internal protocol-fee concept the allocator stamps on plans
and live intents. It is not the engagement performance fee and the two
must never be conflated in a counterparty document.

All functions are pure arithmetic on USD amounts. No chain access.
"""

from __future__ import annotations

from typing import Any, Dict

PERFORMANCE_FEE_RATE = 0.10  # 10% of positive gross yield
MANAGEMENT_FEE_RATE = 0.0  # 0% of AUM, always


def performance_fee(gross_yield_usd: float) -> float:
    """10% of positive gross yield; zero when yield is zero or negative."""
    if gross_yield_usd <= 0:
        return 0.0
    return round(gross_yield_usd * PERFORMANCE_FEE_RATE, 6)


def management_fee(aum_usd: float, periods: float = 1.0) -> float:
    """0% management fee: always exactly zero, for any AUM and period."""
    return 0.0


def engagement_fee_breakdown(
    capital_usd: float, gross_yield_usd: float
) -> Dict[str, Any]:
    """Full engagement fee view for one yield period.

    Returns gross yield, each fee line, and the net yield after fees.
    The allocator's internal 10 bps treasury fee is reported separately
    by ``YieldAggregator.simulate_year_pnl`` and is not deducted here.
    """
    perf = performance_fee(gross_yield_usd)
    mgmt = management_fee(capital_usd)
    return {
        "capital_usd": capital_usd,
        "gross_yield_usd": round(gross_yield_usd, 6),
        "performance_fee_rate": PERFORMANCE_FEE_RATE,
        "performance_fee_usd": perf,
        "management_fee_rate": MANAGEMENT_FEE_RATE,
        "management_fee_usd": mgmt,
        "total_fees_usd": round(perf + mgmt, 6),
        "net_yield_usd": round(gross_yield_usd - perf - mgmt, 6),
    }
