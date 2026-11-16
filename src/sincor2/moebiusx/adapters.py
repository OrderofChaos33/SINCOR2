"""Thin, honest adapters over pre-existing in-repo components.

Each adapter delegates to the real in-tree implementation — no logic
is reimplemented and no existing module is modified:

  * :class:`ResilienceBreakerAdapter` -> ``resilience.auto_heal.CircuitBreaker``
  * :class:`SolverQuoteAdapter`        -> ``solver.quote_engine.SolverQuoter`` (PAPER only)
  * :class:`BankrollRiskAdapter`       -> ``bankroll.Bankroll`` (Polyclaw
    accounting — adjacent risk surface, NOT the MoebiusX session cage)
  * :func:`proof_wall_snapshot`        -> ``lane3.MOEBIUSX_V2/V3`` tallies
"""
from __future__ import annotations

from typing import Any, Callable, Dict


class ResilienceBreakerAdapter:
    """Adapts resilience.auto_heal.CircuitBreaker to the breaker protocol."""

    def __init__(self, name: str = "moebiusx-rpc", failure_threshold: int = 5,
                 cooldown_seconds: float = 60.0) -> None:
        from sincor2.resilience.auto_heal import CircuitBreaker
        self._breaker = CircuitBreaker(
            name, failure_threshold=failure_threshold,
            cooldown_seconds=cooldown_seconds)

    @property
    def underlying(self):
        return self._breaker

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return self._breaker.call(fn, *args, **kwargs)

    def reset(self) -> None:
        self._breaker.reset()


class SolverQuoteAdapter:
    """PAPER quoting via the in-repo solver. Quotes are unsigned and
    can never be submitted (SolverQuoter guarantees signed=False)."""

    def __init__(self, snapshot: Any) -> None:
        from sincor2.solver.quote_engine import SolverQuoter
        self._quoter = SolverQuoter(snapshot)

    def quote(self, token_in: str, token_out: str, amount_in: int,
              deadline: int, **kwargs: Any) -> Dict[str, Any]:
        return self._quoter.quote(token_in, token_out, amount_in,
                                  deadline, **kwargs)


class BankrollRiskAdapter:
    """Read-only risk view over an existing Bankroll instance."""

    def __init__(self, bankroll: Any) -> None:
        self._bankroll = bankroll

    def kill_switch_active(self) -> bool:
        return bool(self._bankroll.kill_switch_active())

    def can_open(self, size_usd: float) -> bool:
        return bool(self._bankroll.can_open(size_usd))

    def snapshot(self) -> Dict[str, Any]:
        return dict(self._bankroll.snapshot())


def proof_wall_snapshot() -> Dict[str, Any]:
    """The build-time MoebiusX tallies published by lane3 (read-only)."""
    from sincor2 import lane3
    return {
        "as_of": lane3.PROOF_WALL_AS_OF,
        "v2": dict(lane3.MOEBIUSX_V2),
        "v3": dict(lane3.MOEBIUSX_V3),
        "total_success": (lane3.MOEBIUSX_V2["successful_trades"]
                          + lane3.MOEBIUSX_V3["successful_trades"]),
    }
