from __future__ import annotations

"""Reference forecaster implementation for the TOA pipeline.

:class:`KernelForecaster` is a lightweight, dependency-free kernel-regression
forecaster that:

1. Accepts a time-series of observations.
2. Fits a Nadaraya-Watson kernel smoother.
3. Projects multiple scenario paths by adding Gaussian noise scaled to the
   residual standard deviation, optionally boosted by recent trend slope.
4. Normalises path probabilities and returns them in the standard TOA format.

DeFi update (2026-07-21): when the context carries ``defi_signals``
(``polyclaw_edge``, ``vault_yield_apr``), the Monte Carlo trend is boosted by
the combined expected DeFi return and every path is tagged with a
``treasury_inflow`` estimate — the simulator's ``treasury_inflow`` objective
then prioritises DeFi paths (Polyclaw + vault yield) end-to-end.  Without
``defi_signals`` the behaviour is byte-for-byte the legacy behaviour.

For production use-cases with heavier time-series requirements, this class
can be swapped for any implementation of :class:`~agents.toa.base.ForecasterAgent`
that wraps Nixtla, NeuralForecast, Darts, or any other forecasting library.
"""

import math
import os
import random
import uuid
import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

try:
    import numpy as np
    import pandas as pd
    from sklearn.linear_model import LinearRegression  # lightweight advanced baseline
    ADVANCED_TS_AVAILABLE = True
except ImportError:
    ADVANCED_TS_AVAILABLE = False

try:
    # Production Nixtla ecosystem (optional - install statsforecast / neuralforecast)
    from statsforecast import StatsForecast
    from statsforecast.models import AutoARIMA, ETS, Naive
    NIXTLA_STATS_AVAILABLE = True
except ImportError:
    NIXTLA_STATS_AVAILABLE = False

try:
    from neuralforecast import NeuralForecast
    from neuralforecast.models import NBEATS, NHITS, LSTM
    NIXTLA_NEURAL_AVAILABLE = True
except ImportError:
    NIXTLA_NEURAL_AVAILABLE = False


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
            :attr:`TOAConfig.simulation_depth`, and ``"defi_signals"`` injects
            DeFi return expectations::

                {
                    "polyclaw_edge": 0.09,    # expected per-step edge capture
                    "vault_yield_apr": 0.06,  # vault/lending yield per step
                }

            When present, the Monte Carlo trend is boosted by the combined
            DeFi return and each path is tagged ``defi_path: True`` with a
            ``treasury_inflow`` estimate over the horizon.
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

        # --- DeFi prioritisation ----------------------------------------
        defi = context.get("defi_signals") or {}
        defi_return = float(defi.get("polyclaw_edge", 0.0)) + float(
            defi.get("vault_yield_apr", 0.0)
        )
        defi_active = defi_return > 0.0
        if defi_active:
            trend += defi_return * abs(last_smooth)

        self._log(
            "debug",
            "forecast generating paths",
            horizon=h,
            n_paths=n_paths,
            last_smooth=round(last_smooth, 4),
            trend=round(trend, 6),
            residual_std=round(residual_std, 6),
            defi_active=defi_active,
        )

        paths = []
        raw_weights: List[float] = []
        for _ in range(n_paths):
            values = self._generate_path(last_smooth, trend, residual_std, h)
            # Weight higher-utility paths by their final value relative to start
            weight = max(0.01, 1.0 + (values[-1] - last_smooth) / (abs(last_smooth) + 1e-9))
            if defi_active:
                # DeFi paths compound treasury inflow — upweight them.
                weight *= 1.0 + defi_return
            paths.append(values)
            raw_weights.append(weight)

        total_weight = sum(raw_weights)
        normalised_probs = [w / total_weight for w in raw_weights]

        result: List[Dict[str, Any]] = []
        for path, p in zip(paths, normalised_probs):
            entry: Dict[str, Any] = {
                "scenario_id": str(uuid.uuid4()),
                "probability": round(p, 6),
                "horizon": h,
                "values": [round(v, 6) for v in path],
            }
            if defi_active:
                entry["defi_path"] = True
                # Expected treasury inflow over the horizon: deployed capital
                # × combined DeFi return × steps.  inflow_scale is set to the
                # deployed capital so the simulator's sigmoid scores the
                # *rate* of inflow (defi_return × h), comparable across
                # capital sizes.
                entry["treasury_inflow"] = round(
                    abs(last_smooth) * defi_return * h, 6
                )
                entry["inflow_scale"] = round(abs(last_smooth), 6)
            result.append(entry)
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


# ---------------------------------------------------------------------------
# Production-ready Nixtla-backed forecaster (optional)
# ---------------------------------------------------------------------------

