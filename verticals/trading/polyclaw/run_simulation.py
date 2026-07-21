#!/usr/bin/env python3
"""
Runnable example: Polyclaw Autonomous Trading System in Simulation

Run this to test the full loop:
CoreAgent -> Rebalancer -> Observer -> Simulation

This is the starting point for proving profitability in sim.

Fix (2026-07-21): the observer was previously fed raw sim.state.trades, which
carry no "correct" flag — every unresolved trade read as a loss, pinning
recent_win_rate at 0.0 and falsely tripping the poor_calibration regime every
cycle. The observer now receives resolved outcomes only, and the rebalancer
receives realized PnL instead of a hardcoded 0.
"""

import logging
from verticals.trading.polyclaw.simulation_engine import SimulationEngine
from verticals.trading.polyclaw.core_agent import PolyclawCoreAgent
from verticals.trading.polyclaw.autonomous_rebalancer import AutonomousRebalancer
from verticals.trading.polyclaw.observer_improver import ObserverImproverAgent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("polyclaw_sim")


def main():
    logger.info("=== Polyclaw Autonomous Trading Simulation ===")

    # Initialize system
    sim = SimulationEngine(initial_bankroll=25000.0)
    core = PolyclawCoreAgent(name="polyclaw-core")
    rebalancer = AutonomousRebalancer(total_bankroll=25000.0)
    observer = ObserverImproverAgent()

    # Synthetic test markets (replace with real historical data later)
    test_markets = [
        {"id": "m1", "probability": 0.62, "true_probability": 0.71, "liquidity": 250000},
        {"id": "m2", "probability": 0.41, "true_probability": 0.38, "liquidity": 180000},
        {"id": "m3", "probability": 0.55, "true_probability": 0.62, "liquidity": 320000},
        {"id": "m4", "probability": 0.73, "true_probability": 0.58, "liquidity": 95000},
    ]

    # Run a few autonomous cycles
    for cycle in range(1, 6):
        logger.info(f"\n--- Cycle {cycle} ---")

        decisions = []
        resolved_outcomes = []  # only trades with a known result
        for market in test_markets:
            # Core agent decides
            decision = core.evaluate_market(market, model_probability=market["true_probability"])
            if decision:
                decisions.append(decision)
                bankroll_before = sim.state.bankroll
                fill = sim.submit_order(market, decision.side, decision.size * 25000)

                # Simulate outcome
                correct = (decision.side == "buy_yes" and market["true_probability"] > 0.5) or \
                          (decision.side == "buy_no" and market["true_probability"] < 0.5)
                sim.resolve_market(fill.market_id, market["true_probability"] > 0.5)
                realized_pnl = sim.state.bankroll - bankroll_before

                # Update core agent
                core.update_from_outcome(correct)

                # Update rebalancer with realized PnL (was hardcoded 0)
                rebalancer.update_performance("core", pnl=realized_pnl, was_win=correct, edge=decision.edge)

                resolved_outcomes.append({
                    "correct": correct,
                    "pnl": realized_pnl,
                    "market_id": decision.market_id,
                })

        # Rebalance
        allocation = rebalancer.rebalance()

        # Observer watches resolved outcomes only — open positions are not losses
        metrics = sim.get_metrics()
        observer.observe_cycle(metrics, resolved_outcomes)
        suggestions = observer.suggest_improvements()

        logger.info(f"Metrics: {metrics}")
        logger.info(f"Observer health: {observer.get_health_report()}")
        if suggestions:
            logger.info(f"Suggestions: {suggestions}")

    logger.info("\n=== Simulation Complete ===")
    logger.info(f"Final bankroll: {sim.state.bankroll:.2f}")
    logger.info(f"Max drawdown: {sim.state.max_drawdown:.1%}")


if __name__ == "__main__":
    main()
