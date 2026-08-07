# 1inch Aqua Integration for SINC (Base)

**Status: LIVE**  
Production ship path for SINC liquidity on 1inch Aqua (Base). No simulation default.

**Purpose:** Multiply effective SINC liquidity via 1inch Aqua shared self-custodial layer without locking tokens. One wallet balance backs multiple concurrent strategies (Shared Liquidity Ratio ≥ 2–3×).

## Critical Security Notes

1. **Self-custodial by design.** Aqua registry never takes custody. Tokens remain in the maker wallet under a standard ERC-20 allowance. Tokens only move on a successful fill (atomic pull + push).
2. **Allowance is revocable.** Revoke the Aqua registry allowance at any time.
3. **No private keys in this repo.** Script uses `process.env.PRIVATE_KEY` only at runtime.
4. **Live by default.** `pnpm ship` broadcasts. Use `--dry-run` only when you explicitly want calldata preview.
5. **Official addresses only.** All contract addresses from the 1inch SDKs (`NetworkEnum.COINBASE`).
6. **Complementary** to existing SharedLiquidityHook / Morpho setup — does not conflict.

## Canonical Addresses (Base, chainId 8453)

| Contract | Address | Notes |
|----------|---------|-------|
| Aqua Registry | `0x1111113ccf1426a8e30e2bff5e005d929bf6a90a` | From `@1inch/aqua-sdk` |
| AquaSwapVMRouter | `0x111111338c5091e8440b67b168bae16a668ac0de` | From `@1inch/swap-vm-sdk` |
| SINC | `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` | 8 decimals |
| WETH | `0x4200000000000000000000000000000000000006` | 18 decimals |
| USDC (native Circle) | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` | 6 decimals |

## Prerequisites

```bash
cd onchain/aqua
pnpm install
```

## Approve tokens (one-time per token)

```solidity
SINC.approve(0x1111113ccf1426a8e30e2bff5e005d929bf6a90a, type(uint256).max);
WETH.approve(0x1111113ccf1426a8e30e2bff5e005d929bf6a90a, type(uint256).max);
```

Or use https://1inch.com/aqua (handles approval + position creation).

## Usage — LIVE

```bash
# Default live ship (constant-product SINC/WETH, 0.30% fee)
MAKER_ADDRESS=0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac \
PRIVATE_KEY=0x... \
pnpm ship

# Custom amounts
SINC_AMOUNT=5000 WETH_AMOUNT=0.05 \
MAKER_ADDRESS=0x... PRIVATE_KEY=0x... \
pnpm ship

# Optional: preview calldata only
MAKER_ADDRESS=0x... pnpm ship:dry-run
```

Env overrides:
- `SINC_AMOUNT` (default `1000`)
- `WETH_AMOUNT` (default `0.01`)
- `FEE_BPS` (default `30`)
- `BASE_RPC_URL` (default `https://mainnet.base.org`)

PRIVATE_KEY address **must** match MAKER_ADDRESS or the script aborts.

## What the script does

1. Builds `AquaXYCAmmStrategy.new().withFeeTokenIn(FEE_BPS).build()`
2. Wraps in `Order.new({ maker, program, traits: MakerTraits.default() })`
3. Calls `aqua.ship()` via official SDK → valid ship calldata
4. Broadcasts on Base unless `--dry-run` is passed

## Recommended positions

1. Full-range / constant-product (this script) — discovery volume
2. Concentrated around $1.50 floor — use `AquaXYCAmmStrategy.newConcentrate(...)` once basic flow is confirmed live
3. Parallel SINC/USDC strategy for stable pair depth

## What this does NOT do

- Does not lock or transfer tokens into any pool
- Does not create new Uniswap V4 pools
- Does not touch SharedLiquidityVault or Morpho markets
- Does not burn, mint, or change SINC supply

## References

- 1inch Aqua docs: https://business.1inch.com/portal/documentation/aqua/
- Aqua SDK: https://github.com/1inch/sdks/tree/master/typescript/aqua
- SwapVM SDK: https://github.com/1inch/sdks/tree/master/typescript/swap-vm
- Aqua public app: https://1inch.com/aqua

---

SINCOR / getsincor.com — LIVE 2026-08-07
