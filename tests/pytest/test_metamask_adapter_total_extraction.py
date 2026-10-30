"""Regression tests for the MetaMask adapter totality breaks (Builder B round).

The prior fix (test_metamask_adapter_orderresult_fix.py) made result
CONSTRUCTION total: OrderResult is only built with declared fields. Builder
B then broke the TOTALITY claim one level up, through the REAL _run() +
real subprocess + real JSON parsing, using a fake ``mm`` binary:

1. Non-dict top-level JSON after a successful CLI execution was reported as
   FAILURE. _run() returns raw ``json.loads(stdout)``, which can be ANY
   JSON value; transfer()/swap_execute() did ``data.get("data")`` — the
   isinstance check guarded ``data["data"]`` but never ``data`` itself.
   Exit 0 printing ``[]``, ``"ok"``, ``null``, ``42`` or ``true`` ->
   AttributeError -> swallowed by the broad except -> OrderResult
   success=False for a trade the CLI executed. A caller retrying on that
   "failure" can double-execute. send_to_treasury inherits it via
   transfer().
2. Non-string tx hash -> success with a wrong-typed order_id.
   ``{"data": {"txHash": 12345}}`` put an int in order_id (dict/list
   likewise); ``tx_hash or ""`` filters falsy values, not truthy wrong
   types, and downstream ``order_id.startswith(...)`` then crashes.
3. Unparseable stdout after exit 0 fell through _run()'s raw fallback with
   tx_hash None -> success=True, order_id="" — previously unqualified;
   pinned here as designed behavior.

Design decision pinned by these tests: the CLI's exit code is the
execution signal. After a 0 exit, NO exception and NO parse outcome may
convert the result into success=False — that conversion is what invites a
double-executing retry. Post-execution extraction is TOTAL (isinstance at
every level; the hash is coerced as ``tx_hash if isinstance(tx_hash, str)
else ""``). Hash-less success with an empty order_id is the already-pinned
design for the ``{}`` case and stands for all unparseable/hash-less
shapes. Nonzero exit / timeout semantics are unchanged.

The fake-binary harness mirrors the devwatch-315 sentinel pattern: a real
executable on disk, driven through the adapter's real subprocess runner —
no stubbing of _run() in this file.
"""

from __future__ import annotations

import pytest

from sincor2.adapters import metamask_agent_wallet as mmw
from sincor2.adapters.metamask_agent_wallet import MetaMaskAgentWallet, WalletStatus
from sincor2.execution_adapter import OrderResult

TX_HASH = "0x" + "cd" * 32
RECIPIENT = "0x" + "11" * 20

FAKE_MM = """\
#!/usr/bin/env python3
import os, sys, time

delay = float(os.environ.get("FAKE_MM_SLEEP", "0"))
if delay:
    time.sleep(delay)
sys.stdout.write(os.environ.get("FAKE_MM_STDOUT", ""))
sys.stdout.flush()
sys.stderr.write(os.environ.get("FAKE_MM_STDERR", ""))
sys.stderr.flush()
sys.exit(int(os.environ.get("FAKE_MM_EXIT", "0")))
"""


@pytest.fixture()
def fake_mm(tmp_path):
    path = tmp_path / "mm"
    path.write_text(FAKE_MM)
    path.chmod(0o755)
    return str(path)


@pytest.fixture()
def live_wallet(fake_mm, monkeypatch):
    """A live-mode wallet whose mm binary is the fake, ready-checked."""
    monkeypatch.setattr(mmw, "kill_switch_tripped", lambda *a, **k: False)
    monkeypatch.setenv("METAMASK_AGENT_LIVE", "true")
    w = MetaMaskAgentWallet(mm_bin=fake_mm)
    monkeypatch.setattr(
        w,
        "require_ready",
        lambda: WalletStatus(authenticated=True, initialized=True),
    )
    return w


def _call(wallet, method):
    if method == "transfer":
        return wallet.transfer(to=RECIPIENT, amount="1.5", force_live=True)
    if method == "swap_execute":
        return wallet.swap_execute("quote-123", force_live=True)
    if method == "send_to_treasury":
        return wallet.send_to_treasury("1.5")
    raise AssertionError(method)


ALL_METHODS = ["transfer", "swap_execute", "send_to_treasury"]
EXEC_METHODS = ["transfer", "swap_execute"]


# ----------------------------------------------------------------------
# Break 1: non-dict top-level JSON after exit 0 must still be success.
# The CLI executed; reporting failure invites a double-executing retry.
# ----------------------------------------------------------------------


