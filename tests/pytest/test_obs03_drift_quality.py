"""OBS-03 Drift & Quality Watch tests.

Loads src/sincor2/obs_skus/drift_quality.py via importlib (B1 owns the
obs_skus/__init__/registry, so this suite does not depend on it).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

MOD_PATH = Path(__file__).resolve().parents[2] / "src" / "sincor2" / "obs_skus" / "drift_quality.py"


def load_module():
    import sys
    spec = importlib.util.spec_from_file_location("obs03_drift_quality", MOD_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["obs03_drift_quality"] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def dq():
    return load_module()


@pytest.fixture()
def watch(dq, tmp_path, monkeypatch):
    # default store path must land in the temp dir, never the repo data dir
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    return dq.DriftQualityWatch(persona_dir=str(persona_dir))


def nine_dims(dq, value=0.85):
    return {d: value for d in dq.QualityDimension}


# ---------------------------------------------------------------------------
# 1. Trend detection on synthetic histories
# ---------------------------------------------------------------------------

class TestQualityTrends:
    def test_degrading_trend_alerts(self, dq, watch):
        for i in range(12):
            dims = nine_dims(dq)
            dims[dq.QualityDimension.CLARITY] = round(0.85 - i * 0.03, 3)
            watch.observe_deliverable("agent-a", f"d{i}", dims, 0.85 - i * 0.01)
        trend = watch.tracker.dimension_trend("agent-a", dq.QualityDimension.CLARITY)
        assert trend["trendable"] is True
        assert trend["slope_per_sample"] < 0
        assert trend["degrading"] is True
        overview = watch.tracker.agent_overview("agent-a")
        assert any(a.startswith("degrading:clarity") for a in overview["alerts"])

    def test_stable_series_no_alert(self, dq, watch):
        for i in range(12):
            watch.observe_deliverable("agent-b", f"d{i}", nine_dims(dq, 0.85), 0.85)
        overview = watch.tracker.agent_overview("agent-b")
        assert overview["alerts"] == []

    def test_improving_series_no_degradation_flag(self, dq, watch):
        for i in range(12):
            dims = nine_dims(dq)
            dims[dq.QualityDimension.ACCURACY] = round(0.60 + i * 0.02, 3)
            watch.observe_deliverable("agent-c", f"d{i}", dims, 0.60 + i * 0.02)
        trend = watch.tracker.dimension_trend("agent-c", dq.QualityDimension.ACCURACY)
        assert trend["degrading"] is False
        assert trend["slope_per_sample"] > 0

    def test_below_floor_alert(self, dq, watch):
        for i in range(8):
            watch.observe_deliverable("agent-d", f"d{i}", nine_dims(dq, 0.40), 0.40)
        overview = watch.tracker.agent_overview("agent-d")
        assert any(a.startswith("low_absolute:") for a in overview["alerts"])

    def test_too_few_samples_not_trendable(self, dq, watch):
        for i in range(3):
            watch.observe_deliverable("agent-e", f"d{i}", nine_dims(dq), 0.85)
        trend = watch.tracker.dimension_trend("agent-e", dq.QualityDimension.CLARITY)
        assert trend["trendable"] is False
        assert trend["degrading"] is False

    def test_history_is_durable_on_disk(self, dq, watch, tmp_path):
        watch.observe_deliverable("agent-f", "d0", nine_dims(dq), 0.80)
        store = tmp_path / "data" / "obs_skus" / "obs03_quality_trends.json"
        assert store.exists()
        data = json.loads(store.read_text())
        assert data["agents"]["agent-f"]["overall"][0]["score"] == 0.80
        # reload from disk keeps the history
        watch2 = dq.DriftQualityWatch(store_path=store, persona_dir=watch.drift_monitor.persona_dir)
        assert watch2.tracker.dimension_trend("agent-f", dq.QualityDimension.CLARITY)["n"] == 1

    def test_out_of_range_score_rejected(self, dq, watch):
        with pytest.raises(ValueError):
            watch.observe_deliverable("agent-g", "d0", {dq.QualityDimension.CLARITY: 1.5}, 0.9)

    def test_ingest_real_quality_score_object(self, dq, watch):
        from sincor2.quality_scoring_engine import QualityScore
        score = QualityScore(
            deliverable_id="deliv-x", overall_score=0.77,
            dimension_scores={d: 0.77 for d in dq.QualityDimension},
            feedback_sources={}, confidence=0.9, improvement_areas=[],
            strengths=[], benchmark_comparison={}, created="2026-09-29T00:00:00",
        )
        watch.tracker.ingest_quality_score("agent-h", score)
        trend = watch.tracker.dimension_trend("agent-h", dq.QualityDimension.DEPTH)
        assert trend["n"] == 1

    def test_score_deliverable_runs_real_engine(self, dq, watch):
        """The live path: real assess_deliverable_quality() -> durable history."""
        import asyncio
        result = asyncio.run(watch.tracker.score_deliverable(
            "agent-live", "deliv-live",
            {"word_count": 800, "sections": ["a", "b"],
             "has_citations": True, "meets_requirements": True,
             "deadline_met": True},
            "report", {},
        ))
        assert result["agent_id"] == "agent-live"
        assert result["deliverable_id"] == "deliv-live"
        # all 9 real dimensions recorded
        overview = watch.tracker.agent_overview("agent-live")
        assert len(overview["dimensions"]) == len(dq.QualityDimension) == 9
        assert all(d["n"] == 1 for d in overview["dimensions"].values())


# ---------------------------------------------------------------------------
# 2. Drift-bound alerting (+ no auto-mutation)
# ---------------------------------------------------------------------------

class TestPersonaDrift:
    def _make_persona(self, dq, persona_dir, agent_id, archetype="Scout", perturb=None):
        engine = dq.PersonaEngine(agent_id, archetype, persona_dir=str(persona_dir))
        if perturb:
            path = Path(persona_dir) / f"{agent_id}_persona.json"
            data = json.loads(path.read_text())
            data.update(perturb)
            path.write_text(json.dumps(data, indent=2))
        return engine

    def test_stable_persona_no_alert(self, dq, tmp_path):
        persona_dir = tmp_path / "personas"
        persona_dir.mkdir()
        self._make_persona(dq, persona_dir, "stable-agent")
        mon = dq.PersonaDriftMonitor(persona_dir=str(persona_dir))
        result = mon.drift("stable-agent")
        assert result["status"] == "stable"
        assert result["drift"] < dq.DRIFT_WATCH_BOUND

    def test_crafted_drift_alerts(self, dq, tmp_path):
        persona_dir = tmp_path / "personas"
        persona_dir.mkdir()
        self._make_persona(
            dq, persona_dir, "drifted-agent",
            perturb={"openness": 0.05, "risk_tolerance": 0.05,
                     "directness": 0.05, "extraversion": 0.05},
        )
        mon = dq.PersonaDriftMonitor(persona_dir=str(persona_dir))
        result = mon.drift("drifted-agent")
        assert result["status"] == "drift_alert"
        assert result["drift"] > dq.DRIFT_ALERT_BOUND
        advisory = mon.re_anchor_advisory("drifted-agent", result)
        assert advisory["mutated"] is False
        assert advisory["action"] == "advisory_only"

    def test_drift_check_never_mutates_persona_file(self, dq, tmp_path):
        persona_dir = tmp_path / "personas"
        persona_dir.mkdir()
        self._make_persona(dq, persona_dir, "drifted-2",
                           perturb={"openness": 0.05, "risk_tolerance": 0.05})
        before = hashlib.sha256((persona_dir / "drifted-2_persona.json").read_bytes()).hexdigest()
        mon = dq.PersonaDriftMonitor(persona_dir=str(persona_dir))
        mon.drift("drifted-2")
        after = hashlib.sha256((persona_dir / "drifted-2_persona.json").read_bytes()).hexdigest()
        assert before == after

    def test_drift_check_writes_nothing_to_persona_dir(self, dq, tmp_path):
        persona_dir = tmp_path / "personas"
        persona_dir.mkdir()
        self._make_persona(dq, persona_dir, "drifted-3",
                           perturb={"openness": 0.05, "risk_tolerance": 0.05})
        files_before = sorted(p.name for p in persona_dir.iterdir())
        mon = dq.PersonaDriftMonitor(persona_dir=str(persona_dir))
        mon.drift("drifted-3")
        assert sorted(p.name for p in persona_dir.iterdir()) == files_before

    def test_missing_persona_raises(self, dq, tmp_path):
        persona_dir = tmp_path / "personas"
        persona_dir.mkdir()
        mon = dq.PersonaDriftMonitor(persona_dir=str(persona_dir))
        with pytest.raises(FileNotFoundError):
            mon.drift("ghost-agent")


# ---------------------------------------------------------------------------
# 3. Anomaly detector: true positives, no false positives, adversarial
# ---------------------------------------------------------------------------

class TestAnomalies:
    def test_spike_flags_true_positive(self, dq, watch):
        det = watch.anomalies
        for _ in range(15):
            det.ingest("agent-a", "token_usage", 1200.0)
        result = det.ingest("agent-a", "token_usage", 48000.0)
        assert result["status"] == "anomaly"
        assert result["is_anomaly"] is True
        assert result["z_flag"] or result["iqr_flag"]

    def test_normal_value_no_false_positive(self, dq, watch):
        det = watch.anomalies
        for _ in range(15):
            det.ingest("agent-a", "latency_ms", 900.0)
        result = det.ingest("agent-a", "latency_ms", 905.0)
        assert result["status"] == "normal"
        assert result["is_anomaly"] is False

    def test_insufficient_baseline_defers_judgment(self, dq, watch):
        result = watch.anomalies.ingest("agent-b", "error_rate", 0.99)
        assert result["status"] == "insufficient_baseline"
        assert result["is_anomaly"] is False

    def test_constant_baseline_no_crash(self, dq, watch):
        det = watch.anomalies
        for _ in range(10):
            det.ingest("agent-c", "quality_overall", 0.80)
        result = det.ingest("agent-c", "quality_overall", 0.80)  # std == 0
        assert result["is_anomaly"] is False  # z forced to 0 on zero variance

    def test_unknown_metric_rejected(self, dq, watch):
        with pytest.raises(ValueError):
            watch.anomalies.ingest("agent-d", "vibes", 1.0)

    def test_adversarial_slow_ramp_evades_detector(self, dq, watch):
        """Honest limitation: a slow ramp below thresholds does not flag.
        Documented as a known gap; gradual drift is the trend tracker's job."""
        det = watch.anomalies
        value = 1000.0
        flagged = False
        for _ in range(40):
            value *= 1.02  # +2% per sample: real degradation, never an outlier
            result = det.ingest("agent-e", "token_usage", value)
            flagged = flagged or result["is_anomaly"]
        assert flagged is False  # expected miss, recorded as a limitation


