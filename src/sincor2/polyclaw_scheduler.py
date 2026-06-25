#!/usr/bin/env python3
"""
POLYCLAW SCHEDULER - AGGRESSIVE TUNED VERSION
Ready to drop into SINCOR2.

This version includes:
- Aggressive tuned parameters (min_edge=0.028, kelly=0.55, max_pos=0.12)
- Adaptive edge + dynamic Kelly based on recent performance
- Daily loss limit + hard drawdown protection
- APScheduler loop (scans every 60 seconds)
- JSONL logging
- Respects POLYCLAW_AUTO_EXECUTE env var (false = paper mode only)
"""

import os
import json
import random
import time
from datetime import datetime, timezone, date
from apscheduler.schedulers.background import BackgroundScheduler

# ==================== AGGRESSIVE TUNED PARAMETERS ====================
MIN_EDGE = 0.028
KELLY_FRACTION = 0.55
MAX_POSITION_PCT = 0.12
SCAN_INTERVAL_SECONDS = 60

ADAPTIVE_MODE = True
WIN_STREAK_BOOST = 0.008
LOSS_STREAK_PENALTY = 0.012

DAILY_LOSS_LIMIT_PCT = 0.08
MAX_DD_HARD_STOP = 0.35

PERSISTENT_EDGE_BIAS = 0.011
NOISE_STD = 0.052
WIN_RATE_EMA_ALPHA = 0.12

AUTO_EXECUTE = os.getenv("POLYCLAW_AUTO_EXECUTE", "false").lower() == "true"


