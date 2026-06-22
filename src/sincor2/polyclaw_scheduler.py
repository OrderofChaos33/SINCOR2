#!/usr/bin/env python3
"""
Polyclaw Autonomous Trading Scheduler (Activated)

Now uses the new autonomous system by default:
- PolyclawCoreAgent (smart edge + adaptive sizing)
- AutonomousRebalancer (performance-driven profit splitting)
- ObserverImproverAgent (continuous observation + improvement)
- SimulationEngine (high-fidelity sim, default mode)

Simulation mode is ON by default until profitability is proven.
No manual dials. Fully autonomous loop.
"""

import logging
import os
from datetime import datetime
from pathlib import Path

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    APSCHEDULER_AVAILABLE = True
except ImportError:
    BackgroundScheduler = None
    IntervalTrigger = None
    APSCHEDULER_AVAILABLE = False

# Import the new autonomous system
try:
    from verticals.trading.polyclaw.simulation_engine import SimulationEngine
    from verticals.trading.polyclaw.core_agent import PolyclawCoreAgent
    from verticals.trading.polyclaw.autonomous_rebalancer import AutonomousRebalancer
    from verticals.trading.polyclaw.observer_improver import ObserverImproverAgent
    AUTONOMOUS_SYSTEM_AVAILABLE = True
except ImportError:
    AUTONOMOUS_SYSTEM_AVAILABLE = False

logger = logging.getLogger(__name__)

# ==================== CONFIG ====================
POLYCLAW_ENABLED = os.getenv("POLYCLAW_ENABLED", "true").lower() == "true"
POLYCLAW_MODE = os.getenv("POLYCLAW_MODE", "simulation").lower()  # simulation | paper | live
POLYCLAW_SCAN_INTERVAL = int(os.getenv("POLYCLAW_SCAN_INTERVAL", "60"))

TRADES_LOG = Path.home() / ".polyclaw" / "trades.jsonl"

scheduler = None
sim_engine = None
core_agent = None
rebalancer = None
observer = None


def initialize_autonomous_system():
    """Initialize the full autonomous trading system."""
    global sim_engine, core_agent, rebalancer, observer

    if not AUTONOMOUS_SYSTEM_AVAILABLE:
        logger.warning("[POLYCLAW] Autonomous system modules not found. Using legacy mode.")
        return False

    sim_engine = SimulationEngine(initial_bankroll=25000.0)
    core_agent = PolyclawCoreAgent(name="polyclaw-core")
    rebalancer = AutonomousRebalancer(total_bankroll=25000.0)
    observer = ObserverImproverAgent()

    logger.info("[POLYCLAW] Autonomous system initialized (simulation mode)")
    return True


def autonomous_cycle():
    """One full autonomous trading cycle using the new system."""
    if not AUTONOMOUS_SYSTEM_AVAILABLE or sim_engine is None:
        logger.warning("[POLYCLAW] Autonomous system not initialized")
        return

    logger.info(f"[POLYCLAW] Autonomous cycle | Mode: {POLYCLAW_MODE}")

    # Example markets (replace with real data feed later)
    markets = [
        {"id": "m1", "probability": 0.61, "true_probability": 0.68, "liquidity": 280000},
        {"id": "m2", "probability": 0.44, "true_probability": 0.39, "liquidity": 150000},
        {"id": "m3", "probability": 0.57, "true_probability": 0.64, "liquidity": 410000},
    ]

    decisions_made = []

    for market in markets:
        decision = core_agent.evaluate_market(market, model_probability=market.get("true_probability", 0.5))
        if decision:
            decisions_made.append(decision)
            # Execute in simulation
            fill = sim_engine.submit_order(market, decision.side, decision.size * 25000)

            # Simulate outcome resolution
            outcome_yes = market.get("true_probability", 0.5) > 0.5
            sim_engine.resolve_market(fill.market_id, outcome_yes)

            # Self-improvement
            correct = (decision.side == "buy_yes" and outcome_yes) or (decision.side == "buy_no" and not outcome_yes)
            core_agent.update_from_outcome(correct)
            rebalancer.update_performance("core", pnl=0, was_win=correct, edge=decision.edge)

    # Autonomous rebalancing
    allocation = rebalancer.rebalance()

    # Observer watches and suggests improvements
    metrics = sim_engine.get_metrics()
    observer.observe_cycle(metrics, sim_engine.state.trades[-len(decisions_made):] if decisions_made else [])
    suggestions = observer.suggest_improvements()

    logger.info(f"[POLYCLAW] Metrics: {metrics}")
    logger.info(f"[POLYCLAW] Health: {observer.get_health_report()}")
    if suggestions:
        logger.info(f"[POLYCLAW] Suggestions from Observer: {suggestions}")


def legacy_scan_polymarket():
    """Legacy placeholder (kept for compatibility)."""
    logger.debug("[POLYCLAW] Legacy scan called (no-op in autonomous mode)")
    return []


def polyclaw_cycle():
    """Main cycle - routes to autonomous system when available."""
    if not POLYCLAW_ENABLED:
        return

    if AUTONOMOUS_SYSTEM_AVAILABLE and POLYCLAW_MODE in ["simulation", "paper"]:
        autonomous_cycle()
    else:
        # Fallback to old behavior
        opportunities = legacy_scan_polymarket()
        for opp in opportunities:
            logger.info(f"[POLYCLAW] Legacy opportunity: {opp}")


def start_polyclaw_scheduler(app=None):
    """Start the Polyclaw autonomous scheduler. Simulation mode by default."""
    global scheduler

    if not POLYCLAW_ENABLED:
        logger.warning("[POLYCLAW] Scheduler disabled (POLYCLAW_ENABLED=false)")
        return None

    # Initialize autonomous system
    initialize_autonomous_system()

    if not APSCHEDULER_AVAILABLE:
        logger.warning("[POLYCLAW] APScheduler not available. Running single cycle only.")
        polyclaw_cycle()
        return None

    try:
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            polyclaw_cycle,
            trigger=IntervalTrigger(seconds=POLYCLAW_SCAN_INTERVAL),
            id="polyclaw_autonomous_cycle",
            name="Polyclaw Autonomous Cycle",
            replace_existing=True,
        )
        scheduler.start()
        logger.info(f"[POLYCLAW] Autonomous scheduler started | Mode: {POLYCLAW_MODE} | Interval: {POLYCLAW_SCAN_INTERVAL}s")
        return scheduler
    except Exception as e:
        logger.error(f"[POLYCLAW] Scheduler start failed: {e}")
        return None


def stop_polyclaw_scheduler():
    global scheduler
    if scheduler:
        try:
            scheduler.shutdown()
            logger.info("[POLYCLAW] Scheduler stopped")
        except Exception as e:
            logger.error(f"[POLYCLAW] Shutdown error: {e}")
        scheduler = None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting Polyclaw in standalone mode...")
    start_polyclaw_scheduler()
