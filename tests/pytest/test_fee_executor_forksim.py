"""Tests for the fee-executor fork-sim harness.

Hermetic: every test runs against stub clients / stub executors with canned
data. No network, no Anvil, no keys. The one live test is skipped unless
SINCOR_FORK_LIVE=1.
"""

import importlib.util
import json
import os
import sys

import pytest
from web3 import Web3

_HARNESS_PATH = os.path.join(os.path.dirname(__file__), "..", "..",
                             "fork_sim_fee_executor",
                             "run_fee_executor_forksim.py")


def _load_harness():
    spec = importlib.util.spec_from_file_location("fee_executor_forksim",
                                                  _HARNESS_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fee_executor_forksim"] = mod
    spec.loader.exec_module(mod)
    return mod


harness = _load_harness()

AXM = "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
NON_CHECKSUMMED = "0x0d5e0f971ed27fbff6c2837bf31316121532048d"  # V4_QUOTER


class StubClient:
    """Fake ForkClient: canned infra + block."""

    pinned_block = 51983038
    rpc_url = "stub"

    def __init__(self, infra_ok=True):
        self._infra_ok = infra_ok

    def has_code(self, address):
        return self._infra_ok


class StubExecutor:
    """Fake FeeConversionExecutor with canned discover_pool behavior."""

    def __init__(self, config, pools=()):
        self.config = config
        self._pools = dict(pools)

    def discover_pool(self, token_in, token_out, probe_wei):
        key = (token_in, token_out)
        if key in self._pools:
            return self._pools[key]
        raise harness.ExecutorError(
            f"no live V4 pool found for {token_in}->{token_out}")


def _pool():
    return harness.PoolKey.for_pair(AXM, USDC, 3000, 60)


# --- research ---------------------------------------------------------------

def test_research_pools_infra_missing(monkeypatch):
    monkeypatch.setattr(harness, "FeeConversionExecutor", StubExecutor)
    infra, findings = harness.research_pools(StubClient(infra_ok=False))
    assert infra == {"V4_QUOTER": False, "UNIVERSAL_ROUTER_V4": False,
                     "PERMIT2": False, "POOL_MANAGER": False}
    assert len(findings) == 2
    assert all(not f.found for f in findings)
    assert all("infra missing code" in f.detail for f in findings)


def test_research_pools_no_pool_found(monkeypatch):
    monkeypatch.setattr(harness, "FeeConversionExecutor", StubExecutor)
    infra, findings = harness.research_pools(StubClient(infra_ok=True))
    assert all(infra.values())
    assert [(f.route, f.found) for f in findings] == [
        ("AXM/USDC", False), ("AXM/WETH", False)]
    assert all("no live V4 pool" in f.detail for f in findings)


def test_research_pools_pool_found(monkeypatch):
    pool = _pool()

    class Found(StubExecutor):
        def __init__(self, config):
            super().__init__(config, pools={("AXM", "USDC"): pool})

    monkeypatch.setattr(harness, "FeeConversionExecutor", Found)
    _, findings = harness.research_pools(StubClient(infra_ok=True))
    by_route = {f.route: f for f in findings}
    assert by_route["AXM/USDC"].found is True
    assert by_route["AXM/USDC"].pool.fee == 3000
    assert by_route["AXM/WETH"].found is False


# --- pin file ----------------------------------------------------------------

def test_load_pin_file(tmp_path):
    pin = {"AXM/USDC": {"currency0": AXM.lower(), "currency1": USDC.lower(),
                        "fee": 3000, "tick_spacing": 60,
                        "hooks": "0x0000000000000000000000000000000000000000"}}
    path = tmp_path / "pins.json"
    path.write_text(json.dumps(pin))
    pins = harness.load_pin_file(str(path))
    key = pins[("AXM", "USDC")]
    assert key.fee == 3000 and key.tick_spacing == 60
    assert key.currency0 < key.currency1


# --- dry run -----------------------------------------------------------------

class _PlanStubExecutor(harness.FeeConversionExecutor):
    """Real executor class with RPC-bearing methods stubbed."""

    def __init__(self, config, plan_result=None, dry_exc=None):
        super().__init__(config)
        self._plan_result = plan_result
        self._dry_exc = dry_exc

    def plan(self, obligation_id):
        if isinstance(self._plan_result, Exception):
            raise self._plan_result
        return self._plan_result

    def execute_plan(self, plan, sign_and_broadcast, wait_for_receipt,
                     dry_run=False):
        assert dry_run is True, "harness must only dry-run"
        # the must_not_broadcast hook must never be invoked
        if self._dry_exc is not None:
            raise self._dry_exc
        return {"obligation_id": plan["obligation_id"], "mode": "dry_run",
                "status": "quoted"}


def _dry_config(tmp_path, pool=None):
    pins = {("AXM", "USDC"): pool} if pool else {}
    return harness.FeeConversionConfig(
        rpc_url="https://example.invalid",
        forwarder=harness.FORK_FORWARDER,
        ledger_path=str(tmp_path / "ledger.json"),
        pool_keys=pins)


def _plan_dict():
    return {"obligation_id": "ob-1",
            "quote": {"token_in": "AXM", "token_out": "USDC",
                      "amount_in_wei": 10**15, "amount_out_wei": 900_000,
                      "amount_out_min_wei": 891_000, "gas_estimate": 200_000,
                      "slippage_bps": 100},
            "txs": [{"kind": "approve_permit2", "unsigned": {"to": USDC}},
                    {"kind": "permit2_approve", "unsigned": {"to": USDC}},
                    {"kind": "swap", "unsigned": {"to": USDC}},
                    {"kind": "forward_to_treasury", "unsigned": None}],
            "armed": False}


def test_dry_run_plan_missing_pool_key_recorded(tmp_path, monkeypatch):
    class NoPool(_PlanStubExecutor):
        def __init__(self, config):
            super().__init__(
                config,
                plan_result=harness.MissingPoolKey(
                    "no pinned PoolKey for AXM->USDC"))

    monkeypatch.setattr(harness, "FeeConversionExecutor", NoPool)
    res = harness.dry_run_plan(StubClient(), _pool(), "AXM", "USDC", 10**15)
    assert res.ok is False
    assert "plan failed" in res.detail
    assert "no pinned PoolKey" in res.detail


def test_plan_raises_missing_pool_key_real_executor(tmp_path):
    from sincor2.onchain.fee_conversion_executor import (
        FeeConversionExecutor, MissingPoolKey)

    cfg = harness.FeeConversionConfig(
        rpc_url="https://example.invalid",
        forwarder=harness.FORK_FORWARDER,
        ledger_path=str(tmp_path / "ledger.json"))
    ex = FeeConversionExecutor(cfg)
    ex.ledger.record("ob-x", "AXM", 10**15)
    with pytest.raises(MissingPoolKey):
        ex.plan("ob-x")


def test_dry_run_plan_ok(tmp_path, monkeypatch):
    captured = {}

    class Ok(_PlanStubExecutor):
        def __init__(self, config):
            super().__init__(config, plan_result=_plan_dict())

        def execute_plan(self, plan, sign_and_broadcast, wait_for_receipt,
                         dry_run=False):
            captured["hook"] = sign_and_broadcast
            return super().execute_plan(plan, sign_and_broadcast,
                                        wait_for_receipt, dry_run=dry_run)

    monkeypatch.setattr(harness, "FeeConversionExecutor", Ok)
    res = harness.dry_run_plan(StubClient(), _pool(), "AXM", "USDC", 10**15)
    assert res.ok is True
    assert res.steps[0]["kinds"] == ["approve_permit2", "permit2_approve",
                                     "swap", "forward_to_treasury"]
    assert res.steps[1] == {"step": "dry_run", "simulated": True,
                            "mode": "dry_run"}
    # the broadcast hook must raise if ever invoked
    with pytest.raises(AssertionError, match="must not broadcast"):
        captured["hook"]({}, "swap")


def test_dry_run_plan_revert_is_recorded_not_crash(tmp_path, monkeypatch):
    class Reverts(_PlanStubExecutor):
        def __init__(self, config):
            super().__init__(
                config, plan_result=_plan_dict(),
                dry_exc=harness.ExecutorError("simulation reverted for swap"))

    monkeypatch.setattr(harness, "FeeConversionExecutor", Reverts)
    res = harness.dry_run_plan(StubClient(), _pool(), "AXM", "USDC", 10**15)
    assert res.ok is False
    assert res.steps[-1]["simulated"] is False
    assert "reverted" in res.steps[-1]["revert"]


# --- report ------------------------------------------------------------------

def test_build_report_marks_disarmed_no_broadcasts():
    client = StubClient()
    report = harness.build_report(
        client,
        {"V4_QUOTER": True, "UNIVERSAL_ROUTER_V4": True,
         "PERMIT2": True, "POOL_MANAGER": True},
        [harness.PoolFinding("AXM/USDC", False, detail="no pool")],
        {}, [], [])
    assert report["armed"] is False
    assert report["broadcasts"] == 0
    assert report["keys_used"] == 0
    assert report["fork_block"] == 51983038


def test_main_no_pool_exit_code(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(harness, "ForkClient", lambda rpc_url="x": StubClient())
    monkeypatch.setattr(harness, "FeeConversionExecutor", StubExecutor)
    report_path = str(tmp_path / "report.json")
    code = harness.main(["--rpc", "stub", "--report", report_path])
    assert code == harness.EXIT_NO_POOL == 2
    report = json.loads(open(report_path).read())
    assert report["verdict"].startswith("NO_POOL")
    assert report["armed"] is False


# --- executor regression: checksum normalization -------------------------------

class _FakeEth:
    def __init__(self):
        self.calls = []

    def call(self, params, block):
        self.calls.append((params, block))
        return b"\x00" * 64


class _FakeW3:
    to_checksum_address = staticmethod(Web3.to_checksum_address)

    def __init__(self):
        self.eth = _FakeEth()


def test_eth_call_normalizes_non_checksummed_addresses(tmp_path):
    """Regression: web3.py v8 raises on non-EIP-55 addresses in call params;
    the executor must normalize V4_QUOTER/AXIOM_TOKEN-style addresses."""
    from sincor2.onchain.fee_conversion_executor import FeeConversionExecutor

    cfg = harness.FeeConversionConfig(
        rpc_url="https://example.invalid",
        forwarder=harness.FORK_FORWARDER,
        ledger_path=str(tmp_path / "ledger.json"))
    ex = FeeConversionExecutor(cfg)
    ex._w3 = _FakeW3()
    ex._eth_call(NON_CHECKSUMMED, b"\x01\x02",
                 from_addr="0x1111111111111111111111111111111111111111")
    params, block = ex._w3.eth.calls[0]
    assert params["to"] == Web3.to_checksum_address(NON_CHECKSUMMED)
    assert params["from"] == "0x1111111111111111111111111111111111111111"
    assert block == "latest"


@pytest.mark.skipif(os.environ.get("SINCOR_FORK_LIVE") != "1",
                    reason="live fork test; set SINCOR_FORK_LIVE=1 to run")
def test_live_research_runs_read_only():
    client = harness.ForkClient()
    infra, findings = harness.research_pools(client)
    assert client.pinned_block > 0
    assert set(infra) == {"V4_QUOTER", "UNIVERSAL_ROUTER_V4", "PERMIT2",
                          "POOL_MANAGER"}
