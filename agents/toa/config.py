from __future__ import annotations

"""Configuration for the Temporal Optimization Agent (TOA).

All settings are read from environment variables with safe defaults so the
module is deployable without any additional configuration.

DeFi update (2026-07-21): ``treasury_inflow`` is now a first-class objective.
Default weights were rebalanced so fee capture / treasury inflow carries
real weight in Monte Carlo scoring; override with TOA_OBJECTIVE_WEIGHTS.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List

# Default objective weights, rebalanced to prioritise DeFi treasury inflow
# alongside revenue.  Sums to 1.0.
DEFAULT_OBJECTIVE_WEIGHTS: Dict[str, float] = {
    "revenue": 0.30,
    "treasury_inflow": 0.20,
    "risk": 0.20,
    "timeline": 0.15,
    "compliance": 0.075,
    "governance": 0.075,
}


@dataclass
class TOAConfig:
    """Immutable configuration snapshot for a TOA session.

    Environment variable overrides are applied in :func:`from_env`.
    """

    # Simulation depth: how many parallel scenario paths to evaluate.
    simulation_depth: int = 50
    # Minimum probability threshold to keep a scenario in the candidate set.
    collapse_threshold: float = 0.05
    # Maximum number of top paths returned after collapse.
    top_k_paths: int = 5
    # Forecast horizon in time-steps (unit is context-dependent).
    forecast_horizon: int = 12
    # Number of Monte Carlo iterations per scenario.
    monte_carlo_iterations: int = 1_000
    # Objective function weights (must sum to 1.0 or be normalised internally).
    objective_weights: Dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_OBJECTIVE_WEIGHTS)
    )
    # Registered objective labels in priority order (highest first).
    objective_priority: List[str] = field(default_factory=lambda: [
        "treasury_inflow", "revenue", "risk", "timeline", "compliance", "governance"
    ])
    # Where to persist TOA state across sessions (empty = memory-only).
    state_path: str = ""
    # Whether to enable structured JSON logging.
    structured_logging: bool = True
    # Maximum feedback events retained in the rolling feedback buffer.
    feedback_buffer_size: int = 500
    # Timeout in seconds for a single TOA collapse run.
    run_timeout_seconds: int = 120

    @classmethod
    def from_env(cls) -> "TOAConfig":
        """Create a :class:`TOAConfig` from environment variables.

        All variables are prefixed with ``TOA_``.  Unset variables fall back to
        the dataclass defaults.

        Example::

            TOA_SIMULATION_DEPTH=100 TOA_COLLAPSE_THRESHOLD=0.1 python ...
        """
        weights_env = os.environ.get("TOA_OBJECTIVE_WEIGHTS", "")
        weights: Dict[str, float] = {}
        if weights_env:
            for part in weights_env.split(","):
                try:
                    key, val = part.split(":")
                    weights[key.strip()] = float(val.strip())
                except ValueError:
                    pass

        return cls(
            simulation_depth=int(os.environ.get("TOA_SIMULATION_DEPTH", 50)),
            collapse_threshold=float(os.environ.get("TOA_COLLAPSE_THRESHOLD", 0.05)),
            top_k_paths=int(os.environ.get("TOA_TOP_K_PATHS", 5)),
            forecast_horizon=int(os.environ.get("TOA_FORECAST_HORIZON", 12)),
            monte_carlo_iterations=int(os.environ.get("TOA_MONTE_CARLO_ITERATIONS", 1_000)),
            objective_weights=weights or dict(DEFAULT_OBJECTIVE_WEIGHTS),
            state_path=os.environ.get("TOA_STATE_PATH", ""),
            structured_logging=os.environ.get("TOA_STRUCTURED_LOGGING", "true").lower() == "true",
            feedback_buffer_size=int(os.environ.get("TOA_FEEDBACK_BUFFER_SIZE", 500)),
            run_timeout_seconds=int(os.environ.get("TOA_RUN_TIMEOUT_SECONDS", 120)),
        )
