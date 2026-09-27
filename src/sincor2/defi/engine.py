"""Protocol OS runtime for the 26 DeFi swarms.

Rebuilt 2026-09-27: the module was referenced by ``sincor2.defi.__init__``,
``tests/test_defi_26_protocols.py`` and ``docs/DEFI_26_PROTOCOL_BUILD_STATUS.md``
but was never committed, which left the whole ``sincor2.defi`` package
unimportable. This rebuild implements exactly the documented runtime contract:

- Default mode is ``dry_run``. This module never broadcasts.
- ``executed`` is always ``False`` here. Signing stays outside.
- High-risk protocols gate when ``risk_score > risk_budget``.
- Fees are a **daily slice**, not invented annual cash.
- P10 flash loans are disabled in this module.
- P14 live path, if any, is the Polyclaw wallet — never the Base treasury EOA.
- P15 / P22 live-eligible venue is Morpho Gauntlet USDC only.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from .catalog import PROTOCOLS, TREASURY, ProtocolSpec
from .yield_aggregator import get_default_aggregator

SUBMISSION_DIR = Path("data/defi_swarm_submissions")


@dataclass
class ProtocolTick:
    swarm_id: int
    protocol_id: str
    mode: str = "dry_run"
    status: str = "completed"  # completed | gated
    executed: bool = False
    action: str = "plan"
    artifacts: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    expected_fee_usd: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SwarmSubmission:
    ticks: List[ProtocolTick]
    completed: int
    gated: int
    errors: int
    treasury: str = TREASURY
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "treasury": self.treasury,
            "completed": self.completed,
            "gated": self.gated,
            "errors": self.errors,
            "timestamp": self.timestamp,
            "ticks": [t.to_dict() for t in self.ticks],
        }


def _daily_fee_usd(capital_usd: float, fee_bps: int) -> float:
    """Fee as a daily slice of allocated capital. Never an invented yearly number."""
    return max(0.0, capital_usd) * fee_bps / 10_000 / 365.0


class DeFiProtocolOS:
    """Dry-run tick engine for the 26 swarm protocols. No chain, no broadcast."""

    def __init__(self, capital_usd: float, risk_budget: float = 0.30):
        self.capital_usd = float(capital_usd)
        self.risk_budget = float(risk_budget)

    # -- core -----------------------------------------------------------
    def _gated(self, spec: ProtocolSpec) -> ProtocolTick:
        return ProtocolTick(
            swarm_id=spec.swarm_id,
            protocol_id=spec.protocol_id,
            status="gated",
            action="gated:risk_budget",
            warnings=[
                f"risk_score {spec.risk_score:.2f} exceeds risk_budget {self.risk_budget:.2f}; "
                "tick held in dry-run, no intent emitted"
            ],
        )

    def tick_one(self, swarm_id: int) -> ProtocolTick:
        spec = next(p for p in PROTOCOLS if p.swarm_id == swarm_id)
        if spec.risk_score > self.risk_budget:
            return self._gated(spec)
        handler = getattr(self, f"_handle_{spec.swarm_id:02d}", None)
        if handler is None:
            return self._generic_tick(spec)
        try:
            return handler(spec)
        except Exception as exc:  # never let one swarm break the tick loop
            tick = self._generic_tick(spec)
            tick.status = "gated"
            tick.action = "gated:error"
            tick.warnings.append(f"handler error (dry-run held): {exc}")
            return tick

    def tick_all(self) -> List[ProtocolTick]:
        return [self.tick_one(p.swarm_id) for p in PROTOCOLS]

    def submit(self) -> SwarmSubmission:
        ticks = self.tick_all()
        completed = sum(1 for t in ticks if t.status == "completed")
        gated = sum(1 for t in ticks if t.status == "gated")
        sub = SwarmSubmission(ticks=ticks, completed=completed, gated=gated, errors=0)
        out_dir = Path(os.environ.get("DEFI_SUBMISSION_DIR", str(SUBMISSION_DIR)))
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "latest.json", "w", encoding="utf-8") as fh:
            json.dump(sub.to_dict(), fh, indent=2)
        return sub

    # -- handlers --------------------------------------------------------
    def _base_tick(self, spec: ProtocolSpec, action: str,
                   artifacts: Dict[str, Any] | None = None,
                   warnings: List[str] | None = None) -> ProtocolTick:
        return ProtocolTick(
            swarm_id=spec.swarm_id,
            protocol_id=spec.protocol_id,
            action=action,
            artifacts=artifacts or {},
            warnings=warnings or [],
            expected_fee_usd=_daily_fee_usd(self.capital_usd, spec.fee_bps),
        )

    def _generic_tick(self, spec: ProtocolSpec) -> ProtocolTick:
        return self._base_tick(
            spec,
            action="scan_plan",
            artifacts={
                "plan": f"dry-run scan for {spec.name}",
                "gates": list(spec.gates),
                "live_blocked": spec.live_blocked,
            },
            warnings=["live intents disabled; dry-run plan only"],
        )

    def _handle_01(self, spec: ProtocolSpec) -> ProtocolTick:
        plan = get_default_aggregator().plan_rebalance(
            capital_usd=self.capital_usd, risk_budget=self.risk_budget
        )
        return self._base_tick(
            spec,
            action="rebalance_plan",
            artifacts={
                "allocations": [a.to_dict() for a in plan.allocations],
                "expected_blended_apr": plan.expected_blended_apr,
                "max_risk_score": plan.max_risk_score,
            },
            warnings=list(plan.warnings),
        )

    def _handle_10(self, spec: ProtocolSpec) -> ProtocolTick:
        return self._base_tick(
            spec,
            action="scan_only",
            artifacts={"flash_loan": False, "opportunities": []},
            warnings=["flash loans disabled in this module; opportunity scan only"],
        )

    def _handle_14(self, spec: ProtocolSpec) -> ProtocolTick:
        tick = self._generic_tick(spec)
        tick.warnings.append("live path, if any, is the Polyclaw wallet — never the treasury EOA")
        return tick

    def _handle_15(self, spec: ProtocolSpec) -> ProtocolTick:
        tick = self._generic_tick(spec)
        tick.warnings.append("Morpho Gauntlet USDC is the only live-eligible venue")
        return tick

    def _handle_22(self, spec: ProtocolSpec) -> ProtocolTick:
        tick = self._generic_tick(spec)
        tick.warnings.append("Morpho Gauntlet USDC is the only live-eligible venue")
        return tick

    def _handle_26(self, spec: ProtocolSpec) -> ProtocolTick:
        ranked = [
            {
                "protocol_id": p.protocol_id,
                "score": round(p.target_apr * (1.0 - p.risk_score), 6),
            }
            for p in PROTOCOLS
            if p.swarm_id != 26
        ]
        ranked.sort(key=lambda r: r["score"], reverse=True)
        return self._base_tick(
            spec,
            action="rank_protocols",
            artifacts={"ranked": ranked},
            warnings=["ranking is advisory; no capital moves in dry-run"],
        )


def run_all_swarms(capital_usd: float, risk_budget: float = 0.30) -> SwarmSubmission:
    """Tick every swarm once and write the submission file."""
    return DeFiProtocolOS(capital_usd=capital_usd, risk_budget=risk_budget).submit()