@pytest.mark.parametrize("method", ALL_METHODS)
@pytest.mark.parametrize(
    "stdout", ["[]", '"ok"', "null", "42", "true"], ids=["list", "str", "null", "int", "bool"]
)
def test_nondict_top_level_json_reports_success(live_wallet, monkeypatch, method, stdout):
    monkeypatch.setenv("FAKE_MM_STDOUT", stdout)
    res = _call(live_wallet, method)
    assert isinstance(res, OrderResult)
    assert res.success is True
    assert res.simulated is False
    assert res.order_id == ""
    assert isinstance(res.order_id, str)
    assert res.error == ""


# ----------------------------------------------------------------------
# Break 2: non-string tx hash must not leak into order_id. Success stands
# (the CLI executed); order_id is "" and always a str.
# ----------------------------------------------------------------------


@pytest.mark.parametrize("method", ALL_METHODS)
@pytest.mark.parametrize(
    "bad_hash",
    ["12345", '{"nested": 1}', '["a", "b"]'],
    ids=["int", "dict", "list"],
)
def test_nonstring_tx_hash_coerced_to_empty_order_id(
    live_wallet, monkeypatch, method, bad_hash
):
    monkeypatch.setenv("FAKE_MM_STDOUT", '{"data": {"txHash": %s}}' % bad_hash)
    res = _call(live_wallet, method)
    assert res.success is True
    assert res.simulated is False
    assert res.order_id == ""
    assert isinstance(res.order_id, str)


@pytest.mark.parametrize("method", EXEC_METHODS)
def test_nonstring_hash_fallback_key_coerced(live_wallet, monkeypatch, method):
    monkeypatch.setenv("FAKE_MM_STDOUT", '{"data": {"hash": 777}}')
    res = _call(live_wallet, method)
    assert res.success is True
    assert res.order_id == ""
    assert isinstance(res.order_id, str)


# ----------------------------------------------------------------------
# Break 3 (pinned design): unparseable stdout after exit 0 -> hash-less
# success with empty order_id, exactly like the {} case.
# ----------------------------------------------------------------------


@pytest.mark.parametrize("method", ALL_METHODS)
def test_garbage_stdout_reports_hashless_success(live_wallet, monkeypatch, method):
    monkeypatch.setenv("FAKE_MM_STDOUT", "this is not json {{{")
    res = _call(live_wallet, method)
    assert res.success is True
    assert res.simulated is False
    assert res.order_id == ""
    assert isinstance(res.order_id, str)


# ----------------------------------------------------------------------
# Guards: the shapes that already worked must keep working through the
# real subprocess path, and failure semantics are unchanged.
# ----------------------------------------------------------------------


@pytest.mark.parametrize("method", ALL_METHODS)
def test_wellformed_tx_hash_lands_in_order_id(live_wallet, monkeypatch, method):
    monkeypatch.setenv("FAKE_MM_STDOUT", '{"data": {"txHash": "%s"}}' % TX_HASH)
    res = _call(live_wallet, method)
    assert res.success is True
    assert res.simulated is False
    assert res.order_id == TX_HASH


@pytest.mark.parametrize("method", EXEC_METHODS)
def test_wellformed_hash_fallback_key_lands(live_wallet, monkeypatch, method):
    monkeypatch.setenv("FAKE_MM_STDOUT", '{"data": {"hash": "%s"}}' % TX_HASH)
    res = _call(live_wallet, method)
    assert res.success is True
    assert res.order_id == TX_HASH


@pytest.mark.parametrize("method", ALL_METHODS)
def test_nonzero_exit_still_reports_failure(live_wallet, monkeypatch, method):
    monkeypatch.setenv("FAKE_MM_EXIT", "3")
    monkeypatch.setenv("FAKE_MM_STDERR", "boom: rejected by policy")
    res = _call(live_wallet, method)
    assert res.success is False
    assert res.simulated is False
    assert "rejected by policy" in res.error


def test_timeout_still_reports_failure(fake_mm, monkeypatch):
    monkeypatch.setattr(mmw, "kill_switch_tripped", lambda *a, **k: False)
    monkeypatch.setenv("FAKE_MM_SLEEP", "5")
    w = MetaMaskAgentWallet(mm_bin=fake_mm, timeout=0.3)
    monkeypatch.setattr(
        w,
        "require_ready",
        lambda: WalletStatus(authenticated=True, initialized=True),
    )
    res = w.transfer(to=RECIPIENT, amount="1.5", force_live=True)
    assert res.success is False
    assert "timed out" in res.error
