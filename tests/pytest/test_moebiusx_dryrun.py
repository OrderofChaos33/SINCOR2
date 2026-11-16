"""Deterministic dry-run tests for sincor2.moebiusx — offline only.

No network, no keys, no broadcasting: every provider is an in-memory
fake, every pool is a value object, and the only file written is a
Bankroll SQLite DB under pytest's tmp_path.
"""
import pytest

from sincor2.moebiusx import (
    ConsecutiveFailureBreaker,
    DryRunEngine,
    FailoverRpcProvider,
    GasGuard,
    PoolState,
    SlippageGuard,
    size_trade_v31,
    slippage_bps,
    within_slippage,
)
from sincor2.moebiusx.breaker import BreakerOpenError, BreakerState
from sincor2.moebiusx.math_ref import PER_CALL_CEILING_USD, trade_floor
from sincor2.moebiusx.rpc_failover import AllProvidersDownError


# ---------------------------------------------------------------------------
# Constant-product invariant across simulated swaps
# ---------------------------------------------------------------------------

def test_single_swap_preserves_invariant():
    engine = DryRunEngine()
    state = PoolState(reserve_in=1_000_000, reserve_out=1_000_000)
    result = engine.simulate_swap(state, 10_000)
    assert result.amount_out == 9871  # canonical V2 vector (30bps fee)
    assert result.invariant_ok
    assert result.state_after.k >= state.k
    assert result.state_after.reserve_in == 1_010_000
    assert result.state_after.reserve_out == 1_000_000 - 9871


def test_swap_sequence_invariant_monotonic():
    engine = DryRunEngine()
    state = PoolState(reserve_in=5_000_000, reserve_out=2_500_000)
    results = engine.simulate_sequence(state, [1_000, 50_000, 250_000, 7_500])
    ks = [state.k] + [r.state_after.k for r in results]
    assert all(r.invariant_ok for r in results)
    assert ks == sorted(ks)  # k never decreases (fees stay in the pool)
    for r in results:
        assert 0 < r.amount_out < r.state_after.reserve_out + r.amount_out


def test_dryrun_rejects_nonpositive_and_never_drains():
    engine = DryRunEngine()
    state = PoolState(reserve_in=1_000, reserve_out=1_000)
    with pytest.raises(ValueError):
        engine.simulate_swap(state, 0)
    # constant-product exact-input swaps asymptote: even an enormous
    # input cannot take the last unit of the output reserve
    result = engine.simulate_swap(state, 10**12)
    assert 0 < result.amount_out < state.reserve_out
    assert result.invariant_ok


# ---------------------------------------------------------------------------
# Slippage guard boundaries
# ---------------------------------------------------------------------------

def test_slippage_bps_math():
    assert slippage_bps(10_000, 10_000) == 0
    assert slippage_bps(10_000, 9_700) == pytest.approx(300.0)
    assert slippage_bps(10_000, 10_100) == pytest.approx(-100.0)
    with pytest.raises(ValueError):
        slippage_bps(0, 0)


def test_slippage_guard_accept_reject_boundary():
    guard = SlippageGuard(max_slippage_bps=300)
    assert guard.check(10_000, 9_700) is True   # exactly at the cap: accept
    assert guard.check(10_000, 9_699) is False  # 1 unit past the cap: reject
    assert guard.check(10_000, 10_500) is True  # better than expected: accept
    assert guard.min_amount_out(10_000) == pytest.approx(9_700.0)
    assert within_slippage(10_000, 9_700, 300) is True
    assert within_slippage(10_000, 9_699, 300) is False


def test_gas_guard_boundary():
    guard = GasGuard(max_cost_wei=1_000_000)
    assert guard.check(100, 10_000) is True   # exactly at cap
    assert guard.check(101, 10_000) is False  # over cap
    assert guard.check(0, 10_000) is True


# ---------------------------------------------------------------------------
# v3.1 sizing law (documented ops-lineage constants — see math_ref docstring)
# ---------------------------------------------------------------------------

def test_sizing_caps_and_floor():
    # pool $100, conf 1.0 -> min(15, 50) = $15, floor max(5, 12) = $12
    r = size_trade_v31(100.0, 1.0)
    assert r.feasible and r.size_usdc == 15.00 and r.floor_usdc == 12.00
    # confidence scales: min(15, 50*0.5)=15 still; conf 0.2 -> min(15,10)=10 < floor 12 -> 0
    r = size_trade_v31(100.0, 0.2)
    assert not r.feasible and r.size_usdc == 0.0
    # per-call ceiling binds when the floor still fits under it:
    # pool $300 -> min(45, 150) capped to $40; floor max(5, 36) = $36
    r = size_trade_v31(300.0, 1.0)
    assert r.size_usdc == PER_CALL_CEILING_USD == 40.00
    # documented consequence of the law as written: once pool*0.12
    # exceeds the $40 ceiling (pool > ~$333), no trade is feasible
    r = size_trade_v31(10_000.0, 1.0)
    assert not r.feasible and r.size_usdc == 0.0 and r.floor_usdc == 1200.0
    # small pool: floor is the $5 minimum; 15% of $40 = $6 >= $5 feasible
    r = size_trade_v31(40.0, 1.0)
    assert r.floor_usdc == 5.00 and r.size_usdc == 6.00
    # pool $30: max size $4.50 < $5 floor -> infeasible (never raised to fit)
    r = size_trade_v31(30.0, 1.0)
    assert not r.feasible and r.size_usdc == 0.0
    assert trade_floor(0.0) == 5.00
    with pytest.raises(ValueError):
        size_trade_v31(100.0, 1.5)


