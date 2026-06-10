from __future__ import annotations

"""Reference forecaster implementation for the TOA pipeline.

:class:`KernelForecaster` is a lightweight, dependency-free kernel-regression
forecaster that:

1. Accepts a time-series of observations.
2. Fits a Nadaraya-Watson kernel smoother.
3. Projects multiple scenario paths by adding Gaussian noise scaled to the
   residual standard deviation, optionally boosted by recent trend slope.
4. Normalises path probabilities and returns them in the standard TOA format.

For production use-cases with heavier time-series requirements, this class
can be swapped for any implementation of :class:`~agents.toa.base.ForecasterAgent`
that wraps Nixtla, NeuralForecast, Darts, or any other forecasting library.
"""

import math
import random
import uuid
from typing import Any, Dict, List, Optional

from .base import ForecasterAgent
from .config import TOAConfig


class KernelForecaster(ForecasterAgent):
    """Nadaraya-Watson kernel smoother with Monte Carlo path generation.

    This implementation is intentionally self-contained (pure Python + stdlib)
    so the TOA module has zero hard runtime dependencies beyond the core
    SINCOR2 stack.  Replace or subclass for GPU-accelerated or library-backed
    forecasting.

    Parameters
    ----------
    bandwidth:
        Kernel bandwidth parameter for the Gaussian kernel.  Smaller values
        produce sharper, noisier fits; larger values produce smoother
        projections.
    noise_scale:
        Multiplier applied to the residual standard deviation when generating
        Monte Carlo paths.  Set to 0.0 for a deterministic mean projection.
    seed:
        Optional random seed for reproducible path generation.
    """

    agent_name = "kernel_forecaster"

    def __init__(
        self,
        config: Optional[TOAConfig] = None,
        bandwidth: float = 1.0,
        noise_scale: float = 0.15,
        seed: Optional[int] = None,
    ) -> None:
        super().__init__(config=config)
        self.bandwidth = bandwidth
        self.noise_scale = noise_scale
        self._rng = random.Random(seed)

    # ------------------------------------------------------------------
    # ForecasterAgent interface
    # ------------------------------------------------------------------

    def forecast(
        self,
        context: Dict[str, Any],
        horizon: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Generate Monte Carlo forecast paths.

        Parameters
        ----------
        context:
            Must contain a ``"values"`` key with a list of numeric observations
            (most-recent last).  Optionally, ``"scenario_count"`` overrides
            :attr:`TOAConfig.simulation_depth`.
        horizon:
            Forecast horizon in time-steps.  Defaults to
            :attr:`TOAConfig.forecast_horizon`.

        Returns
        -------
        List[Dict[str, Any]]
            Sorted (highest probability first) list of forecast path dicts,
            each containing ``scenario_id``, ``probability``, ``horizon``,
            and ``values``.
        """
        observations: List[float] = [float(v) for v in context.get("values", [])]
        if not observations:
            self._log("warning", "forecast called with empty observations; returning empty paths")
            return []

        h = horizon if horizon is not None else self.config.forecast_horizon
        n_paths = int(context.get("scenario_count", self.config.simulation_depth))

        smoothed = self._kernel_smooth(observations)
        last_smooth = smoothed[-1]
        trend = self._estimate_trend(smoothed)
        residual_std = self._residual_std(observations, smoothed)

        self._log(
            "debug",
            "forecast generating paths",
            horizon=h,
            n_paths=n_paths,
            last_smooth=round(last_smooth, 4),
            trend=round(trend, 6),
            residual_std=round(residual_std, 6),
        )

        paths = []
        raw_weights: List[float] = []
        for _ in range(n_paths):
            values = self._generate_path(last_smooth, trend, residual_std, h)
            # Weight higher-utility paths by their final value relative to start
            weight = max(0.01, 1.0 + (values[-1] - last_smooth) / (abs(last_smooth) + 1e-9))
            paths.append(values)
            raw_weights.append(weight)

        total_weight = sum(raw_weights)
        normalised_probs = [w / total_weight for w in raw_weights]

        result: List[Dict[str, Any]] = [
            {
                "scenario_id": str(uuid.uuid4()),
                "probability": round(p, 6),
                "horizon": h,
                "values": [round(v, 6) for v in path],
            }
            for path, p in zip(paths, normalised_probs)
        ]
        result.sort(key=lambda x: x["probability"], reverse=True)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _kernel_smooth(self, data: List[float]) -> List[float]:
        """Apply Gaussian kernel smoother to *data*."""
        n = len(data)
        result: List[float] = []
        for i in range(n):
            weights: List[float] = []
            for j in range(n):
                dist = (i - j) / max(self.bandwidth, 1e-9)
                weights.append(math.exp(-0.5 * dist * dist))
            total = sum(weights)
            smoothed = sum(w * v for w, v in zip(weights, data)) / (total or 1.0)
            result.append(smoothed)
        return result

    def _estimate_trend(self, smoothed: List[float]) -> float:
        """Estimate a simple linear trend as the mean of last-3 deltas."""
        if len(smoothed) < 2:
            return 0.0
        deltas = [smoothed[i] - smoothed[i - 1] for i in range(max(1, len(smoothed) - 3), len(smoothed))]
        return sum(deltas) / len(deltas)

    def _residual_std(self, original: List[float], smoothed: List[float]) -> float:
        """Compute the standard deviation of residuals."""
        residuals = [o - s for o, s in zip(original, smoothed)]
        if len(residuals) < 2:
            return 0.0
        mean_r = sum(residuals) / len(residuals)
        variance = sum((r - mean_r) ** 2 for r in residuals) / (len(residuals) - 1)
        return math.sqrt(variance)

    def _generate_path(
        self,
        start: float,
        trend: float,
        std: float,
        horizon: int,
    ) -> List[float]:
        """Generate one Monte Carlo path using Gaussian increments."""
        path = [start]
        for _ in range(horizon):
            noise = self._rng.gauss(0.0, std * self.noise_scale) if std > 0 else 0.0
            next_val = path[-1] + trend + noise
            path.append(next_val)
        return path[1:]  # Exclude the seed value
