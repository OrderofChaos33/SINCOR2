"""Engagement (commercial) fee math for P23 — Intent Solver & Dark Pool.

This module implements the CATALOG engagement terms, which are distinct
from the reference code's internal settlement fee:

- Reference code (``intent_dark_pool.SettlementEngine``): 8 bps on each
  settled leg, paid to the treasury address. That is an in-simulation
  protocol fee constant (``FEE_BPS = 8``). It is unchanged by extraction.
- Engagement terms (this module, from the catalog): a secondary service
  form priced at 2 bps of order volume plus 50% of price improvement,
  capped at 0.98% of order volume. The primary form is a $127,500
  one-time engagement (see ENGAGEMENT.md); this module only covers the
  secondary per-order form.

All math is integer-only (wei / smallest unit), floor division, matching
the codebase convention. Price improvement is the execution improvement
versus the intent's limit (``min_out``) reference, measured in the same
unit as order volume by the engagement's measurement procedure; this
function takes it as an input and does not measure it.

No solver is deployed or bonded for outside flow today; this module is
arithmetic only and moves no funds.
"""

from __future__ import annotations

VOLUME_BPS = 2            # 2 bps of order volume
IMPROVEMENT_SHARE_PCT = 50  # 50% of price improvement
CAP_BPS = 98              # 0.98% of order volume == 98 bps


def engagement_fee(order_volume: int, price_improvement: int) -> int:
    """Return the engagement fee for one order, in the volume's unit.

    fee = min(volume * 2 / 10_000 + improvement * 50 / 100,
              volume * 98 / 10_000)

    Negative price improvement is treated as zero (no negative fee
    component). Negative volume raises ValueError.
    """
    if order_volume < 0:
        raise ValueError("order_volume must be >= 0")
    improvement = max(0, price_improvement)
    base = order_volume * VOLUME_BPS // 10_000
    share = improvement * IMPROVEMENT_SHARE_PCT // 100
    cap = order_volume * CAP_BPS // 10_000
    return min(base + share, cap)


def fee_breakdown(order_volume: int, price_improvement: int) -> dict:
    """Return the fee with its components, for engagement reporting."""
    if order_volume < 0:
        raise ValueError("order_volume must be >= 0")
    improvement = max(0, price_improvement)
    base = order_volume * VOLUME_BPS // 10_000
    share = improvement * IMPROVEMENT_SHARE_PCT // 100
    cap = order_volume * CAP_BPS // 10_000
    uncapped = base + share
    return {
        "order_volume": order_volume,
        "price_improvement": improvement,
        "volume_component": base,
        "improvement_component": share,
        "cap": cap,
        "capped": uncapped > cap,
        "fee": min(uncapped, cap),
    }
