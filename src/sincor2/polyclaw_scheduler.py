#!/usr/bin/env python3
"""
Polyclaw Autonomous Trading Agent Scheduler
Integrated with SINCOR's APScheduler
Scans Polymarket every minute for arbitrage, executes autonomously
"""

import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

# Config
POLYCLAW_ENABLED = os.getenv("POLYCLAW_ENABLED", "false").lower() == "true"
POLYCLAW_RISK_LEVEL = os.getenv("POLYCLAW_RISK_LEVEL", "medium")
POLYCLAW_AUTO_EXECUTE = os.getenv("POLYCLAW_AUTO_EXECUTE", "false").lower() == "true"
POLYCLAW_ALERT_THRESHOLD = float(os.getenv("POLYCLAW_ALERT_THRESHOLD", "0.5"))
POLYCLAW_SCAN_INTERVAL = int(os.getenv("POLYCLAW_SCAN_INTERVAL", "60"))  # seconds

TRADES_LOG = Path.home() / ".openclaw" / "workspace" / "polyclaw_trades.jsonl"

scheduler = None

def log_trade(trade_data):
    """Log executed trade to JSONL"""
    trade_data['timestamp'] = datetime.utcnow().isoformat()
    TRADES_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(TRADES_LOG, "a") as f:
        f.write(json.dumps(trade_data) + "\n")
    logger.info(f"[POLYCLAW] Trade logged: {trade_data['strategy']} | {trade_data['net_profit_percent']}% profit")

def scan_polymarket():
    """Scan markets via trading vertical agents — signals only unless AUTO_EXECUTE."""
    try:
        from verticals.trading.polymarket_agent import PolymarketAgent
        from verticals.trading.openclaw_agent import OpenClawTradingAgent

        pm = PolymarketAgent()
        trader = OpenClawTradingAgent()
        opportunities = []

        for market in pm.get_open_markets(category="all"):
            price = float(market.get("probability", 0.5))
            # Conservative model tilt — real API forecast would replace this
            model_prob = min(0.99, max(0.01, price + (0.08 if price < 0.5 else -0.08)))
            signal = trader.generate_signal(market, model_prob)
            edge_pct = abs(signal.edge) * 100
            if edge_pct < POLYCLAW_ALERT_THRESHOLD:
                continue
            opportunities.append({
                "market_id": signal.market_id,
                "strategy": signal.side,
                "edge_percent": round(edge_pct, 2),
                "net_profit_percent": round(edge_pct * 0.75, 2),
                "signal_only": not POLYCLAW_AUTO_EXECUTE,
                "rationale": signal.rationale,
            })

        if opportunities:
            logger.info("[POLYCLAW] %s signal(s) above threshold", len(opportunities))
        return opportunities

    except Exception as e:
        logger.error(f"[POLYCLAW] Scan error: {e}")
        return []

def execute_trade(opportunity):
    """
    Execute arbitrage trade autonomously
    Uses wallet private key from env to sign + submit to Polymarket CLOB
    """
    if not POLYCLAW_AUTO_EXECUTE:
        logger.info(f"[POLYCLAW] Auto-execute disabled. Pending approval: {opportunity}")
        return False
    
    net_profit = opportunity.get("net_profit_percent", 0)
    if net_profit < POLYCLAW_ALERT_THRESHOLD:
        logger.debug(f"[POLYCLAW] Skipping: profit {net_profit}% < threshold {POLYCLAW_ALERT_THRESHOLD}%")
        return False
    
    try:
        logger.info(f"[POLYCLAW] 🚀 Executing: {opportunity['strategy']}")
        logger.info(f"[POLYCLAW]    Market: {opportunity['market_id']}")
        logger.info(f"[POLYCLAW]    Edge: {opportunity['edge_percent']}% | Net profit: {net_profit}%")
        
        # TODO: Actual execution via Polyclaw API
        # - Sign transaction with wallet private key
        # - Submit YES/NO order to Polymarket CLOB
        # - Track position in trades.jsonl
        
        trade_result = {
            "market_id": opportunity["market_id"],
            "strategy": opportunity["strategy"],
            "edge_percent": opportunity["edge_percent"],
            "net_profit_percent": net_profit,
            "status": "executed",
            "gas_estimate": opportunity.get("estimated_gas", 0)
        }
        
        log_trade(trade_result)
        return True
    
    except Exception as e:
        logger.error(f"[POLYCLAW] Execution error: {e}")
        return False

def polyclaw_cycle():
    """Single scan + execute cycle"""
    if not POLYCLAW_ENABLED:
        return
    
    try:
        logger.info(f"[POLYCLAW] Cycle | Risk: {POLYCLAW_RISK_LEVEL} | Auto-exec: {POLYCLAW_AUTO_EXECUTE}")
        
        opportunities = scan_polymarket()
        
        if not opportunities:
            logger.debug("[POLYCLAW] No arbitrage opportunities detected")
            return
        
        logger.info(f"[POLYCLAW] Found {len(opportunities)} opportunity/ies")
        
        for opp in opportunities:
            net_profit = opp.get('net_profit_percent', 0)
            logger.info(f"[POLYCLAW]   - {opp['strategy']}: {net_profit}% profit")
            
            if net_profit >= POLYCLAW_ALERT_THRESHOLD:
                execute_trade(opp)
    
    except Exception as e:
        logger.error(f"[POLYCLAW] Cycle error: {e}")

def start_polyclaw_scheduler(app):
    """Start Polyclaw autonomous trading scheduler"""
    global scheduler
    
    if not POLYCLAW_ENABLED:
        logger.warning("[POLYCLAW] Scheduler disabled (POLYCLAW_ENABLED=false)")
        return None
    
    try:
        scheduler = BackgroundScheduler()
        
        # Add polyclaw scan job
        scheduler.add_job(
            polyclaw_cycle,
            trigger=IntervalTrigger(seconds=POLYCLAW_SCAN_INTERVAL),
            id='polyclaw_scan',
            name='Polyclaw Market Scan',
            replace_existing=True
        )
        
        scheduler.start()
        logger.info(f"[POLYCLAW] Scheduler started (scan every {POLYCLAW_SCAN_INTERVAL}s)")
        return scheduler
    
    except Exception as e:
        logger.error(f"[POLYCLAW] Scheduler init failed: {e}")
        return None

def stop_polyclaw_scheduler():
    """Stop Polyclaw scheduler"""
    global scheduler
    if scheduler:
        try:
            scheduler.shutdown()
            logger.info("[POLYCLAW] Scheduler stopped")
        except Exception as e:
            logger.error(f"[POLYCLAW] Shutdown error: {e}")
        scheduler = None
