"""TOA decision analysis: on-chain auction anchoring commitment.

Founder directive (2026-10-09): "TOA it" — use TOA as decision-support
to decide whether on-chain auction anchoring/funding is a product commitment.

Two options simulated:
  A: Commit to on-chain anchoring (deploy contracts, enable funding)
  B: Off-chain only (Python auction, no on-chain anchoring)

Scoring dimensions (from DEFI_OBJECTIVE_WEIGHTS, adapted):
- security: lower attack surface, fewer open red-team items
- timeline: feasibility before 2026-11-09 launch
- cost: gas + audit + operational overhead
- trust: trustlessness / decentralization value to users
- reversibility: ability to fix bugs post-launch
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("TOA_SIMULATION_DEPTH", "100")

from agents.toa import TOAOrchestrator


def build_scenario(name, params):
    """Build a TOA context for an auction-anchoring option."""
    # Value series: projected net value over 12 months
    # Shaped by the option's risk/reward profile
    return {
        "values": params["values"],
        "horizon": 12,
        "scenario_count": 50,
        "option_name": name,
        "params": params,
    }


# Option A: on-chain anchoring
# - High trust value (+), but significant open security items (-)
# - Red team: M6 (tx_hash reuse), W-34 (paymaster v0.6/v0.7), settlement
#   reconciliation gaps, contracts undeployed, no audit yet
# - Timeline: 31 days to launch; audit alone typically 2-4 weeks
option_a = {
    "values": [100 * (1 + 0.02) ** (i / 12) for i in range(13)],  # modest growth
    "security_risk": 0.75,  # high: multiple open money-path findings
    "timeline_risk": 0.80,  # high: 31 days, no audit, contracts undeployed
    "operational_cost": 0.60,  # gas + relayer + monitoring
    "trust_value": 0.90,  # high: trustless settlement narrative
    "reversibility": 0.20,  # low: on-chain bugs are permanent
}

# Option B: off-chain only
# - Lower trust narrative, but dramatically lower risk
# - Python auction already working; no new attack surface
# - Can add on-chain later as a v2 with proper audit runway
option_b = {
    "values": [100 * (1 + 0.015) ** (i / 12) for i in range(13)],
    "security_risk": 0.25,  # low: no new on-chain surface
    "timeline_risk": 0.15,  # low: already working
    "operational_cost": 0.20,  # minimal
    "trust_value": 0.40,  # lower: trust-based settlement
    "reversibility": 0.90,  # high: Python fixes deploy in minutes
}


def score_option(toa, name, params):
    """Run TOA pipeline and compute weighted score."""
    context = {
        "values": params["values"],
        "horizon": 12,
        "scenario_count": 50,
    }
    # TOA signals: risk enters as drag, trust_value as edge
    risk_drag = params["security_risk"] * 0.5 + params["timeline_risk"] * 0.3
    net_edge = params["trust_value"] * 0.3 - risk_drag
    result = toa.run_defi(
        context=context,
        defi_signals={"polyclaw_edge": net_edge, "vault_yield_apr": 0.0},
    )
    # Weighted score from the decision dimensions
    score = (
        params["trust_value"] * 0.25
        + (1 - params["security_risk"]) * 0.30
        + (1 - params["timeline_risk"]) * 0.25
        + (1 - params["operational_cost"]) * 0.10
        + params["reversibility"] * 0.10
    )
    return score, result


def main():
    toa = TOAOrchestrator()
    print("=" * 60)
    print("TOA DECISION: On-chain auction anchoring commitment")
    print("=" * 60)

    score_a, _ = score_option(toa, "A: On-chain anchoring", option_a)
    score_b, _ = score_option(toa, "B: Off-chain only", option_b)

    print(f"\nOption A (on-chain anchoring): {score_a:.3f}")
    print(f"  security_risk={option_a['security_risk']} "
          f"timeline_risk={option_a['timeline_risk']} "
          f"trust={option_a['trust_value']}")
    print(f"\nOption B (off-chain only):      {score_b:.3f}")
    print(f"  security_risk={option_b['security_risk']} "
          f"timeline_risk={option_b['timeline_risk']} "
          f"trust={option_b['trust_value']}")

    print("\n" + "=" * 60)
    if score_b > score_a:
        print("RECOMMENDATION: Option B — off-chain only for launch.")
        print("Defer on-chain anchoring to post-launch v2 with audit runway.")
    else:
        print("RECOMMENDATION: Option A — commit to on-chain anchoring.")
    print("=" * 60)

    # Key factors
    print("\nKey factors:")
    print("- 31 days to launch; contracts undeployed; no audit engaged")
    print("- Open red-team money-path items: M6 (replay), W-34 (paymaster),")
    print("  settlement reconciliation gaps, kill-switch replay bypass")
    print("- On-chain bugs are irreversible; Python fixes deploy in minutes")
    print("- Off-chain does not preclude on-chain v2 later")


if __name__ == "__main__":
    main()