class NixtlaForecaster(ForecasterAgent):
    """
    Production-grade forecaster using the Nixtla ecosystem (statsforecast + neuralforecast).

    Same interface as KernelForecaster for zero code changes in TOA pipeline.
    Graceful fallback if Nixtla packages not installed.

    Installation (recommended for production):
        pip install "statsforecast[dev]" "neuralforecast[dev]"

    To force use: set TOA_FORECASTER_BACKEND=nixtla in environment.
    """

    agent_name = "nixtla_forecaster"

    def __init__(self, config: Optional[TOAConfig] = None) -> None:
        super().__init__(config=config)
        self._use_stats = NIXTLA_STATS_AVAILABLE
        self._use_neural = NIXTLA_NEURAL_AVAILABLE
        self._models = []

        if self._use_stats or self._use_neural:
            self._log("info", "NixtlaForecaster initialized with advanced backends available")
        else:
            self._log(
                "warning",
                "Nixtla/NeuralForecast/StatsForecast not installed. "
                "Falling back to basic behavior. Install for production accuracy: "
                "pip install statsforecast neuralforecast"
            )

    def forecast(
        self,
        context: Dict[str, Any],
        horizon: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        observations: List[float] = [float(v) for v in context.get("values", [])]
        if not observations:
            self._log("warning", "forecast called with empty observations")
            return []

        h = horizon if horizon is not None else self.config.forecast_horizon
        n_paths = int(context.get("scenario_count", self.config.simulation_depth))

        if not (self._use_stats or self._use_neural):
            # Graceful degradation - return simple trend-based paths
            self._log("info", "Using fallback trend projection (install Nixtla stack for full power)")
            return self._fallback_forecast(observations, h, n_paths)

        # Production path: use available Nixtla models
        try:
            return self._nixtla_forecast(observations, h, n_paths)
        except Exception as e:  # pragma: no cover
            self._log("error", f"Nixtla forecast failed, falling back: {e}")
            return self._fallback_forecast(observations, h, n_paths)

    def _nixtla_forecast(self, observations: List[float], horizon: int, n_paths: int) -> List[Dict[str, Any]]:
        """Core production forecasting using available Nixtla components."""
        df = pd.DataFrame({
            "unique_id": ["series_0"] * len(observations),
            "ds": pd.date_range(end=pd.Timestamp.now(), periods=len(observations), freq="D"),
            "y": observations,
        })

        models = []
        if self._use_stats:
            models.extend([AutoARIMA(), ETS(season_length=7), Naive()])
        if self._use_neural and len(observations) > 20:  # Neural needs more data
            models.extend([NBEATS(input_size=horizon, h=horizon), NHITS(input_size=horizon, h=horizon)])

        if not models:
            return self._fallback_forecast(observations, horizon, n_paths)

        sf = StatsForecast(models=models, freq="D", n_jobs=1)
        forecasts = sf.forecast(df=df, h=horizon)

        paths = []
        for model_name in [m.__class__.__name__ for m in models]:
            if model_name in forecasts.columns:
                values = forecasts[model_name].tolist()
                prob = 0.6 if "ARIMA" in model_name or "ETS" in model_name else 0.4
                paths.append({
                    "scenario_id": str(uuid.uuid4()),
                    "probability": round(prob, 6),
                    "horizon": horizon,
                    "values": [round(v, 6) for v in values],
                })

        if len(paths) > 0 and n_paths > len(paths):
            base_values = paths[0]["values"]
            for i in range(min(3, n_paths - len(paths))):
                noise = np.random.normal(0, np.std(base_values) * 0.1, len(base_values))
                noisy = [round(v + n, 6) for v, n in zip(base_values, noise)]
                paths.append({
                    "scenario_id": str(uuid.uuid4()),
                    "probability": round(0.15 / (i + 1), 6),
                    "horizon": horizon,
                    "values": noisy,
                })

        paths.sort(key=lambda x: x["probability"], reverse=True)
        return paths[:n_paths]

    def _fallback_forecast(self, observations: List[float], horizon: int, n_paths: int) -> List[Dict[str, Any]]:
        """Lightweight fallback when advanced libs unavailable."""
        if len(observations) < 3:
            return [{
                "scenario_id": str(uuid.uuid4()),
                "probability": 1.0 / n_paths,
                "horizon": horizon,
                "values": [observations[-1]] * horizon,
            } for _ in range(n_paths)]

        x = np.arange(len(observations)).reshape(-1, 1)
        y = np.array(observations)
        model = LinearRegression().fit(x, y)
        future_x = np.arange(len(observations), len(observations) + horizon).reshape(-1, 1)
        trend_pred = model.predict(future_x)

        paths = []
        std = np.std(y - model.predict(x))
        for i in range(n_paths):
            noise = np.random.normal(0, std * 0.2, horizon)
            values = [round(float(v + n), 6) for v, n in zip(trend_pred, noise)]
            paths.append({
                "scenario_id": str(uuid.uuid4()),
                "probability": round(1.0 / n_paths, 6),
                "horizon": horizon,
                "values": values,
            })
        return paths


# ---------------------------------------------------------------------------
# Factory for production use (best practice)
# ---------------------------------------------------------------------------

def get_forecaster(backend: Optional[str] = None, config: Optional[TOAConfig] = None) -> ForecasterAgent:
    """
    Production factory to select forecaster backend.

    Respects TOA_FORECASTER_BACKEND env var or explicit backend arg.
    Valid values: "kernel" (default, zero-dep) or "nixtla" (advanced).
    """
    if backend is None:
        backend = os.environ.get("TOA_FORECASTER_BACKEND", "kernel").lower()

    if backend == "nixtla" and (NIXTLA_STATS_AVAILABLE or NIXTLA_NEURAL_AVAILABLE or ADVANCED_TS_AVAILABLE):
        return NixtlaForecaster(config=config)
    else:
        if backend == "nixtla":
            logging.getLogger("sincor.toa").warning(
                "Requested nixtla backend but packages not installed. Using kernel. "
                "pip install statsforecast neuralforecast for full production power."
            )
        return KernelForecaster(config=config)
