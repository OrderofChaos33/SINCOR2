"""Deterministic dry-run / simulation engine (no network, no broadcast).

Simulates exact-input swaps against a constant-product pool using the
in-repo solver math (``sincor2.solver.pool_math.v2_amount_out``) and
verifies the pool invariant after every simulated swap:

  * the constant product ``k = reserve_in * reserve_out`` never
    decreases (fees stay in the pool, so k grows or holds);
  * the simulated output never exceeds the pool's output reserve;
  * a swap that would breach the slippage guard is reported, not
    executed against any real pool — there is no real pool here.

State is held only in memory (a :class:`PoolState` value the caller
owns). Nothing is written to disk, so dry-runs cannot pollute the
repo root or any telemetry directory.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from sincor2.moebiusx.guards import SlippageGuard
from sincor2.moebiusx.math_ref import constant_product_k
from sincor2.solver import pool_math as pm


@dataclass(frozen=True)
class PoolState:
    reserve_in: int
    reserve_out: int
    fee_bps: int = 30

    @property
    def k(self) -> int:
        return constant_product_k(self.reserve_in, self.reserve_out)


@dataclass(frozen=True)
class DryRunResult:
    amount_in: int
    amount_out: int
    state_after: PoolState
    invariant_ok: bool
    slippage_accepted: bool


class DryRunEngine:
    """Simulates swaps against caller-supplied pool state."""

    def __init__(self, guard: SlippageGuard | None = None) -> None:
        self.guard = guard or SlippageGuard()

    def quote(self, state: PoolState, amount_in: int) -> int:
        return pm.v2_amount_out(amount_in, state.reserve_in,
                                state.reserve_out, state.fee_bps)

    def simulate_swap(self, state: PoolState, amount_in: int,
                      expected_out: int | None = None) -> DryRunResult:
        """Return the post-swap state + invariant verdict. Pure."""
        if amount_in <= 0:
            raise ValueError("amount_in must be > 0")
        amount_out = self.quote(state, amount_in)
        if amount_out >= state.reserve_out:
            raise pm.InsufficientLiquidity("swap would drain the pool")
        after = replace(state,
                        reserve_in=state.reserve_in + amount_in,
                        reserve_out=state.reserve_out - amount_out)
        invariant_ok = after.k >= state.k
        expected = amount_out if expected_out is None else expected_out
        accepted = self.guard.check(float(expected), float(amount_out))
        return DryRunResult(amount_in=amount_in, amount_out=amount_out,
                            state_after=after, invariant_ok=invariant_ok,
                            slippage_accepted=accepted)

    def simulate_sequence(self, state: PoolState,
                          amounts_in: list[int]) -> list[DryRunResult]:
        results: list[DryRunResult] = []
        current = state
        for amount in amounts_in:
            result = self.simulate_swap(current, amount)
            results.append(result)
            current = result.state_after
        return results
