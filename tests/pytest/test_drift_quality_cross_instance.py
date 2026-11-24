"""W-211 — QualityTrendTracker cross-instance lost updates (hunt 64).

Two instances constructed before either ingests hold stale snapshots;
a stale instance's _save() rewrites the whole JSON file, erasing the
other instance's history (OBS-03 data loss). Mutators must
reload-mutate-save under flock, and reads must not serve a stale
snapshot.
"""
from __future__ import annotations

import json
from pathlib import Path

from sincor2.obs_skus.drift_quality import QualityTrendTracker


def _dims(value=0.85):
    return {"clarity": value, "accuracy": value}


def _overall_on_disk(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        agent: [p["deliverable_id"] for p in state["overall"]]
        for agent, state in raw["agents"].items()
    }


def test_two_preconstructed_instances_both_ingests_survive(tmp_path):
    path = tmp_path / "trends.json"
    a = QualityTrendTracker(store_path=path)
    b = QualityTrendTracker(store_path=path)  # stale: constructed before A ingests
    a.ingest("agent-a", "deliv-a1", _dims(0.90), 0.90)
    b.ingest("agent-b", "deliv-b1", _dims(0.80), 0.80)
    # Both survive on disk
    on_disk = _overall_on_disk(path)
    assert on_disk.get("agent-a") == ["deliv-a1"], f"a's record erased, disk={on_disk}"
    assert on_disk.get("agent-b") == ["deliv-b1"], f"b's record missing, disk={on_disk}"
    # And in a fresh third instance
    fresh = QualityTrendTracker(store_path=path)
    assert fresh.dimension_trend("agent-a", "clarity")["n"] == 1
    assert fresh.dimension_trend("agent-b", "clarity")["n"] == 1
    assert fresh.agent_overview("agent-a")["overall"]["n"] == 1
    assert fresh.agent_overview("agent-b")["overall"]["n"] == 1


def test_same_agent_two_instances_histories_merge(tmp_path):
    path = tmp_path / "trends.json"
    a = QualityTrendTracker(store_path=path)
    b = QualityTrendTracker(store_path=path)
    a.ingest("agent-x", "deliv-1", _dims(0.90), 0.90)
    b.ingest("agent-x", "deliv-2", _dims(0.70), 0.70)
    on_disk = _overall_on_disk(path)
    assert on_disk.get("agent-x") == ["deliv-1", "deliv-2"], f"lost update, disk={on_disk}"
    fresh = QualityTrendTracker(store_path=path)
    assert fresh.dimension_trend("agent-x", "clarity")["n"] == 2


def test_stale_instance_reads_reflect_other_instances_writes(tmp_path):
    path = tmp_path / "trends.json"
    a = QualityTrendTracker(store_path=path)
    b = QualityTrendTracker(store_path=path)  # stale
    a.ingest("agent-a", "deliv-a1", _dims(0.90), 0.90)
    # b's reads must reflect a's writes
    assert b.dimension_trend("agent-a", "clarity")["n"] == 1
    overview = b.agent_overview("agent-a")
    assert overview["overall"] is not None and overview["overall"]["n"] == 1
