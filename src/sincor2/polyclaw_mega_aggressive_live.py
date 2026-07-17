#!/usr/bin/env python3
"""Polyclaw Live Runner — the consolidated, REAL trading loop.

This file replaces the old "mega aggressive" module, whose ``AUTO_EXECUTE``
path rolled ``random.random()`` and booked imaginary profit. That code is
gone. Everything here flows through the real stack:

    forecasting_engine.scan_opportunities()   real Polymarket data → model probs
    bankroll.can_open()                       equity-proportional risk gates
    execution_adapter.place_market_buy()      CLOB FOK orders (dry-run default)
    bankroll.record_trade()                   durable SQLite ledger
    shadow_portfolio                          silent 25% TOA-blend A/B twin

Live mode requires ALL of:
  POLYCLAW_LIVE=true
  POLYMARKET_PRIVATE_KEY=0x...        (Polygon EOA, funded with USDC + MATIC)
  pip install py-clob-client

Without them the runner still does the full scan/sizing loop but every order
is a clearly-logged dry run. There is no silent "looks live" mode anymore.

Sizing policy — ALWAYS GROWING, no fixed dollar ceiling:
  wager = half-Kelly × confidence, clamped to
          [POLYCLAW_MIN_WAGER_USD, POLYCLAW_MAX_POSITION_PCT × equity]
  $20 equity   → ~$1–$3 wagers
  $3,000 equity → ~$30–$300 wagers (same discipline, bigger numbers)

A/B experiment: every cycle the shadow portfolio paper-trades the SAME
markets at a 0.75 book / 0.25 TOA blend. Resolved markets score both
portfolios with real settlement math. Run `compare_performance()` any time
for the live-vs-shadow scoreboard; promotion decisions come from that data.

Run one cycle:    python -m sincor2.polyclaw_mega_aggressive_live
Run forever:      POLYCLAW_LOOP=1 python -m sincor2.polyclaw_mega_aggressive_live
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sincor2.bankroll import get_bankroll
from sincor2.execution_adapter import PolymarketAdapter, kill_switch_tripped
from sincor2.forecasting_engine import (
    Forecast,
    resolve_predictions,
    scan_opportunities,
)
from sincor2 import shadow_portfolio

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("sincor.polyclaw.live")

TREASURY_ADDRESS = os.getenv(
    "TREASURY_ADDRESS", "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
)

MIN_EDGE = float(os.getenv("POLYCLAW_MIN_EDGE", "0.04"))          # 4% min |edge|
MIN_CONFIDENCE = float(os.getenv("POLYCLAW_MIN_CONFIDENCE", "0.4"))
KELLY_FRACTION = float(os.getenv("POLYCLAW_KELLY_FRACTION", "0.5"))  # half-Kelly
# Wager floor = Polymarket's ≈$1 exchange minimum.
MIN_WAGER_USD = float(os.getenv("POLYCLAW_MIN_WAGER_USD", "1.0"))
MAX_TRADES_PER_CYCLE = int(os.getenv("POLYCLAW_MAX_TRADES_PER_CYCLE", "3"))
CYCLE_INTERVAL_SEC = int(os.getenv("POLYCLAW_CYCLE_INTERVAL_SEC", "90"))  # 90s loop


def _kelly_size(fc: Forecast, side: str, equity: float, cap: float) -> float:
    """Half-Kelly position size in USD, confidence-scaled, capped at ``cap``.

    The cap is equity-proportional (bankroll.max_position_size()), so the
    ceiling rises automatically as the bankroll compounds.
    """
    p = fc.model_probability if side == "BUY_YES" else 1.0 - fc.model_probability
    price = fc.market_price if side == "BUY_YES" else 1.0 - fc.market_price
    if price <= 0.01 or price >= 0.99:
        return 0.0
    b = (1.0 / price) - 1.0
    kelly = (p * (b + 1.0) - 1.0) / b
    if kelly <= 0:
        return 0.0
    # Scale Kelly down by data-quality confidence; cap at equity-proportional limit.
    size = equity * kelly * KELLY_FRACTION * fc.confidence
    return round(min(size, cap), 2)


def _pick_side(fc: Forecast) -> tuple[str, str]:
    """Return (side_label, token_id) for the larger-edge direction."""
    if fc.edge > 0:
        return "BUY_YES", fc.token_id_yes
    return "BUY_NO", (fc.token_id_no or "")


def run_cycle(adapter: Optional[PolymarketAdapter] = None) -> Dict[str, Any]:
    """One full scan → size → execute → record → shadow-score cycle."""
    bankroll = get_bankroll()
    adapter = adapter or PolymarketAdapter(bankroll)

    if kill_switch_tripped():
        logger.warning("kill switch tripped — cycle skipped")
        return {"status": "halted", "reason": "kill_switch"}

    snapshot = bankroll.snapshot()
    logger.info(
        "cycle start | equity=$%.2f exposure=$%.2f available=$%.2f "
        "max_pos=$%.2f today=$%.2f%s",
        snapshot["equity"], snapshot["exposure"], snapshot["available"],
        snapshot["max_position"], snapshot["realized_today"],
        "" if adapter.is_live() else " [DRY RUN]",
    )

    # Score resolved forecasts first — calibration data compounds.
    try:
        resolve_predictions()
    except Exception as exc:
        logger.debug("resolve_predictions failed: %s", exc)

    # Resolve closed markets for BOTH portfolios (real settlement math).
    try:
        resolved = shadow_portfolio.resolve_all(bankroll)
        if resolved["live"] or resolved["shadow"]:
            logger.info("resolved: %d live, %d shadow trades",
                        resolved["live"], resolved["shadow"])
    except Exception as exc:
        logger.debug("shadow resolve_all failed: %s", exc)

    opportunities = scan_opportunities(min_edge=MIN_EDGE)
    trades_taken: List[Dict[str, Any]] = []

    # Shadow portfolio silently paper-trades the same scan at 25% TOA blend.
    try:
        shadow_opened = shadow_portfolio.record_shadow_cycle(opportunities)
        if shadow_opened:
            logger.info("[SHADOW] opened %d paper trades", shadow_opened)
    except Exception as exc:
        logger.debug("shadow record failed: %s", exc)

    for fc in opportunities[:MAX_TRADES_PER_CYCLE]:
        if fc.confidence < MIN_CONFIDENCE:
            continue
        side_label, token_id = _pick_side(fc)
        if not token_id:
            continue
        size = _kelly_size(fc, side_label, snapshot["equity"],
                           bankroll.max_position_size())
        if size < MIN_WAGER_USD:
            logger.info("skip '%s' — Kelly size $%.2f below $%.2f wager floor",
                        fc.question[:60], size, MIN_WAGER_USD)
            continue

        result = adapter.place_market_buy(token_id, size)
        if not result.success:
            logger.warning("order failed for %s: %s", fc.question[:60], result.error)
            continue

        trade_id = bankroll.record_trade(
            token_id=token_id, side=side_label, size_usd=size,
            price=fc.market_price, order_id=result.order_id,
            simulated=result.simulated, market_id=fc.market_id,
        )
        trades_taken.append({
            "trade_id": trade_id,
            "question": fc.question,
            "side": side_label,
            "size_usd": size,
            "edge": fc.edge,
            "model_prob": fc.model_probability,
            "market_price": fc.market_price,
            "simulated": result.simulated,
            "order_id": result.order_id,
        })
        logger.info(
            "%s %s $%.2f on '%s' | edge=%.1f%% model=%.2f mkt=%.2f",
            "[DRY]" if result.simulated else "[LIVE]",
            side_label, size, fc.question[:60],
            fc.edge * 100, fc.model_probability, fc.market_price,
        )

    final = bankroll.snapshot()
    return {
        "status": "ok",
        "live": adapter.is_live(),
        "opportunities": len(opportunities),
        "trades_taken": trades_taken,
        "bankroll": final,
        "ab_test": shadow_portfolio.compare_performance(),
        "treasury": TREASURY_ADDRESS,
    }


def main() -> None:
    adapter = PolymarketAdapter()
    if not adapter.is_live():
        logger.warning(
            "DRY-RUN MODE — no real orders will be sent. To go live: "
            "POLYCLAW_LIVE=true + POLYMARKET_PRIVATE_KEY in env, "
            "and `pip install py-clob-client`."
        )
    if os.getenv("POLYCLAW_LOOP", "0") != "1":
        result = run_cycle(adapter)
        print("\n=== CYCLE RESULT ===")
        for k, v in result.items():
            print(f"{k}: {v}")
        return

    logger.info("starting loop, interval=%ds", CYCLE_INTERVAL_SEC)
    while True:
        try:
            run_cycle(adapter)
        except Exception:
            logger.exception("cycle crashed — continuing after interval")
        time.sleep(CYCLE_INTERVAL_SEC)


if __name__ == "__main__":
    main()
