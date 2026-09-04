"""Automated epoch trigger governance for production runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .vector_retrieval_engine import ThreeTierVectorEngine


@dataclass(frozen=True)
class EpochGovernanceConfig:
    tombstone_density_trigger: float = 0.15
    block_interval_trigger: int = 1_000


class AutomatedEpochGovernor:
    """Triggers off-thread cold rebuilds from production rules."""

    def __init__(self, engine: ThreeTierVectorEngine, config: EpochGovernanceConfig = EpochGovernanceConfig()) -> None:
        self.engine = engine
        self.config = config
        self._last_cutover_block: int = 0
        self._model_version: str = engine.model_version

    def should_trigger(
        self,
        *,
        current_block: int,
        model_version: Optional[str] = None,
    ) -> bool:
        metrics = self.engine.telemetry_snapshot()
        tombstone_density = float(metrics.get("tombstone_density", 0.0))
        density_hit = tombstone_density > self.config.tombstone_density_trigger

        mv = model_version or self._model_version
        model_upgrade_hit = mv != self._model_version

        block_hit = (current_block - self._last_cutover_block) >= self.config.block_interval_trigger

        return density_hit or model_upgrade_hit or block_hit

    def trigger_if_needed(
        self,
        *,
        current_block: int,
        model_version: Optional[str] = None,
    ) -> bool:
        if not self.should_trigger(current_block=current_block, model_version=model_version):
            return False

        target_model = model_version or self._model_version
        self.engine.compact_warm()
        self.engine.stage_epoch_from_warm(model_version=target_model)
        self.engine.cutover(max_pause_ms=5.0)

        self._last_cutover_block = int(current_block)
        self._model_version = target_model
        return True
