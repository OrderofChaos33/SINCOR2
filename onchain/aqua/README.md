# 1inch Aqua Integration for SINC (Base)

**Status:** Scaffolding + production-ready ship script (dry-run by default).  
**Purpose:** Multiply effective SINC liquidity via 1inch Aqua shared self-custodial layer without locking tokens.

This is the highest-leverage liquidity primitive available on Base as of 2026-08-01. One wallet balance can back multiple concurrent strategies (Shared Liquidity Ratio ≥ 2–3× observed).

## Critical Security Notes (for Gemini / any auditor)

1. **Self-custodial by design.** Aqua registry never takes custody. Tokens remain in the maker wallet under a standard ERC-20 allowance. Tokens only move on a successful fill (atomic pull + push).
2. **Allowance is revocable.** You can revoke the Aqua registry allowance at any time via Basescan or `approve(0)`.
3. **No private keys in this repo.** The ship script never hard-codes or logs keys. It only builds calldata or uses `process.env.PRIVATE_KEY` when the explicit `--execute` flag is passed.
4. **Dry-run is the default.** Running the script without `--execute` prints the exact transaction data for manual review / hardware wallet signing.
5. **Official addresses only.** All contract addresses below are taken from 1inch docs and confirmed on Base.
6. **Existing SINCOR2 SharedLiquidityHook is complementary**, not conflicting. Aqua is the external, multi-chain, audited shared-liquidity layer. Keep your internal vault/hook for V4-native flows.
7. **Start small.** Ship tiny amounts first. Monitor fills on 1inch Aqua UI / Basescan before scaling.

## Canonical Addresses (Base, chainId 8453)

| Contract | Address | Notes |
|----------|---------|-------|
| Aqua Registry | `0x1111113ccf1426a8e30e2bff5e005d929bf6a90a` | Vanity, identical on all 13 supported chains |
| AquaSwapVMRouter v1.0.2 | `0x111111338c5091e8440b67b168bae16a668ac0de` | The `app` parameter for `ship()` |
| SwapVM Engine | `0x8fdd04dbf6111437b44bbca99c28882434e0958f` | Underlying execution engine |
| SINC | `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` | 8 decimals |
| WETH | `0x4200000000000000000000000000000000000006` | 18 decimals |
| USDC (native Circle) | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` | 6 decimals |

## Prerequisites

```bash
cd onchain/aqua
pnpm add @1inch/aqua-sdk @1inch/swap-vm-sdk viem
```

Copy or set:

```
BASE_RPC_URL=https://mainnet.base.org   # or your preferred RPC
PRIVATE_KEY=0x...                       # only needed for --execute
```

## Usage

### 1. Dry-run (safe – prints calldata only)

```bash
pnpm tsx scripts/ship-sinc-strategy.ts
```

This builds a constant-product (XYC) strategy example for SINC/WETH and prints the exact `to` + `data` for the `ship()` call. Review it, then sign manually with any wallet.

### 2. Execute (real on-chain – requires PRIVATE_KEY)

```bash
pnpm tsx scripts/ship-sinc-strategy.ts --execute
```

**Warning:** This will send a real transaction. Start with tiny amounts.

### 3. Approve tokens first (one-time per token)

Before shipping, the maker wallet must have approved the Aqua registry for the tokens being shipped:

```solidity
SINC.approve(0x1111113ccf1426a8e30e2bff5e005d929bf6a90a, type(uint256).max);
WETH.approve(0x1111113ccf1426a8e30e2bff5e005d929bf6a90a, type(uint256).max);
// same for USDC if used
```

Or use the 1inch Aqua UI at https://1inch.com/aqua which handles approval + position creation in one flow.

## Recommended First Positions for SINC

1. **Concentrated around $1.50 floor** (tight range) – highest fee density while price stays near floor.
2. **Full-range / constant-product** – always available, lower density, good for discovery volume.
3. **Pegged vs USDC** if you want stable-side coverage.

Use the official 1inch Aqua frontend for the first live positions (it supports concentrated, curved/pegged, and full-range). Use this script for automation / agent-driven shipping once the encoding is battle-tested.

## What this does NOT do

- Does not lock or transfer tokens into any pool.
- Does not create new Uniswap V4 pools.
- Does not interact with your existing SharedLiquidityVault or Morpho markets.
- Does not burn, mint, or change SINC supply.
- Does not require any upgrade to existing contracts.

## Next steps after first successful ship

1. Monitor fills and SLR on 1inch Aqua UI + Basescan.
2. Claim any Merkl / 1INCH incentives if the pair is eligible.
3. Expand to multiple strategies from the same balance.
4. Wire high-reputation agents to ship small strategies (boost routing priority).
5. Layer DualPool (Uniswap v4 JIT + ERC-4626) on residual inventory if desired.

## Audit checklist for Gemini

- [ ] All addresses match the table above (no typos).
- [ ] Script defaults to dry-run.
- [ ] No private key is committed or logged.
- [ ] Allowance is standard ERC-20 and revocable.
- [ ] Strategy encoding is taken from official `@1inch/swap-vm-sdk` patterns.
- [ ] No calls that move funds except the explicit `ship()` after user approval.
- [ ] Comments and README match the actual code behavior.

## References

- 1inch Aqua docs: https://business.1inch.com/portal/documentation/aqua/
- Aqua SDK: https://github.com/1inch/sdks/tree/master/typescript/aqua
- SwapVM SDK: https://github.com/1inch/sdks/tree/master/typescript/swap-vm
- Aqua public app: https://1inch.com/aqua

---

Built for SINCOR / getsincor.com – 2026-08-01
