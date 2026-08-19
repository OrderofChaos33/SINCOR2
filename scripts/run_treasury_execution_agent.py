#!/usr/bin/env python3
"""
Entry point for the Treasury Execution Agent (E-treasury-exec-47).

Capable of running yield allocations while the operator is away.

Default (safe):
  python scripts/run_treasury_execution_agent.py
  → reads live capital, builds plan, queues intents, records projected fee.

Live (when you are ready / have secure key in env):
  export EXECUTE_LIVE=1
  export ONCHAIN_EXECUTOR_PRIVATE_KEY=0x...
  python scripts/run_treasury_execution_agent.py

Loop while away:
  python scripts/run_treasury_execution_agent.py --loop --interval 900

Kill switch:
  touch data/TREASURY_EXEC_HALT
  # or
  python scripts/run_treasury_execution_agent.py --trip-kill "manual halt"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("treasury_exec_runner")


def main() -> int:
    parser = argparse.ArgumentParser(description="SINCOR Treasury Execution Agent")
    parser.add_argument("--once", action="store_true", default=True, help="Run one cycle (default)")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=900, help="Seconds between cycles in loop mode")
    parser.add_argument("--status", action="store_true", help="Print agent status and exit")
    parser.add_argument("--trip-kill", type=str, metavar="REASON", help="Trip the kill switch")
    parser.add_argument("--clear-kill", action="store_true", help="Clear the kill switch")
    parser.add_argument("--capital", type=float, default=None, help="Override capital USD for this run")
    args = parser.parse_args()

    from src.sincor2.agents.treasury_execution_agent import (
        TreasuryExecutionAgent,
        trip_kill_switch,
        clear_kill_switch,
    )

    agent = TreasuryExecutionAgent()

    if args.trip_kill:
        trip_kill_switch(args.trip_kill)
        print(json.dumps({"ok": True, "action": "kill_switch_tripped", "reason": args.trip_kill}, indent=2))
        return 0

    if args.clear_kill:
        clear_kill_switch()
        print(json.dumps({"ok": True, "action": "kill_switch_cleared"}, indent=2))
        return 0

    if args.status:
        print(json.dumps(agent.status(), indent=2))
        return 0

    def one_cycle() -> None:
        result = agent.run_cycle(force_capital=args.capital)
        print(json.dumps(result.to_dict(), indent=2))
        logger.info(
            "cycle complete mode=%s capital=$%.2f actionable=%d queued=%d",
            result.mode,
            result.capital_usd,
            len(result.allocations),
            result.intents_queued,
        )

    if args.loop:
        logger.info("Treasury Execution Agent entering loop (interval=%ds). Ctrl-C to stop.", args.interval)
        while True:
            try:
                one_cycle()
                time.sleep(args.interval)
            except KeyboardInterrupt:
                logger.info("stopped by operator")
                break
            except Exception as exc:
                logger.exception("cycle error: %s — continuing", exc)
                time.sleep(60)
    else:
        one_cycle()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
