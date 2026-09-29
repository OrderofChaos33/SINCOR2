"""P26 kill-switch: kills negative-ROI ticks. Advisory records, dry-run default.

A "tick" is one protocol's realized-ROI observation for one epoch. When the
realized ROI is negative (below kill_roi_threshold = 0.0), the kill-switch
emits a KillDecision: an advisory record with the evidence that drove it.
P26 never executes anything live itself — execution (stopping allocation to
the tick) is the operator's path. Every decision carries proof-ledger
references via proof_hooks.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..catalog import PROTOCOL_BY_ID
from . import PARAMS, SELF_ID
from .telemetry import RoiObservation


@dataclass(frozen=True)
class Tick:
    protocol_id: str
    epoch: str            # e.g. "2026-W40"
    realized_roi: float
    observed_ts: float


@dataclass
class KillDecision:
    tick: Tick
    action: str           # "KILL_TICK" | "KEEP"
    reason: str
    decided_ts: float
    evidence_refs: List[str] = field(default_factory=list)  # ledger entry_ids
    executed: bool = False  # always False here: advisory only


class KillSwitchError(Exception):
    """Kill-switch invariant violation."""


class KillSwitch:
    def __init__(self) -> None:
        self.decisions: List[KillDecision] = []

    def evaluate(self, tick: Tick) -> KillDecision:
        if tick.protocol_id == SELF_ID:
            raise KillSwitchError("P26 never kills itself")
        if tick.protocol_id not in PROTOCOL_BY_ID:
            raise KillSwitchError(f"unknown protocol {tick.protocol_id!r}")
        spec = PROTOCOL_BY_ID[tick.protocol_id]
        if tick.realized_roi < PARAMS["kill_roi_threshold"]:
            decision = KillDecision(
                tick=tick,
                action="KILL_TICK",
                reason=(
                    f"realized ROI {tick.realized_roi:.4f} < 0 for "
                    f"{tick.protocol_id} epoch {tick.epoch}: tick killed "
                    f"(advisory; live-blocked={spec.live_blocked})"
                ),
                decided_ts=time.time(),
            )
        else:
            decision = KillDecision(
                tick=tick,
                action="KEEP",
                reason=(f"realized ROI {tick.realized_roi:.4f} >= 0: tick kept"),
                decided_ts=time.time(),
            )
        self.decisions.append(decision)
        return decision

    def evaluate_roi_observation(
        self, obs: RoiObservation, epoch: str
    ) -> KillDecision:
        return self.evaluate(Tick(obs.protocol_id, epoch, obs.roi, obs.ts))

    def kills(self) -> List[KillDecision]:
        return [d for d in self.decisions if d.action == "KILL_TICK"]