# ---------------------------------------------------------------------------
# 4. Report generation
# ---------------------------------------------------------------------------

class TestReports:
    def test_agent_report_json_and_markdown(self, dq, watch):
        persona_dir = Path(watch.drift_monitor.persona_dir)
        dq.PersonaEngine("report-agent", "Scout", persona_dir=str(persona_dir))
        for i in range(10):
            dims = nine_dims(dq)
            dims[dq.QualityDimension.CLARITY] = round(0.85 - i * 0.03, 3)
            watch.observe_deliverable(
                "report-agent", f"d{i}", dims, 0.85,
                behavioral={"token_usage": 1200.0, "latency_ms": 900.0,
                            "error_rate": 0.01, "quality_overall": 0.85},
            )
        report_json, report_md = watch.agent_report("report-agent")
        # JSON-serializable
        json.dumps(report_json)
        entry = report_json["agents"][0]
        assert entry["agent_id"] == "report-agent"
        assert any(a.startswith("degrading:clarity") for a in entry["quality"]["alerts"])
        assert entry["drift"]["status"] == "stable"
        # human-readable, positive framing (no "failure"/"broken" language about the agent)
        assert "report-agent" in report_md
        assert "Needs attention" in report_md
        assert "persona" in report_md.lower()

    def test_drift_alert_surfaces_advisory_in_report(self, dq, watch):
        persona_dir = Path(watch.drift_monitor.persona_dir)
        dq.PersonaEngine("drift-report-agent", "Scout", persona_dir=str(persona_dir))
        path = persona_dir / "drift-report-agent_persona.json"
        data = json.loads(path.read_text())
        data.update({"openness": 0.05, "risk_tolerance": 0.05,
                     "directness": 0.05, "extraversion": 0.05})
        path.write_text(json.dumps(data, indent=2))
        watch.observe_deliverable("drift-report-agent", "d0", nine_dims(dq), 0.8)
        report_json, report_md = watch.agent_report("drift-report-agent")
        entry = report_json["agents"][0]
        assert entry["drift"]["status"] == "drift_alert"
        assert entry["advisory"] is not None
        assert entry["advisory"]["mutated"] is False
        assert "advisory only" in report_md.lower()

    def test_fleet_report(self, dq, watch):
        watch.observe_deliverable("fleet-1", "d0", nine_dims(dq), 0.8)
        report_json, report_md = watch.fleet_report(["fleet-1", "fleet-2"])
        assert len(report_json["agents"]) == 2
        json.dumps(report_json)
        assert "fleet-1" in report_md and "fleet-2" in report_md


# ---------------------------------------------------------------------------
# 5. End-to-end walkthrough stays green
# ---------------------------------------------------------------------------

class TestWalkthrough:
    def test_run_walkthrough_all_green(self, dq):
        result = dq.run_walkthrough()
        assert result["ALL_GREEN"] is True
        assert result["drift_alert_fired"] is True
        assert result["stable_control_status"] == "stable"
        assert result["token_anomaly"]["is_anomaly"] is True
        assert result["persona_file_unmutated"] is True
        assert result["quality_trend_clarity"]["degrading"] is True
