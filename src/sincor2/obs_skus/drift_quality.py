#!/usr/bin/env python3
"""
OBS-03 Drift & Quality Watch ($399/mo)

Watches agent health across three axes:

1. Quality trend tracking — wraps the real SelfImprovingQualityEngine
   (src/sincor2/quality_scoring_engine.py). Scores for each of the 9
   quality dimensions (accuracy, completeness, relevance, timeliness,
   clarity, actionability, innovation, depth, credibility) are stored
   per-agent in a durable history under data_dir(); trends (slope,
   rolling mean) and degradation alerts are computed from that history.

2. Persona drift monitoring — wraps the real constitutional-drift math
   from PersonaEngine (src/sincor2/persona_engine.py). Drift is
   1 - cosine_similarity(current_traits, archetype_anchor). Alerts fire
   when drift exceeds the engine's own bound (0.20 — the same bound
   persona_engine._recursive_blend_toward_constitution blends until).
   ADVISORY ONLY: this module never writes persona files. Re-anchoring
   is an operator decision surfaced in the watch report.

3. Behavioral anomaly detection — rolling z-score + rolling IQR on
   per-agent behavioral metrics (token usage, latency, error rate,
   quality). No ML, fully auditable. Assumptions are documented in
   BehavioralAnomalyDetector.

What this module does NOT claim: the quality engine measures scored
dimensions of deliverables, not business outcomes. A rising quality
trend is not proof of rising revenue.

Safety:
  - Never touches the money path, auth, stake/pool ledgers, task state,
    contracts, credentials, or production deploys.
  - Never auto-mutates personas (advisory only).
  - The Tap protocol in src/sincor2/underwriting/taps/ is payments-only
    (transfer/clawback). OBS-03 deliberately does NOT plug into it.
    Ingestion is a passive observer: call
    DriftQualityWatch.observe_deliverable() from any execution path that
    already produces a QualityScore (e.g. wherever
    assess_deliverable_quality() is called). See observe_deliverable() docs.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sincor2.data_paths import data_dir
from sincor2.quality_scoring_engine import (
    QualityDimension,
    QualityScore,
    SelfImprovingQualityEngine,
)
from sincor2.persona_engine import PersonaEngine, PersonaVector

# ---------------------------------------------------------------------------
# Tunable, documented bounds
# ---------------------------------------------------------------------------

TREND_WINDOW = 20            # last N samples used for slope / rolling mean
MIN_TREND_SAMPLES = 5        # need at least this many points before trending
SLOPE_ALERT_PER_STEP = -0.015  # per-deliverable slope (0..1 score scale) that
                               # counts as degradation, e.g. -0.015/sample loses
                               # ~0.30 score points over a 20-sample window
ROLLING_MEAN_FLOOR = 0.60    # rolling-mean below this on any dimension alerts

DRIFT_ALERT_BOUND = 0.20     # matches persona_engine._recursive_blend_toward_constitution
                             # (it blends until drift <= 0.20)
DRIFT_WATCH_BOUND = 0.12     # early-warning level; "watch", not "alert"

ANOMALY_MIN_SAMPLES = 8      # baseline size before anomaly judgments
ANOMALY_WINDOW = 50          # rolling baseline window
ANOMALY_Z_THRESHOLD = 3.0    # |z| above this flags
ANOMALY_IQR_FACTOR = 1.5     # Tukey fences multiplier

BEHAVIORAL_METRICS = ("token_usage", "latency_ms", "error_rate", "quality_overall")

_STORE_FILENAME = "obs03_quality_trends.json"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _dim_key(dim: Any) -> str:
    return dim.value if isinstance(dim, QualityDimension) else str(dim)


def _least_squares_slope(values: List[float]) -> float:
    """Slope of the best-fit line (per-sample index). Plain math, no fitting magic."""
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    denom = sum((i - mean_x) ** 2 for i in range(n))
    if denom == 0:
        return 0.0
    return sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values)) / denom


# ---------------------------------------------------------------------------
# 1. Quality trend tracking
# ---------------------------------------------------------------------------

class QualityTrendTracker:
    """Durable per-agent, per-dimension quality-score histories + trend math.

    Wraps the real SelfImprovingQualityEngine for scoring; this class adds
    the durable history layer the engine itself does not persist to disk.
    """

    def __init__(self, store_path: Optional[Path] = None):
        self.engine = SelfImprovingQualityEngine()
        self.store_path = Path(store_path) if store_path else data_dir() / "obs_skus" / _STORE_FILENAME
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load()

    # -- durable store ------------------------------------------------------

    def _load(self) -> Dict[str, Any]:
        if self.store_path.exists():
            try:
                with open(self.store_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "agents" in data:
                    return data
            except (json.JSONDecodeError, OSError):
                pass  # corrupt store -> start fresh rather than crash the watcher
        return {"agents": {}, "updated": _utcnow()}

    def _save(self) -> None:
        self._state["updated"] = _utcnow()
        tmp = self.store_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2)
        os.replace(tmp, self.store_path)  # atomic publish

    def _agent_state(self, agent_id: str) -> Dict[str, Any]:
        return self._state["agents"].setdefault(
            agent_id, {"dimensions": {d.value: [] for d in QualityDimension}, "overall": []}
        )

    # -- ingestion ----------------------------------------------------------

    def ingest(
        self,
        agent_id: str,
        deliverable_id: str,
        dimension_scores: Dict[Any, float],
        overall_score: float,
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record one scored deliverable. Scores must be 0..1 (validated)."""
        if not dimension_scores:
            raise ValueError("dimension_scores must not be empty")
        normalized: Dict[str, float] = {}
        for dim, score in dimension_scores.items():
            value = float(score)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"score for {dim} out of range [0,1]: {value}")
            normalized[_dim_key(dim)] = value
        ts = timestamp or _utcnow()
        state = self._agent_state(agent_id)
        for dim_key, value in normalized.items():
            series = state["dimensions"].setdefault(dim_key, [])
            series.append({"ts": ts, "deliverable_id": deliverable_id, "score": value})
        state["overall"].append({"ts": ts, "deliverable_id": deliverable_id, "score": _clamp01(overall_score)})
        self._save()
        return {"agent_id": agent_id, "deliverable_id": deliverable_id, "recorded_at": ts}

    def ingest_quality_score(self, agent_id: str, score: QualityScore) -> Dict[str, Any]:
        """Convenience wrapper around the real QualityScore dataclass."""
        return self.ingest(
            agent_id,
            score.deliverable_id,
            {dim: value for dim, value in score.dimension_scores.items()},
            score.overall_score,
            timestamp=getattr(score, "created", None) or None,
        )

    async def score_deliverable(
        self,
        agent_id: str,
        deliverable_id: str,
        deliverable_content: Dict[str, Any],
        deliverable_type: str,
        client_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run the REAL SelfImprovingQualityEngine assessment and record it.

        This is the live path: heuristic 9-dimension scoring from
        assess_deliverable_quality(), stored into the durable trend history.
        """
        score = await self.engine.assess_deliverable_quality(
            deliverable_id, deliverable_content, deliverable_type, agent_id,
            client_context or {},
        )
        return self.ingest_quality_score(agent_id, score)

    # -- trend math ---------------------------------------------------------

    @staticmethod
    def _series_stats(series: List[Dict[str, Any]]) -> Dict[str, Any]:
        window = [p["score"] for p in series[-TREND_WINDOW:]]
        n = len(window)
        rolling_mean = sum(window) / n if n else 0.0
        slope = _least_squares_slope(window) if n >= MIN_TREND_SAMPLES else 0.0
        return {
            "n": n,
            "rolling_mean": round(rolling_mean, 4),
            "slope_per_sample": round(slope, 5),
            "trendable": n >= MIN_TREND_SAMPLES,
            "degrading": n >= MIN_TREND_SAMPLES and slope < SLOPE_ALERT_PER_STEP,
            "below_floor": n >= MIN_TREND_SAMPLES and rolling_mean < ROLLING_MEAN_FLOOR,
        }

    def dimension_trend(self, agent_id: str, dimension: Any) -> Dict[str, Any]:
        state = self._state["agents"].get(agent_id)
        if not state:
            return {"agent_id": agent_id, "dimension": _dim_key(dimension), "n": 0,
                    "trendable": False, "note": "no history recorded"}
        series = state["dimensions"].get(_dim_key(dimension), [])
        return {"agent_id": agent_id, "dimension": _dim_key(dimension),
                **self._series_stats(series)}

    def agent_overview(self, agent_id: str) -> Dict[str, Any]:
        """Per-dimension trends + overall trend for one agent."""
        state = self._state["agents"].get(agent_id)
        if not state:
            return {"agent_id": agent_id, "dimensions": {}, "overall": None,
                    "alerts": [], "note": "no history recorded"}
        dims = {key: self._series_stats(series) for key, series in state["dimensions"].items()}
        overall = self._series_stats(state["overall"]) if state["overall"] else None
        alerts: List[str] = []
        for key, stats in dims.items():
            if stats.get("degrading"):
                alerts.append(f"degrading:{key} slope={stats['slope_per_sample']}/sample")
            if stats.get("below_floor"):
                alerts.append(f"low_absolute:{key} rolling_mean={stats['rolling_mean']}")
        if overall and overall.get("degrading"):
            alerts.append(f"degrading:overall slope={overall['slope_per_sample']}/sample")
        return {"agent_id": agent_id, "dimensions": dims, "overall": overall, "alerts": alerts}


# ---------------------------------------------------------------------------
# 2. Persona drift monitoring (advisory only — never mutates personas)
# ---------------------------------------------------------------------------

class PersonaDriftMonitor:
    """Wraps PersonaEngine's constitutional-drift math as a read-only watch.

    SAFETY NOTE: PersonaEngine.calculate_constitutional_drift() rebuilds the
    archetype anchor via _create_from_archetype(), which CALLS _save_persona()
    and would OVERWRITE the agent's live persona file with archetype defaults.
    We deliberately do NOT call it here. Instead we rebuild the same anchor
    from a disposable PersonaEngine rooted in a temp dir (no side effects on
    the real persona_dir), read the live persona JSON read-only, and apply
    the same drift formula: drift = 1 - cosine(current, anchor), clamped 0..1.
    """

    def __init__(self, persona_dir: str = "personas"):
        self.persona_dir = persona_dir

    @staticmethod
    def _load_live_persona(persona_dir: str, agent_id: str) -> PersonaVector:
        path = Path(persona_dir) / f"{agent_id}_persona.json"
        if not path.exists():
            raise FileNotFoundError(f"no persona file for agent {agent_id} at {path}")
        with open(path, "r", encoding="utf-8") as f:
            return PersonaVector(**json.load(f))

    @staticmethod
    def _anchor_persona(agent_id: str, archetype: str) -> Tuple[PersonaEngine, PersonaVector]:
        """Build the archetype anchor in a throwaway dir; real code, zero side effects."""
        tmp = tempfile.mkdtemp(prefix="obs03_anchor_")
        engine = PersonaEngine(f"{agent_id}__obs03_anchor", archetype, persona_dir=tmp)
        return engine, engine.current_persona

    def drift(self, agent_id: str, archetype: Optional[str] = None) -> Dict[str, Any]:
        live = self._load_live_persona(self.persona_dir, agent_id)
        anchor_type = archetype or live.archetype
        engine, anchor = self._anchor_persona(agent_id, anchor_type)
        try:
            similarity = engine._cosine_similarity(
                engine._persona_to_vector(live), engine._persona_to_vector(anchor)
            )
        finally:
            # clean up the throwaway anchor dir
            import shutil
            shutil.rmtree(Path(engine.persona_dir), ignore_errors=True)
        drift_value = max(0.0, min(1.0, 1.0 - similarity))
        if drift_value > DRIFT_ALERT_BOUND:
            status = "drift_alert"
        elif drift_value > DRIFT_WATCH_BOUND:
            status = "watch"
        else:
            status = "stable"
        return {
            "agent_id": agent_id,
            "archetype": anchor_type,
            "drift": round(drift_value, 4),
            "cosine_similarity": round(similarity, 4),
            "alert_bound": DRIFT_ALERT_BOUND,
            "watch_bound": DRIFT_WATCH_BOUND,
            "status": status,
            "persona_version": live.version,
        }

    def re_anchor_advisory(self, agent_id: str, drift_report: Dict[str, Any]) -> Dict[str, Any]:
        """Bounded, advisory-only re-anchor plan. DOES NOT MUTATE ANYTHING.

        Re-anchoring (PersonaEngine._recursive_blend_toward_constitution) is an
        operator decision: it changes agent behavior and must be reviewed.
        """
        return {
            "agent_id": agent_id,
            "action": "advisory_only",
            "drift": drift_report.get("drift"),
            "status": drift_report.get("status"),
            "recommended_steps": [
                "Review recent interaction feedback labels for the agent — look for "
                "'harmful' or low-quality flags that drove the drift.",
                "Create a persona checkpoint before any change (PersonaEngine.create_checkpoint()).",
                "If the drift is unwanted, an operator may run the engine's bounded "
                "re-anchor blend toward the constitutional anchor (max 3 passes, stops "
                f"when drift <= {DRIFT_ALERT_BOUND}).",
                "Re-run drift() after the change to confirm the agent is back in bounds.",
            ],
            "mutated": False,
            "note": "OBS-03 never writes persona files. Apply changes through the "
                    "PersonaEngine with human approval.",
        }


# ---------------------------------------------------------------------------
# 3. Behavioral anomaly detection (z-score + IQR, no ML)
# ---------------------------------------------------------------------------

class BehavioralAnomalyDetector:
    """Rolling z-score + Tukey IQR anomaly flags per (agent, metric).

    Documented assumptions and limits:
      - Needs ANOMALY_MIN_SAMPLES (8) baseline points before judging; until
        then every evaluation reports status "insufficient_baseline".
      - The baseline is the agent's own recent history (rolling window of 50);
        there is no cross-agent model and no seasonality handling.
      - z-score assumes roughly symmetric data; IQR catches skew the z-test
        misses. A flag is a statistical outlier, not proof of malfunction.
      - Zero-spread baselines (std == 0): z/IQR fences are degenerate, so a
        shift is flagged only when the latest value moves more than 10%
        relative to the constant baseline. Tiny jitter on a flat line is
        treated as noise; a real jump (e.g. 40x token usage) still flags.
      - Slow ramps below the thresholds will NOT flag — gradual drift is the
        trend tracker's job, not this detector's.
      - Adversarial limitation: if anomalies are interleaved regularly (e.g.
        every 3rd sample spikes), the rolling baseline absorbs them and most
        stop flagging. The detector assumes an honest baseline; persistent
        adversarial behavior belongs to rate-limiting and human review.
      - Histories are per-process memory. Restarting clears baselines; the
        quality trend history is the durable one.
    """

    def __init__(
        self,
        z_threshold: float = ANOMALY_Z_THRESHOLD,
        iqr_factor: float = ANOMALY_IQR_FACTOR,
        min_samples: int = ANOMALY_MIN_SAMPLES,
        window: int = ANOMALY_WINDOW,
    ):
        self.z_threshold = z_threshold
        self.iqr_factor = iqr_factor
        self.min_samples = min_samples
        self.window = window
        self._history: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}

    def ingest(self, agent_id: str, metric: str, value: float,
               timestamp: Optional[str] = None) -> Dict[str, Any]:
        if metric not in BEHAVIORAL_METRICS:
            raise ValueError(f"unknown metric {metric!r}; expected one of {BEHAVIORAL_METRICS}")
        key = (agent_id, metric)
        series = self._history.setdefault(key, [])
        series.append({"ts": timestamp or _utcnow(), "value": float(value)})
        del series[: max(0, len(series) - self.window)]
        return self.evaluate(agent_id, metric)

    @staticmethod
    def _percentile(sorted_vals: List[float], pct: float) -> float:
        if not sorted_vals:
            return 0.0
        k = (len(sorted_vals) - 1) * (pct / 100.0)
        lo = math.floor(k)
        hi = math.ceil(k)
        return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)

    def evaluate(self, agent_id: str, metric: str) -> Dict[str, Any]:
        series = self._history.get((agent_id, metric), [])
        n = len(series)
        if n < self.min_samples:
            return {"agent_id": agent_id, "metric": metric, "n": n,
                    "status": "insufficient_baseline", "is_anomaly": False,
                    "note": f"need {self.min_samples} samples before judging"}
        baseline = [p["value"] for p in series[:-1]]
        latest = series[-1]["value"]
        mean = sum(baseline) / len(baseline)
        variance = sum((v - mean) ** 2 for v in baseline) / len(baseline)
        std = math.sqrt(variance)
        zero_spread = std == 0.0
        z = (latest - mean) / std if std > 0 else 0.0
        ordered = sorted(baseline)
        q1 = self._percentile(ordered, 25)
        q3 = self._percentile(ordered, 75)
        iqr = q3 - q1
        lower = q1 - self.iqr_factor * iqr
        upper = q3 + self.iqr_factor * iqr
        # Degenerate fences (iqr == 0) are meaningless; on a zero-spread
        # baseline flag only genuine shifts: >10% relative change.
        if zero_spread:
            denom = abs(mean) if mean != 0 else 1.0
            iqr_flag = abs(latest - mean) / denom > 0.10
            zero_spread_flag = iqr_flag
        else:
            iqr_flag = latest < lower or latest > upper
            zero_spread_flag = False
        z_flag = abs(z) > self.z_threshold
        is_anomaly = bool(z_flag or iqr_flag)
        return {
            "agent_id": agent_id, "metric": metric, "n": n,
            "status": "anomaly" if is_anomaly else "normal",
            "is_anomaly": is_anomaly,
            "latest": latest,
            "baseline_mean": round(mean, 4),
            "baseline_std": round(std, 4),
            "z_score": round(z, 3),
            "z_flag": bool(z_flag),
            "zero_spread_baseline": zero_spread,
            "zero_spread_shift": bool(zero_spread_flag),
            "iqr_fences": [round(lower, 4), round(upper, 4)],
            "iqr_flag": bool(iqr_flag),
            "thresholds": {"z": self.z_threshold, "iqr_factor": self.iqr_factor},
        }


# ---------------------------------------------------------------------------
# 4. Watch report
# ---------------------------------------------------------------------------

@dataclass
class WatchReport:
    """Structured drift+quality watch report for a fleet slice."""
    generated_at: str = field(default_factory=_utcnow)
    agents: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"generated_at": self.generated_at, "agents": self.agents}

    def render_markdown(self) -> str:
        lines = [
            "# Drift & Quality Watch",
            f"_Generated {self.generated_at} · OBS-03_",
            "",
            "Agent health overview: quality trends, persona drift, and behavioral "
            "anomalies. Flags are signals to review, not verdicts.",
            "",
        ]
        if not self.agents:
            lines.append("No agents with recorded history yet.")
            return "\n".join(lines)
        for agent in self.agents:
            aid = agent["agent_id"]
            lines.append(f"## {aid}")
            quality = agent.get("quality", {})
            alerts = quality.get("alerts", [])
            dims = quality.get("dimensions", {})
            improving = [k for k, s in dims.items()
                         if s.get("trendable") and s["slope_per_sample"] > 0]
            if alerts:
                lines.append(f"- **Needs attention:** {'; '.join(alerts)}")
            else:
                lines.append("- **Quality:** no degradation flags across tracked dimensions.")
            if improving:
                lines.append(f"- **Improving:** {', '.join(improving)} "
                             f"({len(improving)}/{len(dims)} dimensions trending up)")
            drift = agent.get("drift")
            if drift and drift.get("status") not in (None, "no_persona_on_file") \
                    and "drift" in drift:
                lines.append(f"- **Persona drift:** {drift['drift']:.3f} "
                             f"(bound {drift['alert_bound']}) — status: {drift['status']}")
            anomalies = [a for a in agent.get("anomalies", []) if a.get("is_anomaly")]
            if anomalies:
                for a in anomalies:
                    lines.append(f"- **Behavioral anomaly:** {a['metric']} latest={a['latest']} "
                                 f"(z={a['z_score']}, baseline mean={a['baseline_mean']})")
            advisory = agent.get("advisory")
            if advisory:
                lines.append("- **Recommended next step:** " + advisory["recommended_steps"][0])
                lines.append("  (Full re-anchor plan is advisory only — no automatic changes.)")
            lines.append("")
        lines.append("---")
        lines.append("Scope note: quality scores measure the 9 scored dimensions of "
                     "deliverables, not downstream business outcomes. Treat flags as "
                     "review prompts.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5. Facade + behavioral tap
# ---------------------------------------------------------------------------

class DriftQualityWatch:
    """One entry point for OBS-03.

    Behavioral tap (read-only observer): the underwriting Tap protocol
    (src/sincor2/underwriting/taps/base.py) is a payments interface
    (transfer/clawback) on the money path — OBS-03 deliberately does NOT
    plug into it. Instead, any execution path that already produces a
    QualityScore calls observe_deliverable() with the scored dimensions and
    the run's behavioral metrics. Example integration point:
    monetization_engine._execute_opportunity() calls
    SelfImprovingQualityEngine.assess_deliverable_quality(); its result can
    be forwarded to observe_deliverable() without touching payment flow.
    No existing module is modified by this wiring.
    """

    def __init__(self, store_path: Optional[Path] = None, persona_dir: str = "personas"):
        self.tracker = QualityTrendTracker(store_path=store_path)
        self.drift_monitor = PersonaDriftMonitor(persona_dir=persona_dir)
        self.anomalies = BehavioralAnomalyDetector()

    def observe_deliverable(
        self,
        agent_id: str,
        deliverable_id: str,
        dimension_scores: Dict[Any, float],
        overall_score: float,
        behavioral: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """Passive ingestion hook: record one scored deliverable + behavior.

        behavioral: mapping of metric name -> value for any of
        BEHAVIORAL_METRICS (token_usage, latency_ms, error_rate, quality_overall).
        Returns the per-metric anomaly evaluations for immediate routing.
        """
        recorded = self.tracker.ingest(agent_id, deliverable_id, dimension_scores,
                                       overall_score)
        anomaly_results: Dict[str, Dict[str, Any]] = {}
        for metric, value in (behavioral or {}).items():
            anomaly_results[metric] = self.anomalies.ingest(agent_id, metric, float(value))
        return {**recorded, "anomalies": anomaly_results}

    def agent_report(self, agent_id: str,
                     include_drift: bool = True) -> Tuple[Dict[str, Any], str]:
        quality = self.tracker.agent_overview(agent_id)
        drift: Optional[Dict[str, Any]] = None
        advisory: Optional[Dict[str, Any]] = None
        try:
            drift = self.drift_monitor.drift(agent_id)
            if drift["status"] == "drift_alert":
                advisory = self.drift_monitor.re_anchor_advisory(agent_id, drift)
        except FileNotFoundError:
            drift = {"agent_id": agent_id, "status": "no_persona_on_file"}
        anomalies = [self.anomalies.evaluate(agent_id, m) for m in BEHAVIORAL_METRICS]
        entry = {"agent_id": agent_id, "quality": quality, "drift": drift,
                 "anomalies": anomalies, "advisory": advisory}
        report = WatchReport(agents=[entry])
        return report.to_dict(), report.render_markdown()

    def fleet_report(self, agent_ids: List[str]) -> Tuple[Dict[str, Any], str]:
        entries = []
        for agent_id in agent_ids:
            entry, _ = self.agent_report(agent_id)
            entries.append(entry["agents"][0])
        report = WatchReport(agents=entries)
        return report.to_dict(), report.render_markdown()


# ---------------------------------------------------------------------------
# Runnable end-to-end walkthrough (no side effects outside temp dirs)
# ---------------------------------------------------------------------------

def run_walkthrough() -> Dict[str, Any]:
    """Proves OBS-03 works end-to-end in one call:

    ingest agent history -> quality trends computed -> drift alert fires on
    crafted drift -> anomaly flagged -> watch report generated.
    Uses temp dirs only; touches nothing real.
    """
    import shutil

    tmp = Path(tempfile.mkdtemp(prefix="obs03_walkthrough_"))
    persona_dir = tmp / "personas"
    persona_dir.mkdir()
    watch = DriftQualityWatch(store_path=tmp / "trends.json",
                              persona_dir=str(persona_dir))

    # 1) real persona for the agent, then craft drift by perturbing traits
    agent_id = "obs03-demo-agent"
    PersonaEngine(agent_id, "Scout", persona_dir=str(persona_dir))
    PersonaEngine(f"{agent_id}_stable", "Scout", persona_dir=str(persona_dir))
    live_path = persona_dir / f"{agent_id}_persona.json"
    live = json.loads(live_path.read_text())
    for trait in ("openness", "risk_tolerance", "directness", "extraversion"):
        live[trait] = 0.05  # collapse traits -> far from the Scout anchor
    live_path.write_text(json.dumps(live, indent=2))
    import hashlib
    file_before = hashlib.sha256(live_path.read_bytes()).hexdigest()

    # 2) ingest 12 scored deliverables with a degrading clarity trend
    dims = {d.value: 0.85 for d in QualityDimension}
    for i in range(12):
        step_dims = dict(dims)
        step_dims["clarity"] = round(0.85 - i * 0.03, 3)  # steady decline
        watch.observe_deliverable(
            agent_id, f"deliv-{i:02d}", step_dims, 0.85 - i * 0.01,
            behavioral={"token_usage": 1200.0, "latency_ms": 900.0,
                        "error_rate": 0.01, "quality_overall": 0.85 - i * 0.01},
        )
    # 3) spike token usage -> anomaly
    watch.observe_deliverable(
        agent_id, "deliv-spike", dims, 0.80,
        behavioral={"token_usage": 48000.0, "latency_ms": 910.0,
                    "error_rate": 0.02, "quality_overall": 0.80},
    )

    # 4) drift check on crafted drift + stable control
    drift_hit = watch.drift_monitor.drift(agent_id)
    drift_ok = watch.drift_monitor.drift(f"{agent_id}_stable")

    # 5) persona file untouched by the whole pipeline (advisory only)
    file_after = hashlib.sha256(live_path.read_text().encode()).hexdigest()

    report_json, report_md = watch.agent_report(agent_id)

    steps = {
        "quality_trend_clarity": report_json["agents"][0]["quality"]["dimensions"]["clarity"],
        "drift_alert_fired": drift_hit["status"] == "drift_alert",
        "drift_value": drift_hit["drift"],
        "stable_control_status": drift_ok["status"],
        "token_anomaly": [a for a in report_json["agents"][0]["anomalies"]
                          if a["metric"] == "token_usage"][0],
        "persona_file_unmutated": file_before == file_after,
        "report_markdown_lines": len(report_md.splitlines()),
    }
    checks = [
        steps["quality_trend_clarity"]["degrading"] is True,
        steps["drift_alert_fired"] is True,
        steps["stable_control_status"] == "stable",
        steps["token_anomaly"]["is_anomaly"] is True,
        steps["persona_file_unmutated"] is True,
        steps["report_markdown_lines"] > 10,
    ]
    steps["ALL_GREEN"] = all(checks)
    shutil.rmtree(tmp, ignore_errors=True)
    return steps


if __name__ == "__main__":
    result = run_walkthrough()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ALL_GREEN"] else 1)
