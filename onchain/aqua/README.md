# 1inch Aqua Integration for SINC (Base)

**Status: LIVE (hardened 2026-08-07)**  
Production ship path for SINC on 1inch Aqua. See `SECURITY.md` for pen-test findings and fixes.

**Purpose:** Multiply effective SINC liquidity via shared self-custodial layer. One wallet balance backs multiple strategies (SLR ≥ 2–3×).

## Security gates (required)

1. **Self-custodial** — tokens stay in maker wallet until fill; allowance revocable.
2. **`CONFIRM=LIVE`** required for any broadcast (prevents accidental ships).
3. **Amount caps** — default max 100k SINC / 5 WETH per ship (`MAX_SINC_SHIP` / `MAX_WETH_SHIP`).
4. **Preflight** — balance, allowance, chainId 8453, estimateGas before send.
5. **Pinned addresses** — SDK registry/router cross-checked against `constants.ts`.
6. **Not the Polyclaw key** — use treasury / `ONCHAIN_EXECUTOR_PRIVATE_KEY` only.
7. **No keys in repo** — `PRIVATE_KEY` env only; never logged.

## Canonical addresses (Base)

| Contract | Address |
|----------|---------|
| Aqua Registry | `0x1111113ccf1426a8e30e2bff5e005d929bf6a90a` |
| AquaSwapVMRouter | `0x111111338c5091e8440b67b168bae16a668ac0de` |
| SINC | `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` |
| WETH | `0x4200000000000000000000000000000000000006` |
| USDC | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |
| Treasury (maker) | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` |

## Setup

```bash
cd onchain/aqua
pnpm install
```

Approve once:

```text
SINC.approve(0x1111113ccf1426a8e30e2bff5e005d929bf6a90a, max)
WETH.approve(0x1111113ccf1426a8e30e2bff5e005d929bf6a90a, max)
```

## Usage

```bash
# Preview calldata only
MAKER_ADDRESS=0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac pnpm ship:dry-run

# LIVE ship
CONFIRM=LIVE \
MAKER_ADDRESS=0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac \
PRIVATE_KEY=0x... \
pnpm ship

# Custom size (still subject to caps)
CONFIRM=LIVE SINC_AMOUNT=5000 WETH_AMOUNT=0.05 \
MAKER_ADDRESS=0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac \
PRIVATE_KEY=0x... \
pnpm ship
```

Env:
- `SINC_AMOUNT` (default `1000`), `WETH_AMOUNT` (default `0.01`), `FEE_BPS` (default `30`)
- `MAX_SINC_SHIP` / `MAX_WETH_SHIP` — raise only deliberately
- `BASE_RPC_URL` — prefer private RPC in production
- `CONFIRM=LIVE` — mandatory for broadcast

## What this does NOT do

- Lock tokens into a pool
- Create Uniswap V4 pools
- Touch SharedLiquidityVault / Morpho
- Change SINC supply

## References

- `SECURITY.md` — this surface’s audit
- `../AUDIT.md` — vault/hook/lending audit
- https://business.1inch.com/portal/documentation/aqua/
- https://1inch.com/aqua
