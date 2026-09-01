"""Chain-aware hook stats — live SINC pointers, Sepolia env overrides."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sincor2.hook_stats import fetch_hook_status  # noqa: E402
from sincor2.onchain.constants import SINC_TOKEN  # noqa: E402


def test_fetch_hook_status_mainnet_uses_live_sinc():
    with patch("sincor2.hook_stats.fetch_stats", return_value={"official_floor_usd": 0.15}):
        status = fetch_hook_status(chain_id=8453)
    assert status["chain_id"] == 8453
    assert status["sinc_address"].lower() == SINC_TOKEN.lower()
    assert "basescan.org/token/" in status["basescan"]["sinc"]
    assert "sepolia" not in status["basescan"]["sinc"]


def test_fetch_hook_status_sepolia_env_override(monkeypatch):
    monkeypatch.setenv("BASE_SEPOLIA_SHARED_LIQUIDITY_HOOK", "0x1111111111111111111111111111111111111111")
    monkeypatch.setenv("BASE_SEPOLIA_SINC_SWAP_ROUTER", "0x2222222222222222222222222222222222222222")
    with patch("sincor2.hook_stats.fetch_stats", return_value={}):
        status = fetch_hook_status(chain_id=84532)
    assert status["chain_id"] == 84532
    assert status["hook_address"].lower() == "0x1111111111111111111111111111111111111111"
    assert status["router_address"].lower() == "0x2222222222222222222222222222222222222222"
    assert "sepolia.basescan.org" in status["basescan"]["hook"]
