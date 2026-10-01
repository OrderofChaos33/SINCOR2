"""TOA money-move rehearsal: rank the 26 DeFi protocols for live-launch priority.

Rehearsal only — runs the real forecast → simulate → collapse pipeline per
protocol using catalog data (target_apr, risk_score, fee_bps), times the full
run, and prints the ranked money moves. When builds reach `product` stage,
re-run with live test evidence instead of catalog projections.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("TOA_SIMULATION_DEPTH", "50")

from agents.toa import TOAOrchestrator

# catalog.py loaded by path: the sincor2.defi package __init__ on main
# imports .engine, which was never committed there (fixed on the
# xioix/defi-product-arm branch). Load the module file directly.
import importlib.util
_cat_path = os.path.join(os.path.dirname(__file__), "src", "sincor2", "defi", "catalog.py")
_spec = importlib.util.spec_from_file_location("defi_catalog", _cat_path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["defi_catalog"] = _mod
_spec.loader.exec_module(_mod)
PROTOCOLS = _mod.PROTOCOLS


def score_protocol(toa, spec):
    # Synthetic value series shaped by the protocol's target APR; risk enters
    # as a drag on the defi signal so the collapse penalizes risk honestly.
    apr = spec.target_apr
    risk_drag = spec.risk_score * 0.5
    net_edge = max(apr - risk_drag, 0.0)
    values = [100.0 * (1 + net_edge) ** (i / 12) for i in range(13)]
    result = toa.run_defi(
        context={"values": values, "horizon": 12, "scenario_count": 10},
        defi_signals={"polyclaw_edge": 0.0, "vault_yield_apr": net_edge},
        top_k=1,
    )
    actions = result.get("actions") or result.get("action_plan") or []
    if actions:
        best = actions[0]
        return float(best.get("composite_score", 0.0)), result
    return 0.0, result


def main():
    toa = TOAOrchestrator()
    t0 = time.monotonic()
    ranked = []
    for spec in PROTOCOLS:
        score, _ = score_protocol(toa, spec)
        ranked.append((score, spec))
    dt = time.monotonic() - t0
    ranked.sort(key=lambda r: r[0], reverse=True)
    print(f"TOA rehearsal: 26 protocols ranked in {dt:.1f}s")
    print(f"{'RANK':<5}{'PROTO':<7}{'SCORE':<9}{'APR':<8}{'RISK':<7}{'FEE':<6}{'BLOCKED':<8}NAME")
    for i, (score, s) in enumerate(ranked, 1):
        print(f"{i:<5}{s.protocol_id:<7}{score:<9.4f}{s.target_apr:<8.3f}"
              f"{s.risk_score:<7.2f}{s.fee_bps:<6d}{str(s.live_blocked):<8}{s.name}")
    # Persist for the record
    import json
    out = os.path.join(os.path.dirname(__file__), "data",
                       "toa_money_move_rehearsal.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({
        "ts": time.time(),
        "elapsed_s": round(dt, 2),
        "mode": "rehearsal-catalog-projections",
        "ranking": [{"rank": i, "protocol_id": s.protocol_id, "name": s.name,
                     "score": round(sc, 4), "target_apr": s.target_apr,
                     "risk_score": s.risk_score, "fee_bps": s.fee_bps,
                     "live_blocked": s.live_blocked}
                    for i, (sc, s) in enumerate(ranked, 1)],
    }, open(out, "w"), indent=2)
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
