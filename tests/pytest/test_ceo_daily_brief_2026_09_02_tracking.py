from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = ROOT / "agents" / "tasks"


def _load(name: str) -> dict:
    return json.loads((TASKS_DIR / name).read_text(encoding="utf-8"))


def _task_prompt(cfg: dict, task_id: str) -> str:
    return next(task["prompt"] for task in cfg["tasks"] if task["id"] == task_id)


def test_builders_tracks_material_open_prs_and_fee_path() -> None:
    cfg = _load("builders.json")
    prompt = _task_prompt(cfg, "builders-ci-watch")

    assert "#116" in prompt and "#102" in prompt and "#81" in prompt
    assert "record_platform_fee_inflow" in prompt
    assert "AXM-only" in prompt
    assert "DRY_RUN" in prompt


def test_negotiators_tracks_conversion_p0_and_a2a_discovery_gate() -> None:
    cfg = _load("negotiators.json")

    seq_prompt = _task_prompt(cfg, "negotiators-paylink-seq")
    assert "paid pilot" in seq_prompt
    assert "Healthcare RCM" in seq_prompt
    assert "$49 intel" in seq_prompt

    a2a_prompt = _task_prompt(cfg, "negotiators-a2a-external")
    assert "treasury" in a2a_prompt
    assert "discovery 200 only after fee-path-live confirmation" in a2a_prompt


def test_toa_tracks_hard_eod_and_treasury_hold_constraints() -> None:
    cfg = _load("toa.json")

    daily_prompt = _task_prompt(cfg, "toa-daily-sync")
    assert "Hard EOD status" in daily_prompt
    assert "projected=false + tx_hash" in daily_prompt
    assert "locked paid pilot" in daily_prompt
    assert "external A2A settlement" in daily_prompt

    hb_prompt = _task_prompt(cfg, "toa-metrics-heartbeat")
    assert "Morpho/Aave/SharedLiquidity" in hb_prompt
    assert "before first realized conversion" in hb_prompt
