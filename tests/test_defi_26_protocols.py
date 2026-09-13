"""Full-build tests for the 26 DeFi swarm protocols. No chain."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sincor2.defi.catalog import PROTOCOLS, assert_catalog_complete
from src.sincor2.defi.engine import DeFiProtocolOS, SUBMISSION_DIR


def test_catalog_is_exactly_26_unique():
    assert_catalog_complete()
    assert len(PROTOCOLS) == 26
    assert [p.swarm_id for p in PROTOCOLS] == list(range(1, 27))
    assert PROTOCOLS[-1].protocol_id == "P26_DEFI_OS"
    assert PROTOCOLS[0].protocol_id == "P01_YIELD_AGG"


def test_tick_all_returns_26_no_errors():
    osys = DeFiProtocolOS(capital_usd=312.93, risk_budget=0.30)
    ticks = osys.tick_all()
    assert len(ticks) == 26
    assert {t.swarm_id for t in ticks} == set(range(1, 27))
    assert all(t.status in {"completed", "gated"} for t in ticks)
    assert all(t.executed is False for t in ticks)
    assert all(t.mode == "dry_run" for t in ticks)


def test_protocol_1_uses_real_yield_aggregator():
    tick = DeFiProtocolOS(capital_usd=312.93, risk_budget=0.30).tick_one(1)
    assert tick.status == "completed"
    assert tick.action == "rebalance_plan"
    allocs = tick.artifacts.get("allocations") or []
    assert allocs
    ids = {a["strategy_id"] for a in allocs}
    assert "cash_reserve" in ids


def test_risk_budget_gates_high_risk_protocols():
    ticks = DeFiProtocolOS(capital_usd=312.93, risk_budget=0.30).tick_all()
    by_id = {t.swarm_id: t for t in ticks}
    assert by_id[6].status == "gated"
    assert by_id[10].status == "gated"
    assert by_id[20].status == "completed"
    assert by_id[21].status == "completed"
    assert by_id[26].status == "completed"


def test_high_capital_high_risk_completes_all_handlers():
    ticks = DeFiProtocolOS(capital_usd=10_000, risk_budget=1.0).tick_all()
    assert all(t.status == "completed" for t in ticks)
    assert all(t.action not in {"pending", "error"} for t in ticks)


def test_no_broadcast_even_if_someone_sets_env(monkeypatch):
    monkeypatch.setenv("EXECUTE_LIVE", "1")
    ticks = DeFiProtocolOS(capital_usd=10_000, risk_budget=1.0).tick_all()
    assert all(t.executed is False for t in ticks)


def test_protocol_26_ranks_the_other_25():
    ticks = DeFiProtocolOS(capital_usd=10_000, risk_budget=1.0).tick_all()
    meta = ticks[-1]
    assert meta.protocol_id == "P26_DEFI_OS"
    ranked = meta.artifacts["ranked"]
    assert len(ranked) == 25
    scores = [r["score"] for r in ranked]
    assert scores == sorted(scores, reverse=True)


def test_submit_writes_latest_json(tmp_path, monkeypatch):
    monkeypatch.setattr("src.sincor2.defi.engine.SUBMISSION_DIR", tmp_path)
    sub = DeFiProtocolOS(capital_usd=312.93, risk_budget=0.30).submit()
    latest = tmp_path / "latest.json"
    assert latest.exists()
    payload = json.loads(latest.read_text())
    assert payload["treasury"].startswith("0x09E2")
    assert payload["completed"] + payload["gated"] + payload["errors"] == 26
    assert len(payload["ticks"]) == 26
    assert sub.errors == 0


def test_fees_are_daily_not_invented_yearly():
    tick = DeFiProtocolOS(capital_usd=10_000, risk_budget=1.0).tick_one(22)
    assert tick.expected_fee_usd < 1.0
    assert tick.expected_fee_usd >= 0.0


def test_p10_never_enables_flash_loan():
    tick = DeFiProtocolOS(capital_usd=10_000, risk_budget=1.0).tick_one(10)
    assert tick.artifacts.get("flash_loan") is False
    assert any("flash" in w.lower() for w in tick.warnings)
