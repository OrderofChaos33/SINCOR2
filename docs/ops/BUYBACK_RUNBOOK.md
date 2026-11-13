# AXM Buyback Executor — Founder Runbook

**Status: DISARMED BY DEFAULT.** This executor is a skeleton. It refuses every
buyback unless the founder personally completes all four arming steps below.
There is no private key in this pipeline, no signing code, and no real
broadcast adapter shipped in this repository — the only adapter that exists is
the test fake.

Related: `docs/ops/FEE_EXECUTOR_RUNBOOK.md` (fee conversion, also held),
`src/sincor2/onchain/buyback_executor.py`.

## The triple gate (all three required)

1. **Seeded pool pinned in config** — a verified, seeded AXM liquidity pool,
   pinned by the founder (addresses in env, never hardcoded).
2. **`BUYBACK_ARMED=true`** — explicit armed flag env var.
3. **Live founder approval record** — JSON entry with approver, scope covering
   `axm-buyback`, and a future `expires_at`.

Any missing gate → clean `BuybackRefused` with a logged reason. No partial
execution, no "dry-run that signs" (there is no signing code to reach).

## Arming steps (founder only)

### Step 1 — Seed the AXM pool

1. Deploy or select an AXM liquidity pool on the target chain and seed it with
   real liquidity (AXM + the paired quote asset, e.g. USDC).
2. Verify the pool on-chain: correct token pair, fee tier, tick spacing, and
   non-trivial seeded liquidity. Record the pool parameters.
3. Pin the pool into the operator config — set these env vars and restart:

   ```
   BUYBACK_POOL_TOKEN_A=<token A address>
   BUYBACK_POOL_TOKEN_B=<token B address>
   BUYBACK_POOL_FEE=<fee, e.g. 3000>
   BUYBACK_POOL_TICK_SPACING=<tick spacing, e.g. 60>
   ```

   The gate treats this pinning as the seeded-pool detection. Do not pin a
   pool that has not been verified and seeded.

### Step 2 — Fund

1. Fund the buyback source wallet (the wallet the production adapter will draw
   from — note: **the production adapter does not exist yet**; it is built,
   reviewed, and key-custodied separately before any live buyback).
2. Set the per-execution cap so no single buyback can exceed intent:

   ```
   BUYBACK_MAX_BUY_WEI=<cap in wei, e.g. 1000000000000000000>
   ```

   `0` (the default) means no buys of any size are allowed.
3. Optionally tune slippage protection: `BUYBACK_SLIPPAGE_BPS` (default `100`
   = 1%).

### Step 3 — Record founder approval

Write an approval entry — an explicit record with scope and expiry, not a
boolean. Example file (store outside the repo, e.g. secure operator storage):

```json
{
  "approver": "founder",
  "scope": "axm-buyback",
  "issued_at": "2026-11-01T00:00:00+00:00",
  "expires_at": "2026-11-08T00:00:00+00:00",
  "note": "Seeding-week buybacks, max 1 AXM-equiv per execution"
}
```

Point the executor at it:

```
BUYBACK_APPROVAL_PATH=/secure/path/buyback_approval.json
```

Requirements the executor enforces:

- `scope` must include `axm-buyback`
- `expires_at` must be in the future (expiry is honored — an expired approval
  refuses exactly like a missing one)
- Keep approvals short-lived. Re-issue rather than extending silently.

### Step 4 — Set the armed flag

```
BUYBACK_ARMED=true
```

Only after steps 1–3 are complete. With all three gates green, the executor
will drive the **injected adapter** — which, again, is not the live chain
until a production adapter is built and wired by the founder.

## Verifying it armed correctly

From a Python shell in the test env:

```python
from sincor2.onchain.buyback_executor import BuybackConfig, BuybackExecutor, check_gates
cfg = BuybackConfig.from_env()
print(check_gates(cfg))
```

Expect `{"pass": True, "gates": {"pool_seeded": True, "armed_flag": True,
"founder_approval": True}, "reasons": []}`. If `pass` is `False`, read
`reasons` — that is the exact step to fix.

## Disarm procedure (immediate)

Any one of these instantly disarms:

1. `BUYBACK_ARMED=false` (or unset the variable) and restart — fastest.
2. Delete/expire the approval record, or let `expires_at` pass.
3. Unset the `BUYBACK_POOL_*` pinning.

Verify disarm with the same `check_gates` snippet — expect `pass: False`.

**Emergency note:** the executor has no background loop and no scheduler
integration. Killing the process or flipping the armed flag stops everything;
there is nothing queued that can fire on its own.

## What is NOT in this skeleton (by design)

- No private keys — none in code, tests, or docs. Ever.
- No signing code, no `Web3`, no `eth_account`, no HTTP calls.
- No mainnet references and no mainnet execution path.
- No real broadcast adapter. Building one requires: the production swap
  adapter implementation, key custody in the Secure Vault, a separately
  reviewed authorization contract/interface, and an explicit founder
  go-ahead — none of which exist today.
- No tx construction: `plan()` produces unsigned intent only.
