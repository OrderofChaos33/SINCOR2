import builtins
import importlib
import sys

import pytest

from sincor2 import polyclaw_earning_scheduler as scheduler


@pytest.fixture()
def no_hub(monkeypatch):
    """AccountingHub unconfigured: the seam reports no value."""
    monkeypatch.setattr(scheduler, "_hub_available_capital", lambda: None)


def _router_size(context, utility=1.0):
    """The decision router's sizing formula at a given utility score."""
    return round(
        context["available_capital_usd"] * context["max_risk_pct"] * utility, 2
    )


def test_polyclaw_scheduler_import_survives_system_exit(monkeypatch):
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "integration.polyclaw_toa_decision_router":
            raise SystemExit("simulated missing integration")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    sys.modules.pop("sincor2.polyclaw_earning_scheduler", None)

    module = importlib.import_module("sincor2.polyclaw_earning_scheduler")
    result = module.run_scheduled_cycle()

    assert module.run_polyclaw_earning_cycle is None
    assert result["status"] == "skipped"
    assert result["reason"] == "decision_router_unavailable"


def test_no_capital_source_fails_closed_at_zero(monkeypatch, no_hub):
    """No hub, no POLYCLAW_CAPITAL env: capital and trade size are $0,
    never the old phantom $25,000 default."""
    monkeypatch.delenv("POLYCLAW_CAPITAL", raising=False)
    monkeypatch.delenv("POLYCLAW_MAX_TRADE_USD", raising=False)

    context = scheduler.get_market_context()

    assert context["available_capital_usd"] == 0.0
    assert context["max_trade_usd"] == 0.0
    assert _router_size(context) == 0.0


def test_hub_reporting_zero_fails_closed(monkeypatch):
    """AccountingHub configured but reporting 0 wins over the env var."""
    monkeypatch.setattr(scheduler, "_hub_available_capital", lambda: 0.0)
    monkeypatch.setenv("POLYCLAW_CAPITAL", "25000")

    context = scheduler.get_market_context()

    assert context["available_capital_usd"] == 0.0
    assert context["max_trade_usd"] == 0.0
    assert _router_size(context) == 0.0


def test_env_capital_respects_risk_pct_and_default_hard_cap(monkeypatch, no_hub):
    """With env capital set, sizing respects max_risk_pct AND the hard cap:
    $25,000 * 8% = $2,000 risk size, capped at the $100 default ceiling."""
    monkeypatch.setenv("POLYCLAW_CAPITAL", "25000")
    monkeypatch.delenv("POLYCLAW_MAX_TRADE_USD", raising=False)

    context = scheduler.get_market_context()

    assert context["available_capital_usd"] == 25000.0
    assert context["configured_max_risk_pct"] == 0.08
    assert context["max_trade_usd"] == 100.0
    # Router formula at full utility can never exceed the hard cap.
    assert _router_size(context) <= 100.0
    assert scheduler.max_trade_size_usd(25000.0, 0.08) == 100.0


def test_hard_cap_env_override_lets_risk_pct_bind(monkeypatch, no_hub):
    """Raising POLYCLAW_MAX_TRADE_USD above the risk size restores the
    risk-based size: $25,000 * 8% = $2,000."""
    monkeypatch.setenv("POLYCLAW_CAPITAL", "25000")
    monkeypatch.setenv("POLYCLAW_MAX_TRADE_USD", "5000")

    context = scheduler.get_market_context()

    assert context["max_trade_usd"] == 2000.0
    assert context["max_risk_pct"] == 0.08
    assert _router_size(context) == 2000.0


def test_small_env_capital_sizes_below_hard_cap(monkeypatch, no_hub):
    """$1,000 capital * 8% = $80 — under the default hard cap, uncapped."""
    monkeypatch.setenv("POLYCLAW_CAPITAL", "1000")
    monkeypatch.delenv("POLYCLAW_MAX_TRADE_USD", raising=False)

    context = scheduler.get_market_context()

    assert context["available_capital_usd"] == 1000.0
    assert context["max_trade_usd"] == 80.0
    assert _router_size(context) == 80.0


def test_invalid_env_capital_fails_closed(monkeypatch, no_hub):
    monkeypatch.setenv("POLYCLAW_CAPITAL", "not-a-number")

    context = scheduler.get_market_context()

    assert context["available_capital_usd"] == 0.0
    assert context["max_trade_usd"] == 0.0
