from __future__ import annotations

from src.sincor2 import hook_stats


def test_fetch_hook_status_defaults_to_mainnet(monkeypatch):
    monkeypatch.setattr(hook_stats, "fetch_stats", lambda: {"curve_eth_accumulated": 0.25, "official_floor_usd": 1.5})

    status = hook_stats.fetch_hook_status()

    assert status["chain_id"] == 8453
    assert status["hook_address"] == hook_stats.MAINNET["hook"]
    assert "basescan.org/address" in status["basescan"]["hook"]


def test_fetch_hook_status_sepolia_overrides(monkeypatch):
    monkeypatch.setattr(hook_stats, "fetch_stats", lambda: {"curve_eth_accumulated": 0.5, "official_floor_usd": 1.5})
    monkeypatch.setenv("BASE_SEPOLIA_SHARED_LIQUIDITY_HOOK", "0xabc")

    status = hook_stats.fetch_hook_status(chain_id=84532)

    assert status["chain_id"] == 84532
    assert status["hook_address"] == "0xabc"
    assert "sepolia.basescan.org/address" in status["basescan"]["hook"]
    assert status["graduation_pct"] == 100.0
