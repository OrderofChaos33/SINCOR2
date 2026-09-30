"""Tests for the fork-simulation harness (fork_sim/run_fork_sims.py).

Deterministic: every sim runs against a stub ForkClient with canned fork
data, so the full suite is green with no network. One live test is included
but skipped unless SINCOR_FORK_LIVE=1.
"""

import importlib.util
import os
import sys

import pytest

_HARNESS_PATH = os.path.join(os.path.dirname(__file__), "..", "..",
                             "fork_sim", "run_fork_sims.py")


def _load_harness():
    spec = importlib.util.spec_from_file_location("fork_sims", _HARNESS_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fork_sims"] = mod
    spec.loader.exec_module(mod)
    return mod


_harness = _load_harness()


class StubForkClient:
    """Canned fork data mirroring a real Base block."""

    pinned_block = 51970232
    pinned_ts = 1790729811
    rpc_url = "stub"

    def __init__(self):
        self.calls = 0

    def call(self, address, abi, fn, *args):
        self.calls += 1
        addr = address.lower()
        if fn == "convertToAssets":
            assert args[0] == 10**18
            return 1113271  # $1.113271/share in USDC 6dp
        if fn == "totalAssets":
            return 415344666_480000
        if fn == "totalSupply":
            return 373084744915641453467287338
        if fn == "balanceOf":
            if addr == _harness.AXM.lower():
                return 978745764 * 10**18
            if addr == _harness.USDC.lower():
                return 733
            if addr == _harness.SINC.lower():
                return 99731069 * 10**8
            return 0
        raise AssertionError(f"unexpected call {fn}")

    def total_supply(self, token):
        return self.call(token, None, "totalSupply")

    def balance_of(self, token, holder):
        return self.call(token, None, "balanceOf", holder)

    def get_logs(self, address, topic0, from_block, to_block):
        # Three synthetic USDC transfers in one block, two sharing a tx.
        def lg(i, tx, value):
            return {
                "blockNumber": self.pinned_block - 1,
                "blockHash": b"\xab" * 32,
                "transactionIndex": tx,
                "topics": [bytes.fromhex(topic0[2:]), b"\x00" * 32,
                           b"\x11" * 32],
                "data": value.to_bytes(32, "big"),
            }
        return [lg(0, 0, 5_000_000_000), lg(1, 0, 1_000_000_000),
                lg(2, 1, 2_000_000_000)]

    def get_block(self, n):
        return {"timestamp": self.pinned_ts - (self.pinned_block - n) * 2}

    def get_code(self, address):
        return b""


def test_all_seventeen_sims_registered():
    pids = sorted(fn._sim_pid for fn in _harness.SIMS)
    assert len(pids) == 17, pids
    assert len(set(pids)) == 17


def test_all_sims_pass_on_canned_fork_data():
    fc = StubForkClient()
    failures = []
    for fn in _harness.SIMS:
        try:
            detail = fn(fc)
            assert isinstance(detail, str) and detail, fn._sim_pid
        except Exception as exc:  # noqa: BLE001 - collect all
            failures.append((fn._sim_pid, f"{type(exc).__name__}: {exc}"))
    assert not failures, failures


def test_findings_cover_the_remaining_nine():
    found = sorted(pid for pid, _ in _harness.FINDINGS)
    assert found == ["P02_CLMM", "P06_PERPS", "P10_FLASH_ARB", "P11_DELTA_NEUTRAL",
                     "P13_AVS", "P14_PREDICTION", "P17_OPTIONS", "P19_CREDIT",
                     "P23_NFTFI"]
    for pid, reason in _harness.FINDINGS:
        assert reason, pid  # every finding names its blocker


def test_every_sim_maps_to_a_real_sku():
    from sincor2.defi.products import mint_sku
    for fn in _harness.SIMS:
        sku = mint_sku(fn._sim_pid)
        assert sku.startswith("SINCOR-DEFI-P"), (fn._sim_pid, sku)


def test_harness_is_read_only_by_construction():
    src = open(_HARNESS_PATH).read()
    for banned in ("send_transaction(", "sendTransaction(",
                   "sign_transaction(", "eth_account", "Web3.to_wei("):
        assert banned not in src, banned
    assert "no signing surface" in src


@pytest.mark.skipif(os.environ.get("SINCOR_FORK_LIVE") != "1",
                    reason="live Base RPC; run with SINCOR_FORK_LIVE=1")
def test_live_fork_end_to_end():
    fc = _harness.ForkClient()
    outcomes, _ = _harness.run_all()
    failed = [o for o in outcomes if not o.passed]
    assert not failed, [(o.product_id, o.error) for o in failed]
    assert len(outcomes) == 17
