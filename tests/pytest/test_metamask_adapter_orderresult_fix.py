"""Regression tests for the MetaMask adapter OrderResult defect.

Defect (found by dev-watch 315, confirmed on pristine main): transfer() and
swap_execute() in ``sincor2.adapters.metamask_agent_wallet`` constructed
their results as ``OrderResult(..., side=..., raw=...)``, but
``sincor2.execution_adapter.OrderResult`` has no ``side`` or ``raw`` field.
Every call raised TypeError — in dry-run it propagated to the caller; in
live mode the mm CLI could execute a real transfer/swap and the TypeError
was then swallowed into a failure result (executed-but-reported-failed).

The invariant pinned here: result construction can NEVER throw after the
CLI has executed. A successful CLI execution is reported as success; a CLI
failure is reported as failure. No path raises TypeError.

Fix shape: nothing in the codebase reads ``.side`` or ``.raw`` off an
OrderResult (Polyclaw reads only ``.error`` / ``.order_id``), so the
adapter maps onto the existing fields instead of growing the dataclass:
tx hash -> ``order_id``; the operation name survives in the log lines.
"""

from __future__ import annotations

import pytest

from sincor2.adapters import metamask_agent_wallet as mmw
from sincor2.adapters.metamask_agent_wallet import MetaMaskAgentWallet, WalletStatus
from sincor2.execution_adapter import OrderResult

TX_HASH = "0x" + "ab" * 32


@pytest.fixture()
def wallet(monkeypatch):
    """A wallet with the kill switch off and live execution stubbed out."""
    monkeypatch.setattr(mmw, "kill_switch_tripped", lambda *a, **k: False)
    monkeypatch.delenv("METAMASK_AGENT_LIVE", raising=False)
    w = MetaMaskAgentWallet()
    monkeypatch.setattr(
        w,
        "require_ready",
        lambda: WalletStatus(authenticated=True, initialized=True),
    )
    return w


def _run_ok(tx_hash=TX_HASH):
    def _run(*args, **kwargs):
        return {"ok": True, "data": {"txHash": tx_hash}}

    return _run


def _run_boom(*args, **kwargs):
    raise RuntimeError("mm failed: insufficient funds")


# ----------------------------------------------------------------------
# Dry-run: previously raised TypeError to the caller
# ----------------------------------------------------------------------


def test_transfer_dry_run_returns_result(wallet):
    res = wallet.transfer(to="0x" + "11" * 20, amount="1.5", token="native")
    assert isinstance(res, OrderResult)
    assert res.success is True
    assert res.simulated is True
    assert res.error == ""


def test_swap_execute_dry_run_returns_result(wallet):
    res = wallet.swap_execute("quote-123")
    assert isinstance(res, OrderResult)
    assert res.success is True
    assert res.simulated is True
    assert res.error == ""


def test_send_to_treasury_dry_run_returns_result(wallet):
    res = wallet.send_to_treasury("2.0")
    assert isinstance(res, OrderResult)
    assert res.success is True
    assert res.simulated is True


# ----------------------------------------------------------------------
# Live mode: CLI executed -> result construction must be total
# ----------------------------------------------------------------------


def test_transfer_live_success_reports_tx_hash(wallet, monkeypatch):
    monkeypatch.setattr(wallet, "_run", _run_ok())
    res = wallet.transfer(to="0x" + "11" * 20, amount="1.5", force_live=True)
    assert res.success is True
    assert res.simulated is False
    assert res.order_id == TX_HASH
    assert res.error == ""


def test_transfer_live_success_without_tx_hash_still_reports_success(
    wallet, monkeypatch
):
    monkeypatch.setattr(wallet, "_run", lambda *a, **k: {"ok": True, "data": {}})
    res = wallet.transfer(to="0x" + "11" * 20, amount="1.5", force_live=True)
    assert res.success is True
    assert res.simulated is False
    assert res.order_id == ""


def test_transfer_live_cli_failure_reports_failure(wallet, monkeypatch):
    monkeypatch.setattr(wallet, "_run", _run_boom)
    res = wallet.transfer(to="0x" + "11" * 20, amount="1.5", force_live=True)
    assert res.success is False
    assert res.simulated is False
    assert "insufficient funds" in res.error


def test_swap_execute_live_success_reports_tx_hash(wallet, monkeypatch):
    monkeypatch.setattr(wallet, "_run", _run_ok())
    res = wallet.swap_execute("quote-123", force_live=True)
    assert res.success is True
    assert res.simulated is False
    assert res.order_id == TX_HASH
    assert res.error == ""


def test_swap_execute_live_cli_failure_reports_failure(wallet, monkeypatch):
    monkeypatch.setattr(wallet, "_run", _run_boom)
    res = wallet.swap_execute("quote-123", force_live=True)
    assert res.success is False
    assert res.simulated is False
    assert "insufficient funds" in res.error


# ----------------------------------------------------------------------
# Structural guard: the adapter must never again construct an OrderResult
# with a field the dataclass does not declare.
# ----------------------------------------------------------------------


def test_adapter_constructs_only_declared_orderresult_fields():
    import dataclasses
    import inspect

    declared = {f.name for f in dataclasses.fields(OrderResult)}
    src = inspect.getsource(mmw)
    import re

    for call in re.finditer(r"OrderResult\((.*?)\)", src, re.DOTALL):
        kwargs = set(re.findall(r"(\w+)\s*=", call.group(1)))
        assert kwargs <= declared, (
            f"OrderResult constructed with undeclared fields: {kwargs - declared}"
        )
