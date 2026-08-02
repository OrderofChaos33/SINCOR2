import builtins
import importlib
import sys


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
