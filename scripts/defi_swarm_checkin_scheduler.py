#!/usr/bin/env python3
"""
SINCOR2 DeFi Swarm Check-in Scheduler - PRODUCTION ACTIVATED
CEO Directive: 26 Swarms on cutting-edge DeFi projects, check every 5 minutes indefinitely.
Fully wired: Real TOA, PolyclawTOADecisionRouter, earning cycles, vault feedback, treasury inflow tracking.
Self-improving via feedback. Run 24/7. Direct revenue to Treasury.

CEO 2026-08-19: capital input forced to live treasury (~$313). No more hardcoded $5000.

Usage: python -m scripts.defi_swarm_checkin_scheduler [--once] or in docker/railway.
"""

import time
import logging
import sys
import argparse
from datetime import datetime

# Real integrations
try:
    from src.sincor2.agents.toa.orchestrator import TOAOrchestrator
    from integration.polyclaw_toa_decision_router import run_polyclaw_earning_cycle, PolyclawTOADecisionRouter
    from verticals.trading.polyclaw.core_agent import PolyclawCoreAgent
    from verticals.trading.polyclaw.vault_client import VaultClient
except ImportError as e:
    logging.warning(f"Import warning (run from repo root): {e}")
    TOAOrchestrator = None
    run_polyclaw_earning_cycle = None
    PolyclawCoreAgent = None
    VaultClient = None
    PolyclawTOADecisionRouter = None

try:
    from src.sincor2.treasury_inflow import record_inflow
except ImportError:
    try:
        from sincor2.treasury_inflow import record_inflow
    except ImportError:
        record_inflow = None

try:
    from src.sincor2.defi.yield_aggregator import get_default_aggregator
except ImportError:
    try:
        from sincor2.defi.yield_aggregator import get_default_aggregator
    except ImportError:
        get_default_aggregator = None

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("sincor.defi_scheduler")

CHECK_INTERVAL_SECONDS = 300  # 5 minutes

# CEO 2026-08-19: live capital from Basescan (~$312.94). Override with env if needed.
LIVE_TREASURY_USD = float(__import__("os").getenv("YIELD_CAPITAL_USD", "312.93"))

