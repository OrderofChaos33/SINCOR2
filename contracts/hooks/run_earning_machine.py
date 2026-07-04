#!/usr/bin/env python3
"""
Polyclaw Earning Machine - Production Runner
Respects your existing environment variables.
"""

import os
import logging
from datetime import datetime, timezone

# ====================== CONFIG FROM YOUR VARS ======================
LIVE_MODE = os.getenv("LIVE_MODE", "true").lower() == "true"
POLYCLAW_AUTO_EXECUTE = os.getenv("POLYCLAW_AUTO_EXECUTE", "true").lower() == "true"
SAFETY_OVERRIDE = os.getenv("SAFETY_OVERRIDE", "true").lower() == "true"
COMPLIANCE_LOG = os.getenv("COMPLIANCE_LOG_TO_STDOUT", "true").lower() == "false"

# Capital (default to small test amount)
CAPITAL_USD = float(os.getenv("POLYCLAW_CAPITAL", 5))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("earning_machine")

def utc_now():
    return datetime.now(timezone.utc)

# ====================== TOA DECISION ======================
def get_toa_decision(context: dict) -> dict:
    available = context.get("available_capital_usd", CAPITAL_USD)
    size = min(available * 0.2, 8)   # Conservative

    decision = {
        "action": "trade",
        "size_usd": round(size, 2),
        "route": "renegade",
        "confidence": 0.80,
        "risk_level": "low" if SAFETY_OVERRIDE else "medium",
        "timestamp": utc_now().isoformat()
    }
    logger.info(f"TOA decision → {decision['route']} | ${decision['size_usd']}")
    return decision

# ====================== EXECUTION ======================
def execute_trade(decision: dict) -> dict:
    if not LIVE_MODE and not POLYCLAW_AUTO_EXECUTE:
        logger.info("LIVE_MODE=False → Simulation only")
        return {
            "status": "simulated",
            "route": decision["route"],
            "pnl_usd": round(decision["size_usd"] * 0.012, 2),
            "timestamp": utc_now().isoformat()
        }

    try:
        from verticals.trading.polyclaw.core_agent import PolyclawCoreAgent
        agent = PolyclawCoreAgent()
        result = agent.execute(
            route=decision["route"],
            size_usd=decision["size_usd"]
        )
        logger.info(f"REAL execution via {decision['route']}")
        return result
    except Exception as e:
        logger.error(f"Execution failed: {e}")
        return {"status": "error", "reason": str(e)}

def trigger_self_funding(pnl: float):
    if abs(pnl) > 0.8:
        logger.info(f"PnL ${pnl} → Self-funding wheels triggered")
    else:
        logger.info("Small PnL — no trigger this cycle")

# ====================== MAIN ======================
def run_earning_cycle():
    logger.info("=== EARNING MACHINE CYCLE START ===")
    context = {"available_capital_usd": CAPITAL_USD}
    decision = get_toa_decision(context)
    result = execute_trade(decision)
    trigger_self_funding(result.get("pnl_usd", 0))
    logger.info("=== CYCLE COMPLETE ===\n")
    return result

if __name__ == "__main__":
    run_earning_cycle()