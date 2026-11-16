"""Engagement pricing constants and upkeep arithmetic (PACK-2/PACK-4).

Packaging-added helper — this file is NOT part of the original platform
module. It encodes only the catalogued commercial terms for P26 (suite
name P06) so the upkeep service form can be tested arithmetically:

- One-time engagement: $17,500.
- Secondary service form: upkeep at 15%-25% of build cost per year.
  On a $17,500 build that is $2,625-$4,375 per year.

All amounts are integer microdollars (1 USD = 1,000,000), matching the
integer-only money convention of the extracted module. No other price,
discount, or term is implied by this helper.
"""

from __future__ import annotations

from typing import Tuple

ONE_TIME_ENGAGEMENT_MICRO = 17_500_000_000  # $17,500 in microdollars
UPKEEP_LOW_BPS = 1_500   # 15% of build cost per year
UPKEEP_HIGH_BPS = 2_500  # 25% of build cost per year


def annual_upkeep_range_micro(build_cost_micro: int) -> Tuple[int, int]:
    """Return (low, high) annual upkeep in microdollars for a build cost.

    Low is 15% and high is 25% of ``build_cost_micro``, integer-exact
    (floor division, the module's rounding convention).
    """
    if build_cost_micro < 0:
        raise ValueError("build cost must be non-negative")
    low = build_cost_micro * UPKEEP_LOW_BPS // 10_000
    high = build_cost_micro * UPKEEP_HIGH_BPS // 10_000
    return low, high


def median_build_annual_upkeep_micro() -> Tuple[int, int]:
    """Upkeep range on the catalogued $17,500 build: ($2,625, $4,375)."""
    return annual_upkeep_range_micro(ONE_TIME_ENGAGEMENT_MICRO)
