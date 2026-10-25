#!/usr/bin/env python3
"""
Polyclaw Self-Perpetuating Earning Machine - Production Scheduler

Highest caliber, fully wired, super optimized.

This is the single source of truth for autonomous scheduled execution.

Integrates:
- TOA (full stack: orchestrator, forecaster, simulator, collapser, feedback)
- Polyclaw core execution
- Renegade dark pool routing (zero impact, private) via production router
- Circuit breaker protection on all external calls
- Automatic self-funding wheel triggers (AccountingHub + Rehypo + IntentHookV2 + Renegade)
- Simulation gate + risk pruning
- Full observability and self-improvement loops

Run this on schedule (every 2-4 hours during active markets).

Usage:
    python -m src.sincor2.polyclaw_earning_scheduler

Or import and call run_scheduled_cycle() from your existing daily_ops_scheduler.

Production best practices: defensive coding, structured logging, metrics, graceful degradation.
"""

import os
import logging
from datetime import datetime

try:
    from sincor2.production_logger import get_logger
    logger = get_logger(__name__)
except ImportError:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

# Production imports — graceful degradation so a missing integration never kills startup
try:
    from integration.polyclaw_toa_decision_router import run_polyclaw_earning_cycle
except (Exception, SystemExit) as exc:
    logger.warning(
        "integration.polyclaw_toa_decision_router unavailable; "
        "scheduled earning cycle disabled: %s",
        exc,
    )
    run_polyclaw_earning_cycle = None

try:
    from verticals.trading.polyclaw.core_agent import PolyclawCoreAgent
except (Exception, SystemExit) as exc:
    logger.error("PolyclawCoreAgent not importable. Check path: %s", exc)
    PolyclawCoreAgent = None


#: Fraction of available capital the decision router may size into a single
#: trade before the hard notional cap is applied.
_DEFAULT_MAX_RISK_PCT = 0.08

#: Hard per-trade notional ceiling in USD, overridable via
#: ``POLYCLAW_MAX_TRADE_USD``. Deliberately conservative: it sits above the
#: current live bankroll scale (the Bankroll ledger starts at
#: ``POLYCLAW_CAPITAL_USD``, $50 by default) so it never binds legitimate
#: small operations, while hard-blocking outsized orders if a capital
#: source is ever misreported. No single scheduled trade may exceed it.
_DEFAULT_MAX_TRADE_USD = 100.0


def _hub_available_capital():
    """Capital reported by AccountingHub, or None when unavailable.

    AccountingHub is an onchain (Solidity) component in this stack — there
    is no Python ``AccountingHub.get_available_capital()`` binding in the
    repo today. This is the single seam ``resolve_available_capital_usd``
    consults first: when a binding lands, wire it here and its value wins
    over the env fallback. Returning None means "not configured" — never
    an invented number.
    """
    try:
        from sincor2.accounting_hub import AccountingHub
    except Exception:
        return None
    try:
        return float(AccountingHub().get_available_capital())
    except Exception as exc:
        logger.error("AccountingHub capital read failed: %s", exc)
        return None


def resolve_available_capital_usd() -> float:
    """Resolve the capital the earning cycle may size trades against.

    Sources, in order — the first that yields a value wins:

    1. AccountingHub, via the ``_hub_available_capital`` seam, when a
       binding is configured.
    2. Explicit operator configuration: the ``POLYCLAW_CAPITAL`` env var.

    When neither is available the cycle fails CLOSED at 0.0. The decision
    router sizes positions as ``capital * max_risk_pct * utility``, so 0.0
    capital means $0 trades. There is deliberately NO default bankroll
    figure: the previous hardcoded $25,000 default sized real orders
    against money that did not exist.
    """
    hub_value = _hub_available_capital()
    if hub_value is not None:
        return max(0.0, float(hub_value))

    raw = os.getenv("POLYCLAW_CAPITAL")
    if raw is not None and str(raw).strip() != "":
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            logger.error(
                "POLYCLAW_CAPITAL=%r is not a number; failing closed at $0", raw
            )
            return 0.0

    logger.warning(
        "No capital source available (AccountingHub unconfigured, "
        "POLYCLAW_CAPITAL unset); failing closed at $0"
    )
    return 0.0