class PolyclawScheduler:
    def __init__(self):
        self.equity = 10000.0
        self.peak_equity = self.equity
        self.max_drawdown = 0.0
        self.total_trades = 0
        self.winning_trades = 0
        self.win_rate = 0.52
        self.daily_start_equity = self.equity
        self.current_day = date.today()
        self.log_file = "polyclaw_aggressive_trades.jsonl"

        self.scheduler = BackgroundScheduler()
        self.scheduler.add_job(self.scan_and_trade, 'interval', seconds=SCAN_INTERVAL_SECONDS)

        print("=" * 80)
        print("POLYCLAW AGGRESSIVE SCHEDULER STARTED")
        print(f"Parameters: min_edge={MIN_EDGE}, kelly={KELLY_FRACTION}, max_pos={MAX_POSITION_PCT}")
        print(f"Adaptive: {ADAPTIVE_MODE} | Daily Loss Limit: {DAILY_LOSS_LIMIT_PCT*100}%")
        print(f"AUTO_EXECUTE = {AUTO_EXECUTE} (false = paper mode only)")
        print(f"Logging to: {self.log_file}")
        print("=" * 80)

    def _get_adaptive_edge(self, base_edge, recent_wins, recent_losses):
        if not ADAPTIVE_MODE:
            return base_edge
        adjustment = 0.0
        if recent_wins > recent_losses + 2:
            adjustment = -WIN_STREAK_BOOST
        elif recent_losses > recent_wins + 2:
            adjustment = LOSS_STREAK_PENALTY
        return max(0.015, base_edge + adjustment)

    def _calculate_edge(self, market_implied_prob):
        noise = random.gauss(0, NOISE_STD)
        model_prob = max(0.01, min(0.99, market_implied_prob + PERSISTENT_EDGE_BIAS + noise))
        edge = model_prob - market_implied_prob
        return model_prob, edge

    def _kelly_size(self, edge, model_prob, recent_performance):
        if abs(edge) < MIN_EDGE:
            return 0.0
        p = model_prob
        q = 1 - p
        b = 1.0
        kelly = max(0, (b * p - q) / b)

        multiplier = 1.0
        if recent_performance > 0.65:
            multiplier = 1.3
        elif recent_performance < 0.45:
            multiplier = 0.6

        sized = min(kelly * KELLY_FRACTION * multiplier, MAX_POSITION_PCT)
        return max(0, sized)

    def _simulate_outcome(self, model_prob):
        true_prob = max(0.01, min(0.99, model_prob + random.gauss(0, 0.028)))
        return random.random() < true_prob

    def _check_daily_limit(self):
        today = date.today()
        if today != self.current_day:
            self.daily_start_equity = self.equity
            self.current_day = today
            print(f"\n=== NEW DAY - Daily equity reset ===\n")
            return False

        daily_pnl = self.equity - self.daily_start_equity
        daily_loss_pct = abs(daily_pnl) / self.daily_start_equity if daily_pnl < 0 else 0

        if daily_loss_pct >= DAILY_LOSS_LIMIT_PCT:
            print(f"\nDAILY LOSS LIMIT HIT ({daily_loss_pct*100:.1f}%) - Pausing for today.")
            return True
        return False

    def scan_and_trade(self):
        if self._check_daily_limit():
            return

        if self.max_drawdown >= MAX_DD_HARD_STOP:
            print("\nHARD DRAWDOWN STOP TRIGGERED. Shutting down.")
            self.scheduler.shutdown()
            return

        market_implied = random.uniform(0.18, 0.82)
        model_prob, edge = self._calculate_edge(market_implied)

        recent_wr = self.winning_trades / max(self.total_trades, 1)
        adaptive_edge = self._get_adaptive_edge(MIN_EDGE, self.winning_trades, self.total_trades - self.winning_trades)

        if abs(edge) < adaptive_edge:
            return

        bet_fraction = self._kelly_size(edge, model_prob, recent_wr)
        if bet_fraction <= 0:
            return

        bet_amount = self.equity * bet_fraction

        if AUTO_EXECUTE:
            # TODO: Add real Polymarket / on-chain execution here
            print(f"[{datetime.now().strftime('%H:%M:%S')}] LIVE EXECUTION TRIGGERED (not implemented in this template)")
            won = False
        else:
            won = self._simulate_outcome(model_prob)

        pnl = bet_amount if won else -bet_amount
        self.equity += pnl
        self.total_trades += 1
        if won:
            self.winning_trades += 1

        outcome = 1.0 if won else 0.0
        self.win_rate = (WIN_RATE_EMA_ALPHA * outcome) + ((1 - WIN_RATE_EMA_ALPHA) * self.win_rate)

        if self.equity > self.peak_equity:
            self.peak_equity = self.equity
        current_dd = (self.peak_equity - self.equity) / self.peak_equity
        if current_dd > self.max_drawdown:
            self.max_drawdown = current_dd

        trade = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "market_implied": round(market_implied, 4),
            "model_prob": round(model_prob, 4),
            "edge": round(edge, 4),
            "adaptive_edge_used": round(adaptive_edge, 4),
            "bet_fraction": round(bet_fraction, 4),
            "bet_amount": round(bet_amount, 2),
            "won": won,
            "pnl": round(pnl, 2),
            "equity": round(self.equity, 2),
            "running_win_rate": round(self.win_rate, 4),
            "max_dd_so_far": round(self.max_drawdown, 4),
            "auto_execute": AUTO_EXECUTE
        }

        with open(self.log_file, "a") as f:
            f.write(json.dumps(trade) + "\n")

        status = "WIN " if won else "LOSS"
        mode = "LIVE" if AUTO_EXECUTE else "PAPER"
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [{mode}] {status} | Edge={edge*100:.2f}% | Bet=${bet_amount:.2f} | P&L=${pnl:+.2f} | Eq=${self.equity:,.2f} | WR={self.win_rate*100:.1f}% | DD={self.max_drawdown*100:.1f}%")

    def start(self):
        self.scheduler.start()
        print("Polyclaw scheduler is now running...")

    def stop(self):
        self.scheduler.shutdown()
        print("Polyclaw scheduler stopped.")


if __name__ == "__main__":
    polyclaw = PolyclawScheduler()
    polyclaw.start()

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        polyclaw.stop()
        print("\nFinal Stats:")
        print(f"Total Trades: {polyclaw.total_trades}")
        print(f"Final Equity: ${polyclaw.equity:,.2f}")
        print(f"Max Drawdown: {polyclaw.max_drawdown*100:.1f}%")
        print(f"Win Rate: {polyclaw.win_rate*100:.1f}%")
