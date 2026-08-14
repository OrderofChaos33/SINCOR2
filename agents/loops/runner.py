#!/usr/bin/env python3
"""
SINCOR Autonomous Profit Loop Runner
====================================
Calls only existing live contracts. Strict safety bounds. Fail closed.

Usage:
  python -m agents.loops.runner --loop all --dry-run
  python -m agents.loops.runner --loop fluid_yield,ladder_mm --live
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("sincor.loops")

CONFIG_PATH = Path(__file__).with_name("config.yaml")


@dataclass
class SafetyState:
    daily_notional_usdc: float = 0.0
    last_reset_day: str = ""
    paused: bool = False
    pause_reason: str = ""


class LoopRunner:
    def __init__(self, config: dict[str, Any], live: bool = False):
        self.cfg = config
        self.live = live
        self.safety = SafetyState()
        self._validate_config()

    def _validate_config(self) -> None:
        s = self.cfg["safety"]
        assert s["max_single_tx_usdc"] <= s["max_daily_notional_usdc"]
        assert s["min_health_factor"] >= 1.1
        assert s["reserve_usdc"] >= 0
        if not self.live:
            log.info("DRY-RUN mode — no transactions will be sent")

    def _check_daily_cap(self, amount_usdc: float) -> bool:
        from datetime import date

        today = str(date.today())
        if self.safety.last_reset_day != today:
            self.safety.daily_notional_usdc = 0.0
            self.safety.last_reset_day = today
        if self.safety.daily_notional_usdc + amount_usdc > self.cfg["safety"]["max_daily_notional_usdc"]:
            log.warning("Daily notional cap would be exceeded; skipping")
            return False
        return True

    def _record_notional(self, amount_usdc: float) -> None:
        self.safety.daily_notional_usdc += amount_usdc

    def kill(self, reason: str) -> None:
        self.safety.paused = True
        self.safety.pause_reason = reason
        log.error("KILL SWITCH: %s", reason)

    # ------------------------------------------------------------------
    # Loop implementations (stubs that call real surfaces when live)
    # ------------------------------------------------------------------

    def loop_fluid_yield(self) -> None:
        """Deposit excess USDC into SincFluidAdapter; harvest & recycle."""
        if not self.cfg["loops"]["fluid_yield"]["enabled"]:
            return
        if self.safety.paused:
            return

        min_dep = self.cfg["loops"]["fluid_yield"]["min_deposit_usdc"]
        max_tx = self.cfg["safety"]["max_single_tx_usdc"]
        amount = min(min_dep, max_tx)

        if not self._check_daily_cap(amount):
            return

        log.info("[fluid_yield] would deposit %.2f USDC into Fluid adapter (dry=%s)", amount, not self.live)
        if self.live:
            # Real call would use web3/viem against SincFluidAdapter.depositUSDC
            # and respect reserve_usdc. Left as integration point so we never
            # accidentally spend without explicit wallet wiring.
            log.warning("Live Fluid deposit not yet wired in this runner; implement via existing sdk/fluid-amplify.js path")
        else:
            self._record_notional(amount)  # simulate for dry-run accounting

    def loop_vault_fee_compound(self) -> None:
        """Harvest SharedLiquidityVault fees and drip into ladder / compound."""
        if not self.cfg["loops"]["vault_fee_compound"]["enabled"]:
            return
        if self.safety.paused:
            return

        thresh = self.cfg["loops"]["vault_fee_compound"]["min_fee_threshold_usdc"]
        log.info("[vault_fee_compound] check threshold %.2f USDC (dry=%s)", thresh, not self.live)
        # Real path: read vault accrued, call existing settle/harvest, then
        # route % to ladder via SincLimitOrderHook and % back to vault.

    def loop_ladder_mm(self) -> None:
        """Maintain two-sided limit-order ladder + react to Flashblocks."""
        if not self.cfg["loops"]["ladder_mm"]["enabled"]:
            return
        if self.safety.paused:
            return

        target = self.cfg["loops"]["ladder_mm"]["target_price_usd"]
        log.info(
            "[ladder_mm] maintain ladder around $%.2f | ticks sell=%d buy=%d (dry=%s)",
            target,
            self.cfg["loops"]["ladder_mm"]["sell_ladder_ticks"],
            self.cfg["loops"]["ladder_mm"]["buy_ladder_ticks"],
            not self.live,
        )
        # Real path:
        # 1. Poll preconf / Flashblocks for large pending
        # 2. Adjust / place orders via SincLimitOrderHook
        # 3. Rebalance inventory via SincSwapRouter if skew > max_inventory_sinc_pct

    def run_once(self, loops: list[str]) -> None:
        for name in loops:
            if name == "fluid_yield":
                self.loop_fluid_yield()
            elif name == "vault_fee_compound":
                self.loop_vault_fee_compound()
            elif name == "ladder_mm":
                self.loop_ladder_mm()
            elif name == "all":
                self.loop_fluid_yield()
                self.loop_vault_fee_compound()
                self.loop_ladder_mm()
            else:
                log.warning("Unknown loop: %s", name)

    def run_forever(self, loops: list[str]) -> None:
        intervals = [
            self.cfg["loops"].get(n, {}).get("interval_sec", 60)
            for n in (loops if "all" not in loops else ["fluid_yield", "vault_fee_compound", "ladder_mm"])
        ]
        sleep_for = min(intervals) if intervals else 60
        log.info("Starting autonomous loop runner | loops=%s | interval~%ss | live=%s", loops, sleep_for, self.live)
        while True:
            try:
                self.run_once(loops)
            except Exception as e:
                log.exception("Loop iteration failed: %s", e)
                self.kill(f"unhandled exception: {e}")
            if self.safety.paused:
                log.error("Paused: %s — exiting", self.safety.pause_reason)
                break
            time.sleep(sleep_for)


def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    # Env overrides
    if os.getenv("FLUID_ADAPTER"):
        cfg["addresses"]["fluid_adapter"] = os.getenv("FLUID_ADAPTER")
    if os.getenv("PRECONF_RPC"):
        cfg["network"]["preconf_rpc"] = os.getenv("PRECONF_RPC")
    if os.getenv("TARGET_PRICE_USD"):
        cfg["loops"]["ladder_mm"]["target_price_usd"] = float(os.getenv("TARGET_PRICE_USD"))
    return cfg


def main() -> int:
    parser = argparse.ArgumentParser(description="SINCOR autonomous profit loops")
    parser.add_argument("--loop", default="all", help="Comma-separated: fluid_yield,vault_fee_compound,ladder_mm,all")
    parser.add_argument("--live", action="store_true", help="Actually send txs (default is dry-run)")
    parser.add_argument("--once", action="store_true", help="Run one iteration then exit")
    args = parser.parse_args()

    cfg = load_config()
    live = args.live and not cfg["safety"].get("dry_run_default", True)
    if args.live and cfg["safety"].get("dry_run_default", True):
        log.warning("config still has dry_run_default=true; forcing dry-run. Set dry_run_default: false to go live.")
        live = False

    runner = LoopRunner(cfg, live=live)
    loops = [x.strip() for x in args.loop.split(",") if x.strip()]

    if args.once:
        runner.run_once(loops)
    else:
        runner.run_forever(loops)
    return 0


if __name__ == "__main__":
    sys.exit(main())
