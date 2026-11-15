"""Explicit interfaces (typing.Protocol) for the MoebiusX surface.

These protocols describe the seams the live system uses. Only some
have in-repo implementations:

  * :class:`CircuitBreakerProtocol` — implemented in-tree by
    ``sincor2.resilience.auto_heal.CircuitBreaker`` (see adapters.py)
    and by the pure :class:`~.breaker.ConsecutiveFailureBreaker`.
  * :class:`SlippageGuardProtocol` — implemented by
    :class:`~.guards.SlippageGuard` (pure math).
  * :class:`StrategyExecutor` — INTERFACE ONLY. The live executor is
    the external ops kit (``~/workspace/ops/moebiusx/``); nothing in
    this repo can submit or broadcast a trade.
  * :class:`RpcProvider` — INTERFACE ONLY for live use. In-tree RPC
    reading exists in ``sincor2.solver.pool_reader`` /
    ``sincor2.onchain.*`` but is not a MoebiusX failover client; the
    pure :class:`~.rpc_failover.FailoverRpcProvider` implements the
    failover *policy* over injected callables so it can be tested
    deterministically with no network.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class TradeIntent:
    """A direction + size request, before any guard runs."""

    direction: str  # "buy" | "sell"
    amount_usdc: float
    pool_usdc: float
    confidence: float  # TOA confidence in [0, 1]


@dataclass(frozen=True)
class ExecutionResult:
    """Outcome of an execution attempt. ``simulated`` is True for
    every result any in-repo code can produce."""

    success: bool
    simulated: bool
    tx_hash: Optional[str] = None
    error: str = ""


@runtime_checkable
class StrategyExecutor(Protocol):
    """INTERFACE ONLY — live implementation is external (ops kits)."""

    def execute(self, intent: TradeIntent) -> ExecutionResult: ...


@runtime_checkable
class RpcProvider(Protocol):
    """A single JSON-RPC endpoint abstraction (interface only)."""

    name: str

    def call(self, method: str, params: Any = None) -> Any: ...


@runtime_checkable
class CircuitBreakerProtocol(Protocol):
    """Fail-fast breaker: call() raises when open; reset() is the
    operator override (mirrors resilience.auto_heal.CircuitBreaker)."""

    def call(self, fn: Any, *args: Any, **kwargs: Any) -> Any: ...

    def reset(self) -> None: ...


@runtime_checkable
class SlippageGuardProtocol(Protocol):
    def check(self, expected_out: float, actual_out: float) -> bool: ...
