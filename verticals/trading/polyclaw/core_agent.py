#!/usr/bin/env python3
"""
Polyclaw Core Trading Agent

Cutting-edge autonomous trading logic for prediction markets.
Focus: Persistent edge through calibration discipline + adaptive risk.
No manual dials. Designed to work with autonomous rebalancer and observer.

DeFi integration: evaluates SharedLiquidityVault drawdown opportunities for
high-EV Polyclaw positions or lending loops, and feeds settleUp outcomes
back into calibration (win_rate_ema) and the observer/improver loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .vault_client import VaultClient, VaultState, BPS_DENOMINATOR

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


@dataclass
class YieldOpportunity:
    """A proposed vault drawdown funding a high-EV position or lending loop."""
    kind: str                    # "polyclaw_position" | "lending_loop"
    strategy_id: int
    lp: str
    token: str
    drawdown_amount: float       # human units; <= availableDraw at proposal time
    expected_value: float        # EV net of funding cost, human units
    edge: float
    drawdown_calldata: str       # paste-ready, executed via the strategy hook
    rationale: str
    market_id: Optional[str] = None
    trade: Optional[TradeDecision] = None


@dataclass
class SettlementRecord:
    strategy_id: int
    lp: str
    token: str
    principal: float
    fee: float
    protocol_fee_bps: int
    was_correct: bool
    settle_calldata: str


class PolyclawCoreAgent:
    """
    Core autonomous trading agent.

    Key features for long-term edge:
    - Strict positive EV filter after costs
    - Adaptive Kelly with portfolio heat awareness
    - Simple but effective calibration tracking
    - Clean decision interface for higher-level agents
    - SharedLiquidityVault drawdown sizing that cannot revert on-chain
    """

    def __init__(self, name: str = "core-01", vault_client: Optional[VaultClient] = None):
        self.name = name
        self.vault = vault_client
        self.win_rate_ema: float = 0.52
        self.learning_rate: float = 0.15
        self.min_edge_after_cost: float = 0.025  # 2.5% minimum edge after fees/slippage
        self.max_single_position_pct: float = 0.12
        # Max fraction of a lane's availableDraw a single proposal may consume.
        # Leaves headroom so concurrent strategies don't race the same capital.
        self.max_draw_utilization: float = 0.50
        self.protocol_fee_bps: int = 1000  # 10% of strategy fee to treasury
        self.settlements: List[SettlementRecord] = []

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

    # ------------------------------------------------------------------
    # DeFi: SharedLiquidityVault integration
    # ------------------------------------------------------------------
    def evaluate_yield_opportunity(
        self,
        market: Optional[Dict[str, Any]],
        model_probability: Optional[float],
        vault_state: VaultState,
        bankroll: float,
        lending_loop_apr: float = 0.0,
        funding_cost_apr: float = 0.0,
    ) -> Optional[YieldOpportunity]:
        """
        Propose a vault drawdown for a high-EV Polyclaw position, or a lending
        loop when no market edge exists. Queries caps/outstanding via the lane
        state and never proposes more than availableDraw — a proposal larger
        than that would revert on-chain, so the gate lives here.

        Returns None when nothing clears the EV bar.
        """
        if self.vault is None:
            return None

        available = self.vault.available_draw(vault_state)
        if available <= 0:
            return None

        # --- Path 1: high-EV Polyclaw position funded by drawdown ----------
        if market is not None and model_probability is not None:
            trade = self.evaluate_market(market, model_probability)
            if trade is not None:
                position_notional = bankroll * trade.size
                amount = min(
                    position_notional,
                    available * self.max_draw_utilization,
                )
                if amount > 0:
                    # EV = edge * notional, net of funding cost on the drawdown
                    gross_ev = trade.edge * amount
                    funding_cost = amount * funding_cost_apr
                    net_ev = gross_ev - funding_cost
                    if net_ev > 0:
                        return YieldOpportunity(
                            kind="polyclaw_position",
                            strategy_id=vault_state.strategy_id,
                            lp=vault_state.lp,
                            token=vault_state.token,
                            drawdown_amount=round(amount, 2),
                            expected_value=round(net_ev, 4),
                            edge=trade.edge,
                            drawdown_calldata=self.vault.encode_draw_down(
                                vault_state, amount
                            ),
                            rationale=(
                                f"{trade.rationale} | Draw {amount:.2f} of "
                                f"{available:.2f} available | EV net of funding "
                                f"{net_ev:.4f}"
                            ),
                            market_id=trade.market_id,
                            trade=trade,
                        )

        # --- Path 2: lending loop (only when spread clears the bar) --------
        spread = lending_loop_apr - funding_cost_apr
        if spread > self.min_edge_after_cost:
            amount = available * self.max_draw_utilization
            if amount > 0:
                net_ev = amount * spread
                return YieldOpportunity(
                    kind="lending_loop",
                    strategy_id=vault_state.strategy_id,
                    lp=vault_state.lp,
                    token=vault_state.token,
                    drawdown_amount=round(amount, 2),
                    expected_value=round(net_ev, 4),
                    edge=round(spread, 5),
                    drawdown_calldata=self.vault.encode_draw_down(vault_state, amount),
                    rationale=(
                        f"Lending loop APR {lending_loop_apr:.2%} vs funding "
                        f"{funding_cost_apr:.2%} | Spread {spread:.2%} | "
                        f"Draw {amount:.2f} of {available:.2f} available"
                    ),
                )

        return None

    def settle_up(
        self,
        vault_state: VaultState,
        principal: float,
        fee: float,
        was_correct: bool,
        observer: Optional[Any] = None,
    ) -> SettlementRecord:
        """
        settleUp callback: encode the settlement calldata, update internal
        calibration from the outcome, and feed the observer/improver loop.
        """
        if self.vault is None:
            raise RuntimeError("settle_up requires a VaultClient")

        record = SettlementRecord(
            strategy_id=vault_state.strategy_id,
            lp=vault_state.lp,
            token=vault_state.token,
            principal=principal,
            fee=fee,
            protocol_fee_bps=self.protocol_fee_bps,
            was_correct=was_correct,
            settle_calldata=self.vault.encode_settle_up(
                vault_state, principal, fee, self.protocol_fee_bps
            ),
        )
        self.settlements.append(record)

        # Self-improvement: calibration update from realized outcome
        self.update_from_outcome(was_correct)

        # Feed observer/improver with the settlement as a closed trade
        if observer is not None:
            pnl = fee if was_correct else -principal
            observer.observe_cycle(
                metrics={
                    "max_drawdown": 0.0,
                    "vault_strategy_id": vault_state.strategy_id,
                    "protocol_fee": fee * self.protocol_fee_bps / BPS_DENOMINATOR,
                },
                recent_trades=[{
                    "correct": was_correct,
                    "pnl": pnl,
                    "source": "vault_settle_up",
                }],
            )

        logger.info(
            "[CORE] settleUp sid=%s principal=%.2f fee=%.2f correct=%s ema=%.4f",
            vault_state.strategy_id, principal, fee, was_correct, self.win_rate_ema,
        )
        return record

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
            "settlements": len(self.settlements),
            "vault_connected": self.vault is not None,
        }
