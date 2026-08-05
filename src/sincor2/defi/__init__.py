"""SINCOR DeFi package — yield aggregation, strategy adapters, risk-gated execution."""

from .yield_aggregator import (
    StrategyAllocation,
    YieldAggregator,
    YieldStrategy,
    get_default_aggregator,
)

__all__ = [
    "StrategyAllocation",
    "YieldAggregator",
    "YieldStrategy",
    "get_default_aggregator",
]
