"""P21 — Yield Aggregator Vault (suite name P01), standalone package.

Extracted from ``src/sincor2/defi/yield_aggregator.py`` in the SINCOR2
platform repo (see PACKAGING.md for provenance and the extraction
record). Routes deposits across yield sources inside the SINCOR
platform. Exists as a platform module; not a standalone product.
State: EXTERNAL — a buyer/counterparty and a signed engagement are
required before any deployment (see ENGAGEMENT.md).
"""

from .fees import (
    MANAGEMENT_FEE_RATE,
    PERFORMANCE_FEE_RATE,
    engagement_fee_breakdown,
    management_fee,
    performance_fee,
)
from .yield_aggregator import (
    DEFAULT_STRATEGIES,
    RebalancePlan,
    StrategyAllocation,
    StrategyKind,
    YieldAggregator,
    YieldStrategy,
    get_default_aggregator,
)

__all__ = [
    "DEFAULT_STRATEGIES",
    "MANAGEMENT_FEE_RATE",
    "PERFORMANCE_FEE_RATE",
    "RebalancePlan",
    "StrategyAllocation",
    "StrategyKind",
    "YieldAggregator",
    "YieldStrategy",
    "engagement_fee_breakdown",
    "get_default_aggregator",
    "management_fee",
    "performance_fee",
]

__version__ = "0.1.0"
