#!/usr/bin/env python3
"""
Tests for the Yield Optimizer vertical agent (Auditor gate + vault bridge).

Run:  python -m pytest verticals/trading/test_yield_optimizer.py -q
"""

from __future__ import annotations

from verticals.trading.yield_optimizer_agent import (
    BASE_USDC,
    YieldOptimizerAgent,
    default_auditor,
    AuditorRejection,
)

LP = "0xdba7180cdd90D12B9Bc2F15080ddFD9B14fEf31a"

LANE = {
    "strategy_id": 0,
    "lp": LP,
    "token": BASE_USDC,
    "virtual_alloc": 10_000.0,
    "outstanding": 2_000.0,
    "strategy_outstanding": 5_000.0,
    "cap": 20_000.0,
    "real_balance": 50_000.0,
    "lp_outstanding_total": 6_000.0,
}

GOOD_MARKET = {"id": "m1", "probability": 0.50, "liquidity": 250_000}


def agent() -> YieldOptimizerAgent:
    return YieldOptimizerAgent()


# ----------------------------------------------------------------------
# propose_drawdown (Auditor-gated)
# ----------------------------------------------------------------------
def test_propose_drawdown_approved_with_calldata():
    out = agent().run({
        "task_type": "propose_drawdown",
        "payload": {
            "lane": LANE,
            "market": GOOD_MARKET,
            "model_probability": 0.62,
            "bankroll": 25_000.0,
        },
    })
    assert out["status"] == "success"
    opp = out["result"]["opportunity"]
    assert opp is not None
    assert opp["auditor"] == "approved"
    assert opp["drawdown_amount"] <= 8_000.0  # availableDraw for this lane
    assert opp["drawdown_calldata"].startswith("0x0ca63fb1")


def test_propose_drawdown_rejected_below_edge_bar():
    out = agent().run({
        "task_type": "propose_drawdown",
        "payload": {
            "lane": LANE,
            "market": GOOD_MARKET,
            "model_probability": 0.51,  # 1% edge < 2.5% bar
            "bankroll": 25_000.0,
        },
    })
    assert out["result"]["opportunity"] is None


def test_auditor_rejects_non_allowlisted_token():
    bad_lane = dict(LANE, token="0x000000000000000000000000000000000000dead")
    out = agent().run({
        "task_type": "propose_drawdown",
        "payload": {
            "lane": bad_lane,
            "market": GOOD_MARKET,
            "model_probability": 0.62,
            "bankroll": 25_000.0,
        },
    })
    assert out["result"]["opportunity"] is None
    assert "auditor_rejected" in out["result"]["reason"]


def test_custom_auditor_is_used():
    calls = []

    def strict_auditor(opp, state):
        calls.append(opp.kind)
        raise AuditorRejection("vetoed by test")

    out = YieldOptimizerAgent(auditor=strict_auditor).run({
        "task_type": "propose_drawdown",
        "payload": {
            "lane": LANE,
            "market": GOOD_MARKET,
            "model_probability": 0.62,
            "bankroll": 25_000.0,
        },
    })
    assert calls == ["polyclaw_position"]
    assert out["result"]["opportunity"] is None
    assert "vetoed" in out["result"]["reason"]


# ----------------------------------------------------------------------
# settle_position feeds calibration + observer
# ----------------------------------------------------------------------
def test_settle_position_updates_calibration_and_observer():
    a = agent()
    ema_before = a.core.win_rate_ema
    out = a.run({
        "task_type": "settle_position",
        "payload": {
            "lane": LANE, "principal": 2_960.0, "fee": 266.4,
            "was_correct": True,
        },
    })
    r = out["result"]
    assert r["settlement"]["settle_calldata"].startswith("0xd77225c8")
    assert r["win_rate_ema"] > ema_before
    assert r["observer"]["recent_win_rate"] == 1.0


# ----------------------------------------------------------------------
# vault_status + loop_scan
# ----------------------------------------------------------------------
def test_vault_status_reports_available_draw():
    out = agent().run({"task_type": "vault_status", "payload": {"lane": LANE}})
    r = out["result"]
    assert r["available_draw"] == 8_000.0
    assert r["agent"]["vault_connected"] is True


def test_loop_scan_ranks_auditor_approved_loops():
    lanes = [
        dict(LANE, strategy_id=0, lending_loop_apr=0.04),   # spread 2% < bar
        dict(LANE, strategy_id=1, lending_loop_apr=0.09),   # spread 7% > bar
        dict(LANE, strategy_id=2, lending_loop_apr=0.12),   # spread 10% > bar
    ]
    out = agent().run({
        "task_type": "loop_scan",
        "payload": {"lanes": lanes, "funding_cost_apr": 0.02},
    })
    loops = out["result"]["loops"]
    assert out["result"]["count"] == 2
    assert loops[0]["edge"] > loops[1]["edge"]  # ranked by EV desc


def test_unknown_task_type_errors_cleanly():
    out = agent().run({"task_type": "nope", "payload": {}})
    assert out["status"] == "error"


def test_agent_card_exposes_capabilities():
    card = agent().get_agent_card()
    assert card["name"] == "yield_optimizer_agent"
    assert "drawdown_proposal" in card["capabilities"]
    assert card["metadata"]["a2a_compliant"] is True
