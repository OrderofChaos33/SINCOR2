"""Standalone package: P26 — Perp DEX Hedging Swarm (suite name P06).

Extracted from ``src/sincor2/defi/perp_hedge_swarm.py`` in the SINCOR2
platform tree (PACK-1; see PACKAGING.md in the package root). The core
engine lives in :mod:`perp_hedge_swarm.perp_hedge_swarm`; this package
adds only a vendored price-oracle stub and engagement-pricing helpers.

State: EXTERNAL — a buyer/counterparty is required. No hedging service
is running for outside accounts.
"""

from .perp_hedge_swarm import (
    CHECKIN_S,
    DELTA_BAND_BPS,
    DRY_RUN,
    FEE_BPS,
    PRICE_ASSETS,
    TREASURY,
    DeltaBand,
    FundingCheckpoint,
    FundingMonitor,
    GateViolationError,
    HedgeEngine,
    HedgeIntent,
    LiqBuffer,
    LiveBlockedError,
    LiveGate,
    MarginModeError,
    OraclePrice,
    PerpError,
    Position,
    PriceFeed,
    StaleOracleError,
    UnauthorizedError,
    commitment_id,
    microdollars,
    price_feed_for,
    size_short_notional,
    spot_exposure_micro,
)
from .pricing import (
    ONE_TIME_ENGAGEMENT_MICRO,
    UPKEEP_HIGH_BPS,
    UPKEEP_LOW_BPS,
    annual_upkeep_range_micro,
    median_build_annual_upkeep_micro,
)

__all__ = [
    "CHECKIN_S",
    "DELTA_BAND_BPS",
    "DRY_RUN",
    "FEE_BPS",
    "PRICE_ASSETS",
    "TREASURY",
    "DeltaBand",
    "FundingCheckpoint",
    "FundingMonitor",
    "GateViolationError",
    "HedgeEngine",
    "HedgeIntent",
    "LiqBuffer",
    "LiveBlockedError",
    "LiveGate",
    "MarginModeError",
    "OraclePrice",
    "PerpError",
    "Position",
    "PriceFeed",
    "StaleOracleError",
    "UnauthorizedError",
    "commitment_id",
    "microdollars",
    "price_feed_for",
    "size_short_notional",
    "spot_exposure_micro",
    "ONE_TIME_ENGAGEMENT_MICRO",
    "UPKEEP_HIGH_BPS",
    "UPKEEP_LOW_BPS",
    "annual_upkeep_range_micro",
    "median_build_annual_upkeep_micro",
]
