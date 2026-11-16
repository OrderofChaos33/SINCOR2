"""RPC failover POLICY — pure, over injected providers.

INTERFACE-ONLY for live use: this module contains no endpoints, no
HTTP, and no keys. It implements the ordering policy — try providers
in order, skip ones whose breaker is open, record failures/successes
on each provider's breaker — over any objects satisfying the
:class:`~.interfaces.RpcProvider` protocol. Tests inject in-memory
fakes; production wiring (real endpoints) lives in the ops kits.
"""
from __future__ import annotations

from typing import Any, List, Sequence

from sincor2.moebiusx.breaker import BreakerOpenError, ConsecutiveFailureBreaker
from sincor2.moebiusx.interfaces import RpcProvider


class AllProvidersDownError(RuntimeError):
    """Every provider failed or was circuit-open for one call."""


class FailoverRpcProvider:
    """Ordered failover across providers, each behind its own breaker."""

    def __init__(self, providers: Sequence[RpcProvider],
                 failure_threshold: int = 3) -> None:
        if not providers:
            raise ValueError("at least one provider is required")
        self._providers = list(providers)
        self._breakers = [
            ConsecutiveFailureBreaker(name=f"rpc:{p.name}",
                                      failure_threshold=failure_threshold)
            for p in self._providers
        ]

    @property
    def name(self) -> str:
        return "failover(" + ",".join(p.name for p in self._providers) + ")"

    @property
    def breakers(self) -> List[ConsecutiveFailureBreaker]:
        return list(self._breakers)

    def call(self, method: str, params: Any = None) -> Any:
        errors: List[str] = []
        for provider, breaker in zip(self._providers, self._breakers):
            if breaker.tripped:
                errors.append(f"{provider.name}: circuit open")
                continue
            try:
                result = breaker.call(provider.call, method, params)
                return result
            except BreakerOpenError:
                errors.append(f"{provider.name}: circuit open")
            except Exception as exc:  # provider failure -> next in order
                errors.append(f"{provider.name}: {exc}")
        raise AllProvidersDownError("; ".join(errors))
