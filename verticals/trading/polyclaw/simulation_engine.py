#!/usr/bin/env python3
"""
High-fidelity Simulation Engine for Polyclaw Autonomous Trading System

Supports fast backtesting and realistic paper trading mode.
No manual dials - all parameters adaptive where possible.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class SimulatedFill:
    market_id: str
    side: str
    size: float
    fill_price: float
    slippage: float
    fee: float
    timestamp: str
    status: str = "filled"


@dataclass
class SimulationState:
    bankroll: float = 10000.0
    positions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    realized_pnl: float = 0.0
    total_fees: float = 0.0
    trades: List[Dict[str, Any]] = field(default_factory=list)
    max_drawdown: float = 0.0
    peak_bankroll: float = 10000.0


class SimulationEngine:
    """
    High-fidelity simulation for prediction market trading.
    
    Features:
    - Realistic slippage and fee modeling
    - Outcome resolution simulation
    - Full P&L and risk metrics
    - Fast backtest mode and step-by-step paper mode
    - Designed for autonomous agent loops
    """

    def __init__(
        self,
        initial_bankroll: float = 10000.0,
        fee_rate: float = 0.005,  # 0.5% round trip typical for PM
        slippage_model: str = "adaptive",
        seed: Optional[int] = None,
    ):
        self.state = SimulationState(bankroll=initial_bankroll, peak_bankroll=initial_bankroll)
        self.fee_rate = fee_rate
        self.slippage_model = slippage_model
        self.rng = random.Random(seed)
        self.current_time = datetime.now(timezone.utc)
        self.historical_markets: List[Dict[str, Any]] = []
        self.resolved_markets: Dict[str, bool] = {}  # market_id -> outcome (True=Yes win)

    def load_historical_markets(self, markets: List[Dict[str, Any]]):
        """Load historical or synthetic market data for replay."""
        self.historical_markets = markets
        logger.info(f"[SIM] Loaded {len(markets)} markets for simulation")

    def _calculate_slippage(self, market: Dict[str, Any], side: str, size: float) -> float:
        """Adaptive slippage model based on market liquidity and order size."""
        base_slippage = 0.002  # 0.2% base
        liquidity = market.get("liquidity", 100000)
        impact = min(size / max(liquidity, 1000) * 0.01, 0.015)
        return base_slippage + impact

    def submit_order(
        self, market: Dict[str, Any], side: str, size: float
    ) -> SimulatedFill:
        """Submit order and get realistic fill."""
        current_price = float(market.get("probability", 0.5))
        slippage = self._calculate_slippage(market, side, size)

        if side == "buy_yes":
            fill_price = min(current_price + slippage, 0.99)
        else:
            fill_price = max(current_price - slippage, 0.01)

        fee = size * self.fee_rate

        fill = SimulatedFill(
            market_id=str(market.get("id")),
            side=side,
            size=round(size, 2),
            fill_price=round(fill_price, 4),
            slippage=round(slippage, 5),
            fee=round(fee, 2),
            timestamp=self.current_time.isoformat(),
        )

        # Update state
        self.state.bankroll -= fee
        self.state.total_fees += fee

        # Track position (simplified for now)
        mid = fill.market_id
        if mid not in self.state.positions:
            self.state.positions[mid] = {"size": 0.0, "avg_price": 0.0, "side": side}

        pos = self.state.positions[mid]
        pos["size"] += size if side.startswith("buy") else -size
        # Update avg price (simple)
        if pos["size"] != 0:
            pos["avg_price"] = fill_price

        self.state.trades.append({
            "market_id": mid,
            "side": side,
            "size": size,
            "fill_price": fill_price,
            "fee": fee,
            "timestamp": fill.timestamp,
        })

        return fill

    def resolve_market(self, market_id: str, outcome_yes: bool):
        """Resolve a market and realize P&L."""
        if market_id not in self.state.positions:
            return

        pos = self.state.positions[market_id]
        size = pos["size"]
        avg_price = pos["avg_price"]

        if size > 0:  # long Yes
            pnl = size * (1.0 if outcome_yes else 0.0) - (size * avg_price)
        else:  # short / Yes sell
            pnl = abs(size) * (0.0 if outcome_yes else 1.0) - (abs(size) * (1.0 - avg_price))

        self.state.realized_pnl += pnl
        self.state.bankroll += pnl

        # Track drawdown
        if self.state.bankroll > self.state.peak_bankroll:
            self.state.peak_bankroll = self.state.bankroll
        drawdown = (self.state.peak_bankroll - self.state.bankroll) / self.state.peak_bankroll
        if drawdown > self.state.max_drawdown:
            self.state.max_drawdown = drawdown

        self.resolved_markets[market_id] = outcome_yes
        logger.info(f"[SIM] Resolved {market_id} | PnL: {pnl:.2f} | Bankroll: {self.state.bankroll:.2f}")

    def get_metrics(self) -> Dict[str, Any]:
        """Return current performance metrics."""
        return {
            "bankroll": round(self.state.bankroll, 2),
            "realized_pnl": round(self.state.realized_pnl, 2),
            "total_fees": round(self.state.total_fees, 2),
            "max_drawdown": round(self.state.max_drawdown, 4),
            "trade_count": len(self.state.trades),
            "open_positions": len([p for p in self.state.positions.values() if p["size"] != 0]),
        }

    def run_fast_backtest(self, markets: List[Dict[str, Any]], agent_fn) -> Dict[str, Any]:
        """Run fast backtest using provided agent decision function."""
        self.load_historical_markets(markets)
        results = []

        for market in markets:
            decision = agent_fn(market)
            if decision and decision.get("edge", 0) > 0.03:  # min edge filter
                fill = self.submit_order(market, decision["side"], decision.get("size", 100))
                # Simulate resolution (in real backtest this would come from data)
                outcome = self.rng.random() < market.get("true_probability", 0.5)
                self.resolve_market(fill.market_id, outcome)
                results.append({
                    "market_id": fill.market_id,
                    "decision": decision,
                    "fill": fill.__dict__,
                    "outcome": outcome,
                })

        return {
            "final_metrics": self.get_metrics(),
            "trades": results,
        }
