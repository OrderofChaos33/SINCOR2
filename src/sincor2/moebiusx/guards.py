"""Slippage / gas guards — pure reference implementations.

No in-repo MoebiusX guard module exists; the live guards are enforced
inside the external ops kits (slippage <= 3%, per-call exec-fee cap in
the session policy) and on-chain by the session-account prototype's
per-call / cumulative caps. These classes implement the *checks* as
pure functions of caller-supplied numbers so the boundaries are
unit-testable without any chain access.
"""
from __future__ import annotations

from sincor2.moebiusx.math_ref import within_slippage

DEFAULT_MAX_SLIPPAGE_BPS = 300.0  # 3% — v3.1 session-policy lineage


class SlippageGuard:
    def __init__(self, max_slippage_bps: float = DEFAULT_MAX_SLIPPAGE_BPS):
        if max_slippage_bps < 0:
            raise ValueError("max_slippage_bps must be >= 0")
        self.max_slippage_bps = float(max_slippage_bps)

    def check(self, expected_out: float, actual_out: float) -> bool:
        return within_slippage(expected_out, actual_out,
                               self.max_slippage_bps)

    def min_amount_out(self, expected_out: float) -> float:
        """Smallest acceptable output under this guard."""
        return expected_out * (1 - self.max_slippage_bps / 10_000)


class GasGuard:
    """Reject executions whose estimated gas cost exceeds a cap.

    Pure: the caller supplies the estimate (wei) and price; nothing
    here queries a chain.
    """

    def __init__(self, max_cost_wei: int):
        if max_cost_wei < 0:
            raise ValueError("max_cost_wei must be >= 0")
        self.max_cost_wei = int(max_cost_wei)

    def check(self, gas_units: int, gas_price_wei: int) -> bool:
        if gas_units < 0 or gas_price_wei < 0:
            raise ValueError("gas inputs must be >= 0")
        return gas_units * gas_price_wei <= self.max_cost_wei
