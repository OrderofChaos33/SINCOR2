# Fee-Conversion Executor — Fork-Sim Readiness (Wave 53)

**Date:** 2026-09-30
**Branch:** `xioix/buildout-53-executor-forksim-prep` (base `fd96801`)
**Status:** Executor REPAIRED and validated on a Base fork (control route).
**AXM conversion: BLOCKED — no AXM/USDC or AXM/WETH pool exists. Honest verdict: `NO_POOL`.**

This document records everything before arming. Nothing here arms the executor,
uses keys, broadcasts, moves funds, or deploys. The locked fee policy is unchanged:
**5% platform fee, 100% converted to USDC/WETH before treasury, no burn.**

## Pinned addresses (Base)

| Role | Address |
|---|---|
| AXM | `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a` |
| SINC | `0xe1D836087F6573b665d25CE088793E916D7892f8` |
| USDC | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |
| WETH | `0x4200000000000000000000000000000000000006` |
| Treasury | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` |
| V4 PoolManager | `0x498581fF718922c3f8e6A244956aF099B2652b2b` |
| Universal Router | `0xfdf682f51fE81aA4898F0aE2163D8a55C127fbc7` |
| Permit2 | `0x000000000022D473030F116dDEE9F6B43aC78BA3` |

## Pool research (read-only, `https://mainnet.base.org`)

- At Base block `51983038`: code present at PoolManager, V4 Quoter, Universal
  Router, Permit2. Executor quoter returned a live quote on the control route
  WETH→USDC (fee `500`, spacing `10`): 1 WETH → 883,358,595 USDC-wei, gas 314,460.
- `discover_pool()` probed zero-hook V4 candidates `100/1`, `500/10`, `3000/60`,
  `10000/200` for AXM/USDC and AXM/WETH: **no pool found**.
- At Base block `51983256`: Uniswap V3 factory
  `0x33128a8fC17869897dcE68Ed026d694621f6FDfD` had no AXM/USDC or AXM/WETH pool
  at fees `500`, `3000`, `10000`. WETH/USDC control pools exist.
- CLI at block `51983359` returned `armed: false`, `broadcasts: 0`, `keys_used: 0`,
  verdict `NO_POOL`, **true process exit code 2** (re-verified 2026-09-30).

**Blocker (honest):** no AXM conversion replay can pass until an AXM/USDC or
AXM/WETH pool is created, seeded, verified, and its PoolKey pinned.

## Executor defects found and fixed (via fork probes)

### 1. EIP-55 checksum rejection
web3.py v8 rejects non-checksummed addresses before any RPC I/O. Fixed by
normalizing in `_eth_call`/`_ck()` and checksumming every tx `to`.

### 2. V4 swap params must be a single ABI-encoded struct
The executor encoded `ExactInputSingleParams` as five flat ABI args. The deployed
v4-periphery `CalldataDecoder.decodeSwapExactInSingleParams` follows the first
word as an offset to the struct; flat encoding reverted inside `unlockCallback`
before `PoolManager.swap` (proven by A/B Anvil traces). Fixed to encode
`((PoolKey), bool, uint128, uint128, uint256, bytes)` with `minHopPriceX36 = 0`.

### 3. Missing Permit2 internal allowance
ERC-20 `approve(Permit2)` alone is insufficient: V4 settlement calls
Permit2 `transferFrom`, which checks Permit2's own
`allowance(owner, token, spender)`. Added `permit2_allowance()`,
`build_permit2_approve_tx()` (`approve(address,address,uint160,uint48)`), and a
`permit2_approve` plan step between ERC-20 approval and swap.

### 4. SWEEP must not be used — TAKE_ALL pays the forwarder directly
The deployed V4Router's `TAKE_ALL` does `_take(currency, msgSender(), amount)`
where `msgSender()` is the original `execute()` caller (the forwarder). A fork
trace proved the take emitted `USDC.transfer(to=forwarder, 247288544)`. The
Universal Router therefore holds nothing, and a `SWEEP` command reverts with
`InsufficientBalance`. The executor now encodes commands `[V4_SWAP]` only.

## Control-route validation (Base fork, Anvil)

Full 4-step plan replayed via the harness's own `anvil_replay` on a fork of
Base block `51983899` (WETH→USDC `500/10`, 0.1 WETH):

1. `approve_permit2` → landed (allowance verified on fork)
2. `permit2_approve` → landed (Permit2 internal allowance verified on fork)
3. `swap` → succeeded, met `amountOutMinimum=244661038`
4. `forward_to_treasury` → `523803001` USDC-wei to treasury; forwarder holds 0

`ok: true`. No keys, no mainnet broadcast, executor disarmed throughout.

## Test status

- `tests/pytest/test_fee_executor_forksim.py` + `test_fee_conversion_executor.py`
  + `test_p21_treasury_units.py`: **51 passed, 1 skipped**.
- Live read-only gated suite (`SINCOR_FORK_LIVE=1`): **12 passed**.
- Audit: no `armed=True` in production/harness code (test-only mocks are
  explicitly scoped with fake RPC + stub signer); no key material; no live
  `send_transaction` path outside local Anvil.

## What remains (founder steps)

1. Create, seed, and verify an AXM/USDC or AXM/WETH pool.
2. Pin the exact V4 `PoolKey` (fee, spacing, hook).
3. Re-run read-only discovery and the local fork replay.
4. Custody the dedicated forwarder key in Secure Vault.
5. Conduct a founder-approved dust ceremony.
6. Only after all gates pass, separately authorize `armed=True`.

## Files

- Executor: `src/sincor2/onchain/fee_conversion_executor.py`
- Harness: `fork_sim_fee_executor/run_fee_executor_forksim.py`
- Tests: `tests/pytest/test_fee_executor_forksim.py`,
  `tests/pytest/test_fee_conversion_executor.py`
- Runbook: `docs/ops/FEE_EXECUTOR_RUNBOOK.md`
- CLI NO_POOL evidence: `docs/ops/forksim-evidence/cli_no_pool_report.json`
