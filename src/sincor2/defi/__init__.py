"""SINCOR DeFi package — 26-protocol OS, yield aggregation, risk-gated execution."""

from .catalog import (
    PROTOCOL_BY_ID,
    PROTOCOL_BY_SWARM,
    PROTOCOLS,
    ProtocolSpec,
    assert_catalog_complete,
)
from .engine import DeFiProtocolOS, ProtocolTick, SwarmSubmission, run_all_swarms
from .yield_aggregator import (
    StrategyAllocation,
    YieldAggregator,
    YieldStrategy,
    get_default_aggregator,
)

__all__ = [
    "PROTOCOL_BY_ID",
    "PROTOCOL_BY_SWARM",
    "PROTOCOLS",
    "ProtocolSpec",
    "assert_catalog_complete",
    "DeFiProtocolOS",
    "ProtocolTick",
    "SwarmSubmission",
    "run_all_swarms",
    "StrategyAllocation",
    "YieldAggregator",
    "YieldStrategy",
    "get_default_aggregator",
]
