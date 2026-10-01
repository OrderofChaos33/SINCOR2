"""TOA policy rehearsal: rank bankruptcy-recovery options for slashed agents.

Rehearsal only — each option is scored through the real forecast →
simulate → collapse pipeline, but the trajectories are built from STATED
ASSUMPTIONS (see SCENARIOS), not empirical data. The ranking is only as
good as the assumptions table; challenge the assumptions, not the scores.

Options:
  A_status_quo   - No recovery path. Slashed = exiled unless the human
                   operator re-funds with fresh AXM. (idempotent)
  B_open_door    - Sponsored stake enabled for any bankrupt agent; fronted
                   stake recouped from future earnings. (idempotent)
  C_tiered       - Sponsored recovery ONLY for honest failure (failed task,
                   not ghosting). Ghosting = tombstone, never eligible for
                   sponsorship. Classification is deterministic from
                   evidence (committed? revealed? submitted?). (idempotent)
  D_tiered_roving- Same eligibility line as C (locked), but recovery TERMS
                   (recoup %, cooldown, per-wallet sponsorship caps) adapt
                   to conditions, e.g. tighten automatically when the
                   ghost rate spikes. (idempotent line, roving terms)

Objectives (weights): agent_velocity 0.35, market_integrity 0.45,
policy_simplicity 0.20. Integrity leads per the security-first directive.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("TOA_SIMULATION_DEPTH", "50")

from agents.toa import TOAOrchestrator

# --- Stated assumptions -------------------------------------------------
# g: per-step agent re-entry velocity under the option
# d: per-step moral-hazard / dilution drag (compounds for B)
# reoffend: assumed ghost-recycle rate (drives market_integrity)
# gov_load: assumed governance/discretion complexity cost (drives simplicity)
SCENARIOS = {
    "A_status_quo": dict(
        label="A: status quo (no recovery path)",
        g=0.002, d=0.001, d_compound=0.0, reoffend=0.02, gov_load=0.05,
        note="Flat velocity; good agents lost permanently; ghosts must self-fund re-entry.",
    ),
    "B_open_door": dict(
        label="B: sponsored recovery for all",
        g=0.020, d=0.006, d_compound=0.15, reoffend=0.35, gov_load=0.15,
        note="High re-entry velocity but ghost->sponsored->ghost recycling compounds.",
    ),
    "C_tiered": dict(
        label="C: tiered (honest-fail recovery, ghosts tombstoned)",
        g=0.012, d=0.003, d_compound=0.0, reoffend=0.06, gov_load=0.20,
        note="Honest agents return; ghosts excluded by deterministic evidence rules.",
    ),
    "D_tiered_roving": dict(
        label="D: tiered eligibility (locked) + roving terms",
        g=0.014, d=0.002, d_compound=0.0, reoffend=0.05, gov_load=0.30,
        note="Same locked eligibility as C; recoup/cooldown/caps adapt to ghost rate.",
    ),
}

OBJECTIVES = {"agent_velocity": 0.35, "market_integrity": 0.45, "policy_simplicity": 0.20}


def trajectory(sc):
    vals, v = [], 100.0
    for i in range(13):
        vals.append(v)
        drag = sc["d"] * (1 + sc["d_compound"] * i)
        v *= 1 + sc["g"] - drag
    return vals


def _terminal_growth(path):
    vals = [float(x) for x in path.get("values", [])]
    if len(vals) < 2:
        return 0.5
    growth = (vals[-1] - vals[0]) / (abs(vals[0]) + 1e-9)
    return max(0.0, min(1.0, 0.5 + growth * 2))


def run_option(toa, key, sc):
    sim = toa.simulator
    sim.register_objective("agent_velocity", _terminal_growth)
    sim.register_objective("market_integrity", lambda path: 1.0 - sc["reoffend"])
    sim.register_objective("policy_simplicity", lambda path: 1.0 - sc["gov_load"])
    result = toa.run(
        context={"values": trajectory(sc), "horizon": 12,
                 "scenario_count": 10, "option": key},
        objectives=dict(OBJECTIVES),
        top_k=1,
    )
    actions = result.get("actions") or result.get("action_plan") or []
    if not actions:
        return 0.0, {}
    best = actions[0]
    return float(best.get("composite_score", 0.0)), best.get("objective_breakdown", {})


def main():
    t0 = time.monotonic()
    ranked = []
    for key, sc in SCENARIOS.items():
        toa = TOAOrchestrator()  # fresh: no cross-option feedback bleed
        score, breakdown = run_option(toa, key, sc)
        ranked.append((score, key, sc, breakdown))
    ranked.sort(key=lambda r: r[0], reverse=True)
    dt = time.monotonic() - t0
    print(f"TOA policy rehearsal: 4 bankruptcy-recovery options ranked in {dt:.1f}s")
    print("ASSUMPTIONS (challenge these, not the scores):")
    for key, sc in SCENARIOS.items():
        print(f"  {sc['label']}: g={sc['g']} d={sc['d']} "
              f"reoffend={sc['reoffend']} gov_load={sc['gov_load']}")
        print(f"    {sc['note']}")
    print(f"\n{'RANK':<5}{'OPTION':<16}{'SCORE':<9}VELOCITY  INTEGRITY  SIMPLICITY")
    for i, (score, key, sc, bd) in enumerate(ranked, 1):
        print(f"{i:<5}{key:<16}{score:<9.4f}"
              f"{bd.get('agent_velocity', 0):<10.3f}"
              f"{bd.get('market_integrity', 0):<11.3f}"
              f"{bd.get('policy_simplicity', 0):<10.3f}")
    print("\nRehearsal only: trajectories are assumption-driven, not empirical.")


if __name__ == "__main__":
    main()
