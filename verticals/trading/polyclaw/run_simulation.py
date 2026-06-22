#!/usr/bin/env python3
"""
Runnable example: Polyclaw Autonomous Trading System in Simulation

Run this to test the full loop:
CoreAgent -> Rebalancer -> Observer -> Simulation

This is the starting point for proving profitability in sim.
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
        for market in test_markets:
            # Core agent decides
            decision = core.evaluate_market(market, model_probability=market["true_probability"])
            if decision:
                decisions.append(decision)
                fill = sim.submit_order(market, decision.side, decision.size * 25000)

                # Simulate outcome
                correct = (decision.side == "buy_yes" and market["true_probability"] > 0.5) or \
                          (decision.side == "buy_no" and market["true_probability"] < 0.5)
                sim.resolve_market(fill.market_id, market["true_probability"] > 0.5)

                # Update core agent
                core.update_from_outcome(correct)

                # Update rebalancer
                rebalancer.update_performance("core", pnl=0, was_win=correct, edge=decision.edge)

        # Rebalance
        allocation = rebalancer.rebalance()

        # Observer watches
        metrics = sim.get_metrics()
        observer.observe_cycle(metrics, sim.state.trades[-len(decisions):] if decisions else [])
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
