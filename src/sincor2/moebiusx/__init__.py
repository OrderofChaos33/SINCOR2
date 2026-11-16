"""MoebiusX in-repo surface — modular interfaces + pure dry-run math.

WHAT THIS PACKAGE IS
--------------------
An additive, import-safe facade over the MoebiusX-related code that
actually lives IN this repository, plus pure reference implementations
of the trading math documented in the MoebiusX ops lineage (v3.1).

WHAT IT IS NOT
--------------
The live MoebiusX trading kits live OUTSIDE this repo in
``~/workspace/ops/moebiusx/`` (founder-gated production tooling: session
keys, signing, broadcasting). This package never imports them, never
holds keys, never performs network I/O, and never broadcasts.

In-repo surface wrapped here (all pre-existing, unmodified):
  * ``sincor2.solver.pool_math``    — pure Uniswap V2/V3 pool math.
  * ``sincor2.solver.quote_engine`` — PAPER-only solver quoter.
  * ``sincor2.resilience.auto_heal.CircuitBreaker`` — breaker state machine.
  * ``sincor2.bankroll.Bankroll``    — Polyclaw bankroll / kill switch
    (adjacent trading risk accounting; NOT the MoebiusX session cage).
  * ``sincor2.lane3``                — build-time MoebiusX proof-wall
    tallies (``MOEBIUSX_V2`` / ``MOEBIUSX_V3`` snapshots, read-only data).
  * On-chain (Solidity, not importable from Python — documented only):
    ``contracts/security/MoebiusXSessionAccount.sol`` (prototype),
    ``contracts/security/SincorSessionValidator.sol``,
    ``onchain/src/sincor/EpochSessionKeyValidator.sol``,
    ``onchain/src/hooks/MoebiusMEVHook.sol``.

Interface-only pieces (no in-tree live component exists):
  * ``StrategyExecutor`` / ``RpcProvider`` protocols — the live executor
    and RPC clients live in the external ops kits.
  * The v3.1 sizing law in :mod:`.math_ref` — a pure reference
    implementation of documented constants; no in-repo Python module
    implements it (``bankroll.py`` uses different, Polyclaw-specific
    proportions: 25% max position, 3x leverage, 30% daily-loss stop).
"""
from sincor2.moebiusx.math_ref import (
    PER_CALL_CEILING_USD,
    SizingResult,
    size_trade_v31,
    slippage_bps,
    within_slippage,
)
from sincor2.moebiusx.breaker import ConsecutiveFailureBreaker
from sincor2.moebiusx.dryrun import DryRunEngine, DryRunResult, PoolState
from sincor2.moebiusx.guards import GasGuard, SlippageGuard
from sincor2.moebiusx.interfaces import (
    CircuitBreakerProtocol,
    RpcProvider,
    StrategyExecutor,
    TradeIntent,
)
from sincor2.moebiusx.rpc_failover import FailoverRpcProvider

__all__ = [
    "PER_CALL_CEILING_USD",
    "SizingResult",
    "size_trade_v31",
    "slippage_bps",
    "within_slippage",
    "ConsecutiveFailureBreaker",
    "DryRunEngine",
    "DryRunResult",
    "PoolState",
    "GasGuard",
    "SlippageGuard",
    "CircuitBreakerProtocol",
    "RpcProvider",
    "StrategyExecutor",
    "TradeIntent",
    "FailoverRpcProvider",
]
