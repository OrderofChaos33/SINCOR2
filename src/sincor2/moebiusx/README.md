# sincor2.moebiusx — in-repo MoebiusX surface

Additive package (A+ overhaul, Step 3). It changes no existing module;
every live import path that existed before still works unchanged.

## Honest map: implementation vs interface vs external

| Piece | Kind | Where the real thing lives |
|---|---|---|
| `dryrun.DryRunEngine` | **Implementation** (pure) | Simulates constant-product swaps via in-repo `sincor2.solver.pool_math.v2_amount_out`; verifies k-invariant in memory. No network, no disk, no broadcast. |
| `math_ref.size_trade_v31` | **Reference math** | Pure transcription of the documented v3.1 sizing law (min(pool·0.15, pool·0.5·conf), $40 ceiling, floor max($5, pool·0.12)). No in-repo module implemented this law — `bankroll.py` is Polyclaw accounting with different constants (25% / 3x). Live enforcement is in the external ops kits. |
| `guards.SlippageGuard` / `GasGuard` | **Reference math** | Pure boundary checks (default slippage cap 300 bps from the session-policy lineage). Live enforcement is in the ops kits + on-chain session caps. |
| `breaker.ConsecutiveFailureBreaker` | **Reference implementation** | Pure state machine for the ops-lineage rule: trip after 3 consecutive failures, never self-clear, operator `reset()` only. |
| `adapters.ResilienceBreakerAdapter` | **Adapter over in-repo code** | `sincor2.resilience.auto_heal.CircuitBreaker` (threshold + cooldown + half-open) — a real in-tree component. |
| `adapters.SolverQuoteAdapter` | **Adapter over in-repo code** | `sincor2.solver.quote_engine.SolverQuoter` — PAPER only, quotes are never signed. |
| `adapters.BankrollRiskAdapter` | **Adapter over in-repo code** | `sincor2.bankroll.Bankroll` kill switch / `can_open` / snapshot (Polyclaw risk accounting, adjacent to — not the same as — the MoebiusX session cage). |
| `adapters.proof_wall_snapshot` | **Adapter over in-repo data** | `sincor2.lane3` `MOEBIUSX_V2`/`MOEBIUSX_V3` build-time tallies. |
| `interfaces.StrategyExecutor` | **Interface only** | Live executor is EXTERNAL: `~/workspace/ops/moebiusx/` founder-gated kits. Nothing in this repo submits trades. |
| `interfaces.RpcProvider` + `rpc_failover.FailoverRpcProvider` | **Interface + pure policy** | Failover ordering/breaker policy over injected providers; no endpoints, HTTP, or keys in-repo. |
| Session validation (on-chain) | **External to Python** | Solidity only: `contracts/security/MoebiusXSessionAccount.sol` (prototype: cumulative + per-call caps, allowlist, revocation), `contracts/security/SincorSessionValidator.sol` (ERC-7579, per-call cap only), `onchain/src/sincor/EpochSessionKeyValidator.sol` (epoch roots), `onchain/src/hooks/MoebiusMEVHook.sol` (MEV tax hook). Documented here, not wrapped — Python must not pretend to validate sessions. |

## State / telemetry hygiene

This package writes nothing. Dry-run state lives in caller-owned
`PoolState` values in memory; tests use pytest `tmp_path` for the one
adapter that needs a file (Bankroll's SQLite DB).

## Tests

`tests/pytest/test_moebiusx_dryrun.py` — deterministic, offline:
constant-product invariant across simulated swap sequences, slippage
accept/reject boundaries, breaker trip/reset state machine (both the
reference breaker and the resilience adapter), RPC failover ordering
with in-memory fakes, v3.1 sizing caps/floor boundaries, and adapter
smoke over lane3 / bankroll / solver quoting.
