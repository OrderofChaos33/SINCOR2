#!/usr/bin/env python3
"""TOA Trading Optimization Example Workflow.

Demonstrates the full Temporal Optimization Agent pipeline applied to a
trading scenario:

1. Seed TOA with recent asset price observations.
2. Run the forecast → simulate → collapse pipeline.
3. Ingest a simulated trade execution result as feedback.
4. Re-run TOA to show adaptive improvement from feedback.

Run this script from the repository root::

    PYTHONPATH=. python examples/workflows/toa_trading_optimization.py
"""

from __future__ import annotations

import sys
import os
import json
from datetime import datetime, timezone

# Ensure the repo root is on the path when running directly.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agents.toa import TOAOrchestrator, TOAConfig  # noqa: E402


def main() -> None:
    print("=" * 60)
    print("TOA Trading Optimization Workflow")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 1. Configure TOA for a trading scenario
    # ------------------------------------------------------------------
    config = TOAConfig(
        simulation_depth=20,        # 20 Monte Carlo paths (fast demo)
        collapse_threshold=0.02,    # Keep paths with >= 2 % probability
        top_k_paths=3,              # Return top 3 action plans
        forecast_horizon=5,         # 5-step ahead forecast
        objective_weights={
            "revenue": 0.45,
            "risk": 0.30,
            "timeline": 0.15,
            "compliance": 0.05,
            "governance": 0.05,
        },
    )
    toa = TOAOrchestrator(config=config)
    print(f"\n[Config] simulation_depth={config.simulation_depth}, "
          f"top_k={config.top_k_paths}, horizon={config.forecast_horizon}")

    # ------------------------------------------------------------------
    # 2. Prepare trading context (e.g. BTC price in USD, last 10 periods)
    # ------------------------------------------------------------------
    trading_context = {
        "asset": "BTC-USD",
        "values": [62_000, 62_400, 61_800, 63_100, 64_200,
                   63_800, 65_000, 64_500, 66_100, 67_200],
        "compliance_score": 0.95,   # Pre-computed by compliance vertical
        "governance_score": 0.80,   # From DAE governance signals
    }
    print(f"\n[Context] Asset: {trading_context['asset']}, "
          f"last_price={trading_context['values'][-1]:,.0f}")

    # ------------------------------------------------------------------
    # 3. First TOA run
    # ------------------------------------------------------------------
    print("\n--- Run 1: Initial forecast and collapse ---")
    result1 = toa.run(context=trading_context)
    _print_result(result1)

    # ------------------------------------------------------------------
    # 4. Simulate execution: trade executed, partial success
    # ------------------------------------------------------------------
    print("\n--- Ingesting feedback: trade execution result ---")
    toa.ingest_feedback({
        "source": "trading_vertical",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "success": True,
            "quality_rating": 4.0,
            "pnl_usd": 1_240,
            "asset": "BTC-USD",
            "action": "long",
        },
    })
    print("[Feedback] Trade executed: success=True, quality=4.0/5, PnL=$1,240")

    # ------------------------------------------------------------------
    # 5. Second TOA run — feedback loop improves forecast context
    # ------------------------------------------------------------------
    print("\n--- Run 2: Post-feedback run (adaptive improvement) ---")
    result2 = toa.run(context=trading_context)
    _print_result(result2)

    # ------------------------------------------------------------------
    # 6. Show orchestrator stats
    # ------------------------------------------------------------------
    stats = toa.get_stats()
    print(f"\n[Stats] Total runs: {stats['run_count']}, "
          f"feedback events: {stats['feedback_events']}")

    print("\n" + "=" * 60)
    print("Workflow complete. TOA is ready for production integration.")
    print("=" * 60)


def _print_result(result: dict) -> None:
    """Pretty-print a TOA run result."""
    print(f"  run_id     : {result['run_id']}")
    print(f"  elapsed_ms : {result['elapsed_ms']}")
    print(f"  forecast   : {result['forecast_paths']} paths generated")
    print(f"  evaluated  : {result['evaluated_paths']} paths scored")
    plan = result.get("action_plan", [])
    print(f"  collapsed  : {len(plan)} actions selected")
    for action in plan:
        print(
            f"    Rank {action['rank']}: "
            f"composite={action['composite_score']:.4f}, "
            f"utility={action['utility_score']:.3f}, "
            f"p={action['probability']:.4f}"
        )
        print(f"      {action['rationale']}")
    fb = result.get("feedback_summary", {})
    if fb.get("total_events", 0) > 0:
        print(f"  feedback   : {fb['total_events']} events, "
              f"signal={fb['feedback_signal']:.3f}, "
              f"success_rate={fb['success_rate']:.2%}")


if __name__ == "__main__":
    main()
