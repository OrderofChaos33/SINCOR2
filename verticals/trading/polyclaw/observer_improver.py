#!/usr/bin/env python3
"""
Observer + Improver Agent

Constant observation of system performance.
Detects problems and triggers improvements.
This is the piece that enables long-term compounding advantage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


@dataclass
class SystemHealth:
    calibration_error: float = 0.0
    recent_win_rate: float = 0.5
    max_drawdown: float = 0.0
    expectancy: float = 0.0
    regime: str = "normal"


class ObserverImproverAgent:
    """
    Meta-agent that watches the entire trading system.
    
    Responsibilities:
    - Track calibration (are our probabilities honest?)
    - Detect regime shifts
    - Identify decaying strategies
    - Suggest or trigger adjustments (via rebalancer or core agent params)
    
    This agent is what makes the system get better over time autonomously.
    """

    def __init__(self):
        self.health = SystemHealth()
        self.observations: List[Dict[str, Any]] = []

    def observe_cycle(self, metrics: Dict[str, Any], recent_trades: List[Dict[str, Any]]):
        """Run observation on latest cycle results."""
        self.health.max_drawdown = metrics.get("max_drawdown", 0.0)

        if recent_trades:
            wins = sum(1 for t in recent_trades if t.get("correct", False))
            self.health.recent_win_rate = wins / len(recent_trades)

        # Simple calibration check (placeholder - real version would use proper scoring)
        self.health.calibration_error = abs(self.health.recent_win_rate - 0.55)

        # Very basic regime detection
        if self.health.max_drawdown > 0.25:
            self.health.regime = "high_risk"
        elif self.health.recent_win_rate < 0.42:
            self.health.regime = "poor_calibration"
        else:
            self.health.regime = "normal"

        self.observations.append({
            "metrics": metrics,
            "health": self.health.__dict__,
        })

        if self.health.regime != "normal":
            logger.warning(f"[OBSERVER] Regime detected: {self.health.regime}")

    def suggest_improvements(self) -> Dict[str, Any]:
        """Return recommended adjustments for other agents."""
        suggestions = {}

        if self.health.regime == "poor_calibration":
            suggestions["core_agent"] = {"increase_edge_threshold": True}
            suggestions["rebalancer"] = {"reduce_risk": True}

        if self.health.max_drawdown > 0.20:
            suggestions["rebalancer"] = {"reduce_exposure": True}

        return suggestions

    def get_health_report(self) -> Dict[str, Any]:
        return {
            "regime": self.health.regime,
            "recent_win_rate": round(self.health.recent_win_rate, 3),
            "max_drawdown": round(self.health.max_drawdown, 4),
            "calibration_error": round(self.health.calibration_error, 4),
        }
