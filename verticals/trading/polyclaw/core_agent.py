#!/usr/bin/env python3
"""
Polyclaw Core Trading Agent

Cutting-edge autonomous trading logic for prediction markets.
Focus: Persistent edge through calibration discipline + adaptive risk.
No manual dials. Designed to work with autonomous rebalancer and observer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class TradeDecision:
    market_id: str
    side: str
    size: float
    edge: float
    confidence: float
    rationale: str
    expected_value: float


class PolyclawCoreAgent:
    """
    Core autonomous trading agent.
    
    Key features for long-term edge:
    - Strict positive EV filter after costs
    - Adaptive Kelly with portfolio heat awareness
    - Simple but effective calibration tracking
    - Clean decision interface for higher-level agents
    """

    def __init__(self, name: str = "core-01"):
        self.name = name
        self.win_rate_ema: float = 0.52
        self.learning_rate: float = 0.15
        self.min_edge_after_cost: float = 0.025  # 2.5% minimum edge after fees/slippage
        self.max_single_position_pct: float = 0.12

    def evaluate_market(
        self, market: Dict[str, Any], model_probability: float
    ) -> Optional[TradeDecision]:
        """Core evaluation. Returns decision only if edge is compelling."""
        market_price = float(market.get("probability", 0.5))
        edge = model_probability - market_price

        # Only trade if edge clears the bar after realistic costs
        if abs(edge) < self.min_edge_after_cost:
            return None

        side = "buy_yes" if edge > 0 else "buy_no"

        # Simple but effective Kelly with guardrail
        kelly = self._half_kelly(model_probability, market_price)
        size = min(kelly, self.max_single_position_pct)

        confidence = min(abs(edge) * 4 + self.win_rate_ema * 0.3, 0.95)

        rationale = (
            f"Model prob {model_probability:.1%} vs market {market_price:.1%} | "
            f"Edge {edge:.1%} | Kelly {kelly:.1%}"
        )

        return TradeDecision(
            market_id=str(market.get("id")),
            side=side,
            size=round(size, 4),
            edge=round(edge, 5),
            confidence=round(confidence, 4),
            rationale=rationale,
            expected_value=round(edge * size, 4),
        )

    def _half_kelly(self, model_prob: float, market_price: float) -> float:
        """Conservative half-Kelly with bounds."""
        if market_price <= 0 or market_price >= 1:
            return 0.0
        b = (1.0 / market_price) - 1.0 if market_price > 0 else 0
        kelly = (model_prob * (b + 1) - 1) / b if b > 0 else 0
        return max(min(kelly * 0.5, self.max_single_position_pct), 0.0)

    def update_from_outcome(self, was_correct: bool):
        """Self-improvement: update internal calibration model."""
        observation = 1.0 if was_correct else 0.0
        self.win_rate_ema = (
            self.learning_rate * observation
            + (1 - self.learning_rate) * self.win_rate_ema
        )
        self.win_rate_ema = max(min(self.win_rate_ema, 0.85), 0.35)

    def get_status(self) -> Dict[str, Any]:
        return {
            "agent": self.name,
            "win_rate_ema": round(self.win_rate_ema, 4),
            "min_edge_threshold": self.min_edge_after_cost,
        }
