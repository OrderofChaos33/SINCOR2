"""Production tests for realized treasury inflow path — 2026-08-07 CEO directive."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest


def test_record_realized_inflow_requires_tx_hash(monkeypatch, tmp_path):
    # Point ledger at temp so we never touch real data
    ledger = tmp_path / "treasury_inflow.jsonl"
    monkeypatch.setenv("TREASURY_INFLOW_LEDGER", str(ledger))

    # Re-import under env
    import importlib
    import src.sincor2.treasury_inflow as ti
    importlib.reload(ti)

    with pytest.raises(ValueError, match="tx_hash"):
        ti.record_realized_inflow(10.0, asset="SINC", source="test", tx_hash="")

    with pytest.raises(ValueError, match="invalid tx_hash"):
        ti.record_realized_inflow(10.0, asset="SINC", source="test", tx_hash="0xabc")

    with pytest.raises(ValueError, match="positive"):
        ti.record_realized_inflow(0.0, asset="SINC", source="test", tx_hash="0x" + "a" * 64)

    good = "0x" + "ab" * 32
    ev = ti.record_realized_inflow(
        12.5,
        asset="SINC",
        source="x402",
        tx_hash=good,
        usd_estimate=18.75,
        note="unit",
    )
    assert ev.projected is False
    assert ev.tx_hash == good.lower()
    assert ev.amount == 12.5
    assert ledger.exists()
    line = ledger.read_text().strip()
    data = json.loads(line)
    assert data["projected"] is False
    assert data["tx_hash"] == good.lower()
    assert data["source"] == "x402"


def test_realized_24h_usd_ignores_projected(monkeypatch, tmp_path):
    ledger = tmp_path / "treasury_inflow.jsonl"
    monkeypatch.setenv("TREASURY_INFLOW_LEDGER", str(ledger))

    import importlib
    import src.sincor2.treasury_inflow as ti
    importlib.reload(ti)

    ti.record_inflow(100.0, asset="USD", source="defi_swarms", projected=True, usd_estimate=100.0)
    good = "0x" + "cd" * 32
    ti.record_realized_inflow(40.0, asset="SINC", source="x402", tx_hash=good, usd_estimate=40.0)

    assert ti.realized_24h_usd() == 40.0
