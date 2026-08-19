#!/usr/bin/env python3
"""
CEO 2026-08-19: Emit live Yield Aggregator intents against current treasury capital.

Safety:
- Default is still dry-run.
- To emit live intents: export EXECUTE_LIVE=1
- Signing and broadcast remain EXTERNAL. This script never holds keys and never sends txs.

Usage:
  python scripts/emit_yield_live_intents.py
  EXECUTE_LIVE=1 python scripts/emit_yield_live_intents.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Ensure repo root on path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sincor2.defi.yield_aggregator import get_default_aggregator
from src.sincor2.treasury_inflow import record_inflow

# Live capital confirmed Basescan 19 Aug 2026
LIVE_CAPITAL_USD = float(os.getenv("YIELD_CAPITAL_USD", "312.93"))
RISK_BUDGET = float(os.getenv("YIELD_RISK_BUDGET", "0.30"))


def main() -> int:
    agg = get_default_aggregator()
    plan = agg.plan_rebalance(capital_usd=LIVE_CAPITAL_USD, risk_budget=RISK_BUDGET)

    print("=== SINCOR Yield Aggregator — Live Capital Plan ===")
    print(f"capital_usd     : {plan.total_capital_usd}")
    print(f"mode            : {plan.mode}")
    print(f"blended_apr     : {plan.expected_blended_apr * 100:.4f}%")
    print(f"max_risk_score  : {plan.max_risk_score}")
    print(f"fee_to_treasury : {plan.fee_to_treasury_bps} bps")
    print(f"treasury        : {plan.treasury}")
    print(f"vault           : {plan.vault}")
    print()
    print("Allocations:")
    for a in plan.allocations:
        print(f"  {a.strategy_id:20s} ${a.capital_usd:10.2f}  weight={a.weight:.4f}  apr={a.estimated_apr*100:.2f}%")

    # Record measured projection every run so TOA / CEO KPI has a number
    expected_fee = LIVE_CAPITAL_USD * plan.expected_blended_apr * (plan.fee_to_treasury_bps / 10_000)
    record_inflow(
        round(expected_fee, 6),
        asset="USD",
        source="yield_aggregator_plan",
        usd_estimate=round(expected_fee, 6),
        projected=True,
        note=f"emit_yield_live_intents capital={LIVE_CAPITAL_USD} mode={plan.mode}",
    )
    print(f"\nProjected annual fee recorded to ledger: ${expected_fee:.6f}")

    if plan.mode == "live_intent" and plan.intents:
        print("\n=== LIVE INTENTS (sign + broadcast externally) ===")
        print(json.dumps(plan.intents, indent=2))
        print("\nNext step: use your signer / agent wallet to execute the allocate actions.")
        print("On any fee-bearing success tx, call treasury_settlement.record_platform_fee_inflow(..., projected=False, tx_hash=...)")
    else:
        print("\nDRY_RUN only. To emit live intents:")
        print("  export EXECUTE_LIVE=1")
        print("  python scripts/emit_yield_live_intents.py")
        print("Signing still required outside this process.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