def _max_trade_usd() -> float:
    """Hard per-trade notional cap from ``POLYCLAW_MAX_TRADE_USD``."""
    raw = os.getenv("POLYCLAW_MAX_TRADE_USD")
    if raw is not None and str(raw).strip() != "":
        try:
            value = float(raw)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
        logger.error(
            "POLYCLAW_MAX_TRADE_USD=%r is not a positive number; "
            "using the conservative default $%.2f",
            raw,
            _DEFAULT_MAX_TRADE_USD,
        )
    return _DEFAULT_MAX_TRADE_USD


def max_trade_size_usd(available_capital_usd: float, max_risk_pct: float,
                       hard_cap_usd: float = None) -> float:
    """Per-trade notional ceiling: min(risk-based size, hard cap).

    The risk-based size mirrors the decision router's sizing formula at
    full utility (``capital * max_risk_pct``); the hard cap binds on top
    of it regardless of how large the resolved capital is.
    """
    cap = _max_trade_usd() if hard_cap_usd is None else float(hard_cap_usd)
    risk_based = max(0.0, float(available_capital_usd)) * max(0.0, float(max_risk_pct))
    return round(min(risk_based, max(0.0, cap)), 2)


def get_market_context():
    """Build the live market context for one earning cycle.

    Capital is resolved from a real source (see
    ``resolve_available_capital_usd``) — never a hardcoded bankroll — and
    the per-trade notional is hard-capped: the context carries
    ``max_trade_usd`` = min(capital * max_risk_pct, POLYCLAW_MAX_TRADE_USD)
    plus an effective ``max_risk_pct`` lowered when needed, so the
    router's size formula (capital * max_risk_pct * utility, utility <= 1)
    can never produce a trade above the cap.
    """
    capital = resolve_available_capital_usd()
    hard_cap = _max_trade_usd()
    capped = max_trade_size_usd(capital, _DEFAULT_MAX_RISK_PCT, hard_cap)
    effective_risk_pct = _DEFAULT_MAX_RISK_PCT
    if capital > 0:
        effective_risk_pct = min(_DEFAULT_MAX_RISK_PCT, capped / capital)
    context = {
        "available_capital_usd": capital,
        "max_risk_pct": effective_risk_pct,
        "configured_max_risk_pct": _DEFAULT_MAX_RISK_PCT,
        "max_trade_usd": capped,
        "max_trade_hard_cap_usd": hard_cap,
        "market": "polymarket_perps",
        "timestamp": datetime.utcnow().isoformat(),
    }
    return context


def polyclaw_execute(route: str, **kwargs):
    """Wrapper around your core Polyclaw execution with resilience."""
    if PolyclawCoreAgent is None:
        logger.error("PolyclawCoreAgent missing - cannot execute")
        return {"status": "error", "reason": "core_agent_unavailable"}

    agent = PolyclawCoreAgent()
    try:
        result = agent.execute(route=route, **kwargs)
        return result
    except Exception as e:
        logger.exception(f"Polyclaw execution failed on route {route}: {e}")
        return {"status": "error", "reason": str(e)}


def run_scheduled_cycle():
    """
    One full optimized earning cycle.
    This is what you schedule.
    """
    logger.info("=== Polyclaw Earning Cycle Starting ===")

    if run_polyclaw_earning_cycle is None:
        logger.warning("Polyclaw earning cycle skipped: decision router unavailable")
        return {"status": "skipped", "reason": "decision_router_unavailable"}

    context = get_market_context()

    # The router handles TOA decision, Renegade routing, circuit breaker, simulation gate, and self-funding triggers
    result = run_polyclaw_earning_cycle(context, polyclaw_execute)

    logger.info(f"Cycle complete: {result.get('status')} | PnL={result.get('execution_result', {}).get('pnl_usd', 0)}")

    # Optional: push metrics to your monitoring dashboard here
    return result


if __name__ == "__main__":
    # Run once manually or via cron / Railway schedule
    run_scheduled_cycle()