# ---------------------------------------------------------------------------
# Circuit-breaker state machine (reference breaker)
# ---------------------------------------------------------------------------

def test_breaker_trips_after_consecutive_failures_and_never_self_clears():
    breaker = ConsecutiveFailureBreaker(failure_threshold=3)
    calls = {"n": 0}

    def fail():
        calls["n"] += 1
        raise RuntimeError("validation failed")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            breaker.call(fail)
        assert breaker.state is BreakerState.CLOSED
    with pytest.raises(RuntimeError):
        breaker.call(fail)
    assert breaker.tripped and breaker.state is BreakerState.OPEN
    # open breaker fails fast WITHOUT invoking the function, and stays open
    with pytest.raises(BreakerOpenError):
        breaker.call(fail)
    assert calls["n"] == 3 and breaker.tripped
    # only the operator reset closes it
    breaker.reset()
    assert breaker.state is BreakerState.CLOSED
    assert breaker.consecutive_failures == 0


def test_breaker_success_resets_consecutive_count():
    breaker = ConsecutiveFailureBreaker(failure_threshold=3)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert breaker.call(lambda: "ok") == "ok"
    assert breaker.consecutive_failures == 0
    for _ in range(2):
        with pytest.raises(RuntimeError):
            breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert not breaker.tripped  # 2 + success + 2 never reaches 3 in a row


def test_resilience_adapter_breaker_trips_and_resets():
    from sincor2.moebiusx.adapters import ResilienceBreakerAdapter
    from sincor2.resilience.auto_heal import CircuitOpenError

    adapter = ResilienceBreakerAdapter(failure_threshold=2,
                                       cooldown_seconds=3600.0)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            adapter.call(lambda: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(CircuitOpenError):
        adapter.call(lambda: "never runs")
    adapter.reset()
    assert adapter.call(lambda: "back") == "back"


# ---------------------------------------------------------------------------
# RPC failover policy (in-memory fakes — no network)
# ---------------------------------------------------------------------------

class _FakeProvider:
    def __init__(self, name, behavior):
        self.name = name
        self.behavior = behavior
        self.calls = 0

    def call(self, method, params=None):
        self.calls += 1
        if self.behavior == "fail":
            raise ConnectionError(f"{self.name} unreachable")
        return f"{self.name}:{method}"


def test_failover_uses_first_healthy_and_skips_failing():
    bad = _FakeProvider("primary", "fail")
    good = _FakeProvider("fallback", "ok")
    provider = FailoverRpcProvider([bad, good])
    assert provider.call("eth_blockNumber") == "fallback:eth_blockNumber"
    assert bad.calls == 1 and good.calls == 1


def test_failover_breaker_opens_and_all_down_raises():
    bad1 = _FakeProvider("a", "fail")
    bad2 = _FakeProvider("b", "fail")
    provider = FailoverRpcProvider([bad1, bad2], failure_threshold=2)
    for _ in range(2):
        with pytest.raises(AllProvidersDownError):
            provider.call("eth_chainId")
    # both breakers open now: no further provider calls are attempted
    calls_before = bad1.calls + bad2.calls
    with pytest.raises(AllProvidersDownError):
        provider.call("eth_chainId")
    assert bad1.calls + bad2.calls == calls_before


# ---------------------------------------------------------------------------
# Adapters over the real in-repo surface
# ---------------------------------------------------------------------------

def test_proof_wall_snapshot_adapter():
    from sincor2.moebiusx.adapters import proof_wall_snapshot
    snap = proof_wall_snapshot()
    assert snap["v2"]["successful_trades"] == 12
    assert snap["v3"]["successful_trades"] == 69
    assert snap["total_success"] == 81


def test_bankroll_adapter_uses_tmp_db(tmp_path):
    from sincor2.bankroll import Bankroll
    from sincor2.moebiusx.adapters import BankrollRiskAdapter

    adapter = BankrollRiskAdapter(Bankroll(db_path=tmp_path / "bankroll.db"))
    assert adapter.kill_switch_active() is False
    assert adapter.can_open(1.0) in (True, False)  # real Bankroll verdict
    assert "equity" in adapter.snapshot()


def test_solver_quote_adapter_is_paper_only():
    from test_solver_pathfinder import fixture_snapshot

    from sincor2.moebiusx.adapters import SolverQuoteAdapter
    from sincor2.solver.pools import USDC, WETH

    adapter = SolverQuoteAdapter(fixture_snapshot())
    import time
    now = int(time.time())
    quote = adapter.quote(WETH, USDC, 10**18, deadline=now + 600, now=now)
    assert quote["mode"] == "PAPER"
    assert quote["signed"] is False and quote["signature"] is None
