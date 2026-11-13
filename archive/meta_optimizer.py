"""
Meta-Marketplace Optimizer (Goal #10)

Observes signals from:
- Barter / Coalition performance
- ZK quality attestations
- Libp2p vs A2A usage patterns
- Reputation + collusion scores

Suggests dynamic adjustments to:
- Routing weights
- Pricing multipliers
- Reputation boost formulas
- Protocol preference ordering

This is the self-improving brain of the Ultimate Ecosystem.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger("sincor.meta_optimizer")


@dataclass
class OptimizationSignal:
    source: str
    metric: str
    value: float
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MetaOptimizer:
    """Lightweight meta-optimizer that recommends configuration changes."""

    def __init__(self):
        self.signals: list[OptimizationSignal] = []
        self.recommendations: Dict[str, Any] = {}

    def ingest_signal(self, source: str, metric: str, value: float):
        signal = OptimizationSignal(source=source, metric=metric, value=value)
        self.signals.append(signal)
        logger.debug("Ingested signal from %s: %s = %.3f", source, metric, value)

    def recommend_routing_weights(self) -> Dict[str, float]:
        """Suggest updated weights for TaskRouter based on recent signals."""
        # Example heuristic logic
        base = {"capability": 0.55, "reputation": 0.30, "collusion_penalty": 0.15}

        # If we see high collusion, increase penalty weight
        recent_collusion = [s for s in self.signals[-20:] if s.metric == "collusion_score"]
        if recent_collusion and sum(s.value for s in recent_collusion) / len(recent_collusion) > 0.4:
            base["collusion_penalty"] = 0.25
            base["reputation"] = 0.25

        return base

    def recommend_pricing_adjustment(self) -> float:
        """Suggest global pricing multiplier based on market health signals."""
        recent_quality = [s for s in self.signals[-30:] if s.metric in ("quality_ema", "zk_quality")]
        if not recent_quality:
            return 1.0

        avg_quality = sum(s.value for s in recent_quality) / len(recent_quality)
        if avg_quality < 0.65:
            return 0.92  # slight discount to attract more volume
        if avg_quality > 0.88:
            return 1.08  # premium pricing for high quality
        return 1.0

    def recommend_protocol_preference(self) -> list[str]:
        """Suggest protocol ordering based on observed performance."""
        # In future: analyze latency, reliability, decentralization metrics from libp2p vs a2a
        return ["libp2p", "a2a", "mcp"]

    def get_recommendations(self) -> Dict[str, Any]:
        return {
            "routing_weights": self.recommend_routing_weights(),
            "pricing_multiplier": self.recommend_pricing_adjustment(),
            "protocol_preference": self.recommend_protocol_preference(),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }


# Singleton
_meta_optimizer: Optional[MetaOptimizer] = None


def get_meta_optimizer() -> MetaOptimizer:
    global _meta_optimizer
    if _meta_optimizer is None:
        _meta_optimizer = MetaOptimizer()
    return _meta_optimizer