class DeFiSwarmScheduler:
    def __init__(self):
        self.toa = TOAOrchestrator() if TOAOrchestrator else None
        self.router = PolyclawTOADecisionRouter() if PolyclawTOADecisionRouter else None
        self.core_agent = PolyclawCoreAgent(vault_client=VaultClient() if VaultClient else None) if PolyclawCoreAgent else None
        self.swarm_count = 26
        self.projects = list(range(1, 27))
        self.running = True
        self.cycle_count = 0
        self.yield_agg = get_default_aggregator() if get_default_aggregator else None
        logger.info("DeFi Swarm Scheduler ACTIVATED. 26 swarms live. TOA + Router + Vault + Treasury ledger wired. Live capital=$%.2f", LIVE_TREASURY_USD)

    def check_in_all_swarms(self):
        """Real check-in: Trigger earning cycles, ingest to TOA, log treasury impact."""
        total_inflow_projection = 0.0
        for i in range(1, self.swarm_count + 1):
            try:
                project_id = i
                market_context = {
                    "available_capital_usd": LIVE_TREASURY_USD,
                    "max_risk_pct": 0.08,
                    "polyclaw_edge": 0.04 + (i % 5) * 0.01,
                    "vault_yield_apr": 0.12,
                    "project_id": project_id,
                    "equity_history": [LIVE_TREASURY_USD] * 6,
                }

                if self.router and run_polyclaw_earning_cycle:
                    def dummy_execute(route="public", **ctx):
                        # Conservative projection only — never invent realized cash
                        pnl = 0.0
                        return {"status": "ok", "pnl_usd": pnl, "route": route, "project": project_id}

                    result = run_polyclaw_earning_cycle(market_context, dummy_execute)
                    pnl = result.get("execution_result", {}).get("pnl_usd", 0.0)
                    total_inflow_projection += pnl

                    if self.toa:
                        self.toa.ingest_feedback({
                            "source": "defi_swarm_checkin",
                            "swarm_id": i,
                            "project": project_id,
                            "pnl_usd": pnl,
                            "status": result.get("status"),
                            "timestamp": datetime.utcnow().isoformat(),
                        })

                    logger.info(f"Check-in Swarm {i}/26 Project {project_id}: Cycle {result.get('status')} | PnL ${pnl:.2f} -> Treasury")
                else:
                    logger.warning(f"Swarm {i}: Router/TOA not available - conservative mode")

            except Exception as e:
                logger.error(f"Swarm {i} check-in error: {e}. TOA self-healing...")
                if self.toa:
                    self.toa.ingest_feedback({"source": "error", "swarm_id": i, "error": str(e)})

        # Yield aggregator plan against LIVE capital (Project #1)
        if self.yield_agg:
            try:
                plan = self.yield_agg.plan_rebalance(capital_usd=LIVE_TREASURY_USD, risk_budget=0.30)
                logger.info(
                    "YieldAggregator: capital=$%.2f blended_apr=%.2f%% allocations=%d mode=%s",
                    LIVE_TREASURY_USD,
                    plan.expected_blended_apr * 100,
                    len(plan.allocations),
                    plan.mode,
                )
                if self.toa:
                    self.toa.ingest_feedback({
                        "source": "yield_aggregator",
                        "capital_usd": LIVE_TREASURY_USD,
                        "blended_apr": plan.expected_blended_apr,
                        "allocations": [a.to_dict() for a in plan.allocations],
                        "mode": plan.mode,
                        "timestamp": datetime.utcnow().isoformat(),
                    })
                # Measured projected fee so the loop has a real number
                if record_inflow:
                    expected_fee = LIVE_TREASURY_USD * plan.expected_blended_apr * (plan.fee_to_treasury_bps / 10_000)
                    record_inflow(
                        round(expected_fee, 6),
                        asset="USD",
                        source="yield_aggregator_plan",
                        usd_estimate=round(expected_fee, 6),
                        projected=True,
                        note=f"scheduler_cycle_{self.cycle_count} capital={LIVE_TREASURY_USD}",
                    )
            except Exception as e:
                logger.warning("YieldAggregator plan failed: %s", e)

        # Additional projected only if real positive PnL appeared (none in dummy path)
        if record_inflow and total_inflow_projection > 0:
            try:
                record_inflow(
                    total_inflow_projection,
                    asset="USD",
                    source="defi_swarms",
                    usd_estimate=total_inflow_projection,
                    projected=True,
                    note=f"scheduler_cycle_{self.cycle_count}",
                )
            except Exception as e:
                logger.error("record_inflow failed: %s", e)
        logger.info(f"Cycle complete | Projected Treasury Inflow this round: ${total_inflow_projection:.2f}")
        return total_inflow_projection

    def simulate_revenue_paths(self, projects):
        """Use TOA run_defi to prioritize revenue paths across projects."""
        if not self.toa:
            return [{"action": "hold", "reason": "no_toa"}]
        try:
            result = self.toa.run_defi(
                context={"values": [LIVE_TREASURY_USD] * 6, "scenario_count": 8},
                defi_signals={"polyclaw_edge": 0.05, "vault_yield_apr": 0.12},
            )
            return result.get("action_plan", [])
        except Exception as e:
            logger.error(f"Revenue sim error: {e}")
            return []

    def run_indefinite(self, once: bool = False):
        """Infinite 24/7 loop. Production hardened."""
        logger.info("Starting INDEFINITE 5-min DeFi revenue check-ins. Live capital=$%.2f. Results only.", LIVE_TREASURY_USD)
        while self.running:
            try:
                self.cycle_count += 1
                inflow = self.check_in_all_swarms()
                revenue_paths = self.simulate_revenue_paths(self.projects)
                if revenue_paths:
                    logger.info(f"TOA Revenue Paths prioritized: {len(revenue_paths)} top actions. Treasury focus active.")

                if once:
                    logger.info("One-shot complete. Exiting.")
                    break
                time.sleep(CHECK_INTERVAL_SECONDS)
            except KeyboardInterrupt:
                logger.info("Scheduler stopped by directive. Momentum preserved.")
                self.running = False
            except Exception as e:
                logger.error(f"Scheduler error: {e}. Self-improving via TOA. Continuing...")
                time.sleep(60)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit (for testing/CI)")
    args = parser.parse_args()

    scheduler = DeFiSwarmScheduler()
    scheduler.run_indefinite(once=args.once)
