# Fee Conversion Executor — arming runbook

The executor (`src/sincor2/onchain/fee_conversion_executor.py`) converts
realized AXM/SINC platform fees to USDC/WETH and forwards the output to the
treasury. It ships **disarmed** (`armed=False`): planning, quoting, and
dry-runs work, but `execute_plan` refuses to broadcast until every step below
is signed off.

Fee policy (locked 2026-09-26): 5 % platform fee, 100 % to treasury, converted
to USDC/WETH before deposit, no burn. Deflationary mechanics deferred.

## 0. Prerequisites

- A dedicated forwarder EOA. It must hold the realized fee tokens (AXM/SINC)
  and a small ETH balance for gas. It must NOT be the treasury key and must
  NOT hold anything else.
- The forwarder private key lives **only** in the Secure Vault. The executor
  never reads it; production wires a `sign_and_broadcast` callable that signs
  through the vault with per-broadcast approval.
- Base RPC URL with `eth_call` access (no archive needed).

## 1. Discover and pin the pool keys

The executor never guesses pools. For each route you intend to run
(`AXM->USDC`, `AXM->WETH`, and later `SINC->USDC`, `SINC->WETH`):

```python
from sincor2.onchain.fee_conversion_executor import (
    FeeConversionConfig, FeeConversionExecutor, PoolKey)

cfg = FeeConversionConfig(rpc_url="https://mainnet.base.org",
                          forwarder="<FORWARDER_ADDRESS>")
ex = FeeConversionExecutor(cfg)
pool = ex.discover_pool("AXM", "USDC", probe_wei=10**15)  # read-only
print(pool)  # e.g. PoolKey(currency0=..., currency1=..., fee=3000, tick_spacing=60, hooks='0x...')
```

Verify the discovered pool on Basescan (it must be the legitimate AXM/USDC
V4 pool, not an impersonator): check token addresses, fee tier, and that it
has real liquidity. Then pin it in config:

```python
cfg.pool_keys[("AXM", "USDC")] = pool
```

Repeat for every route. SINC routes additionally need a liquidity sanity
check — SINC liquidity is thin and the rogue V2 pool
(`0x85372932f9b151a076815d92cf71a97980ffd667`) must never be used.

## 2. Fork-simulate the exact calldata

1. `plan = ex.plan(obligation_id)` for a real (or synthetic) obligation.
2. Replay every unsigned tx in `plan["txs"]` against a Base mainnet fork
   (Anvil/Hardhat) with the forwarder funded and holding the fee tokens.
3. Confirm: Permit2 approval lands, the `execute()` swap fills within the
   quoted `amountOutMinimum`, and the forward moves the full output balance
   to `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`.
4. Record the fork block number, the tx hashes, and the realized slippage in
   the PR or ops log that arms the executor.

If the Universal Router build changes upstream, re-run this step before
re-arming.

## 3. Dust-amount live test

- Set `min_swap_wei` low, `slippage_bps` to 50 (0.5 %), `target="USDC"`.
- Record a synthetic obligation for a dust amount of AXM (a few cents).
- Run `execute_plan(plan, sign_and_broadcast, wait_for_receipt, dry_run=True)`
  first: every step must simulate clean via `eth_call`.
- Then run it live. Verify on Basescan: swap tx, output balance, forward tx
  to the treasury. Confirm the ledger shows `executed` with both tx hashes.

## 4. Arm

Only after steps 1–3 are green:

```python
cfg.armed = True
```

Operational notes:

- `slippage_bps` default is 100 (1 %). Lower it for large conversions.
- `min_swap_wei` should exceed ~2x the expected gas cost in output terms;
  dust stays queued in the ledger rather than being swapped at a loss.
- `target` is `"USDC"` by default (treasury stability). `"WETH"` is one
  flag away; per-obligation override is supported in `plan()`.
- The ledger (`~/workspace/ops/fee_conversion_ledger.json`) is the audit
  trail: obligation -> quote -> approve/swap/forward tx hashes -> executed.
  Back it up; it is append-mostly and tiny.
- Failure handling: any reverted step marks the obligation `failed` with the
  reason and stops the plan. Partially completed plans (e.g. swap confirmed,
  forward reverted) keep their tx history — re-plan the obligation after
  fixing the cause; already-confirmed steps are not repeated blindly.
- The Basescan fee watcher (`~/workspace/ops/basescan_fee_watcher.py`)
  covers AXM transfers into the treasury; extend it to USDC/WETH once the
  first live conversion lands.
