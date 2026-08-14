from __future__ import annotations

from scripts import defi_swarm_checkin_scheduler as scheduler_module


class _DummyTOA:
    def __init__(self):
        self.feedback = []

    def ingest_feedback(self, payload):
        self.feedback.append(payload)


def test_scheduler_reports_hook_metrics_to_toa(monkeypatch):
    monkeypatch.setattr(scheduler_module, "fetch_hook_status", lambda chain_id: {
        "chain_id": chain_id,
        "hook_address": "0x123",
        "router_address": "0x456",
        "sinc_in_hook_pm_m": 1.23,
        "curve_eth_accumulated": 4.56,
        "graduation_pct": 9.87,
    })
    monkeypatch.setattr(scheduler_module, "HOOK_METRICS_CHAIN_ID", 84532)

    scheduler = scheduler_module.DeFiSwarmScheduler()
    scheduler.toa = _DummyTOA()
    scheduler.router = None
    scheduler.yield_agg = None

    scheduler.check_in_all_swarms()

    hook_feedback = [f for f in scheduler.toa.feedback if f.get("source") == "shared_liquidity_hook"]
    assert hook_feedback
    assert hook_feedback[0]["chain_id"] == 84532
    assert hook_feedback[0]["hook_address"] == "0x123"
