#!/usr/bin/env python3
"""
Autonomous Rebalancer Agent

Smart profit splitting and dynamic capital allocation.
No fixed percentages. Performance-driven rebalancing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


@dataclass
class StrategyPerformance:
    total_pnl: float = 0.0
    trades: int = 0
    win_rate: float = 0.5
    recent_edge: float = 0.0


class AutonomousRebalancer:
    """
    Autonomous capital allocation and profit splitting agent.
    
    Core idea: Allocate more capital to what is working (positive expectancy + good calibration).
    Reduce or pause what is underperforming.
    No manual dials.
    """

    def __init__(self, total_bankroll: float = 10000.0):
        self.total_bankroll = total_bankroll
        self.strategy_performance: Dict[str, StrategyPerformance] = {}
        self.allocation: Dict[str, float] = {}  # strategy -> fraction
        self.min_allocation = 0.05
        self.max_allocation = 0.35

    def update_performance(self, strategy_id: str, pnl: float, was_win: bool, edge: float):
        """Update performance record for a strategy."""
        if strategy_id not in self.strategy_performance:
            self.strategy_performance[strategy_id] = StrategyPerformance()

        perf = self.strategy_performance[strategy_id]
        perf.total_pnl += pnl
        perf.trades += 1
        perf.recent_edge = edge

        # Simple exponential win rate
        alpha = 0.2
        perf.win_rate = alpha * (1.0 if was_win else 0.0) + (1 - alpha) * perf.win_rate

    def rebalance(self) -> Dict[str, float]:
        """
        Calculate new allocations based on recent performance.
        Stronger recent edge + positive PnL gets more capital.
        """
        if not self.strategy_performance:
            # Default equal split if no data
            return {"default": 1.0}

        scores = {}
        for sid, perf in self.strategy_performance.items():
            # Score = edge * win_rate * (1 + normalized pnl)
            score = max(perf.recent_edge, 0.001) * perf.win_rate * (1 + min(perf.total_pnl / 1000, 2))
            scores[sid] = max(score, 0.001)

        total_score = sum(scores.values())
        new_alloc = {}

        for sid, score in scores.items():
            fraction = score / total_score
            # Clamp
            fraction = max(min(fraction, self.max_allocation), self.min_allocation)
            new_alloc[sid] = round(fraction, 4)

        # Normalize to sum to 1.0
        s = sum(new_alloc.values())
        for sid in new_alloc:
            new_alloc[sid] = round(new_alloc[sid] / s, 4)

        self.allocation = new_alloc
        logger.info(f"[REBALANCER] New allocation: {new_alloc}")
        return new_alloc

    def get_allocation_for(self, strategy_id: str) -> float:
        return self.allocation.get(strategy_id, 0.15)
