"""Unit tests for treasury_inflow — no network required."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# Ensure package import works when tests run from repo root
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def isolated_ledger(tmp_path, monkeypatch):
    ledger = tmp_path / "treasury_inflow.jsonl"
    monkeypatch.setenv("TREASURY_INFLOW_LEDGER", str(ledger))
    # Re-import module so path is picked up
    import importlib
    import src.sincor2.treasury_inflow as ti

    importlib.reload(ti)
    yield ti, ledger
    importlib.reload(ti)


def test_record_inflow_writes_ledger(isolated_ledger):
    ti, ledger = isolated_ledger
    ev = ti.record_inflow(42.5, asset="USDC", source="unit_test", usd_estimate=42.5)
    assert ev.amount == 42.5
    assert ev.asset == "USDC"
    assert ledger.exists()
    lines = ledger.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    raw = json.loads(lines[0])
    assert raw["amount"] == 42.5
    assert raw["source"] == "unit_test"
    assert raw["projected"] is False


def test_record_inflow_rejects_negative(isolated_ledger):
    ti, _ = isolated_ledger
    with pytest.raises(ValueError):
        ti.record_inflow(-1.0, source="bad")


def test_ledger_summary_24h(isolated_ledger):
    ti, _ = isolated_ledger
    ti.record_inflow(10, asset="USD", source="a", usd_estimate=10)
    ti.record_inflow(5, asset="USDC", source="b", usd_estimate=5, projected=True)
    summary = ti.ledger_summary_24h()
    assert summary["events_24h"] == 2
    assert summary["usd_total_24h"] == 15.0
    assert summary["usd_projected_24h"] == 5.0
    assert summary["usd_realized_24h"] == 10.0
    assert "USD" in summary["by_asset"]


def test_snapshot_without_rpc(isolated_ledger, monkeypatch):
    ti, _ = isolated_ledger
    ti.record_inflow(1.0, source="snap_test", usd_estimate=1.0)
    # Force include_onchain=False so no network
    snap = ti.get_treasury_snapshot(include_onchain=False)
    assert snap.treasury_address.startswith("0x")
    assert snap.ledger_events_24h >= 1
    assert snap.rpc_ok is False
    assert snap.rpc_detail == "skipped"
    d = snap.to_dict()
    assert "ledger_24h_usd" in d
