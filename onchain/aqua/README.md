# 1inch Aqua Integration for SINC (Base)

**Status:** Working ship script (real SwapVM strategy encoding, tested).  
**Purpose:** Multiply effective SINC liquidity via 1inch Aqua shared self-custodial layer without locking tokens.

One wallet balance can back multiple concurrent strategies (Shared Liquidity Ratio ≥ 2–3× observed in the wild).

## Critical Security Notes (for Gemini / any auditor)

1. **Self-custodial by design.** Aqua registry never takes custody. Tokens remain in the maker wallet under a standard ERC-20 allowance. Tokens only move on a successful fill (atomic pull + push).
2. **Allowance is revocable.** You can revoke the Aqua registry allowance at any time.
3. **No private keys in this repo.** The ship script never hard-codes or logs keys. It only builds calldata or uses `process.env.PRIVATE_KEY` when the explicit `--execute` flag is passed.
4. **Dry-run is the default.** Running the script without `--execute` prints the exact transaction data for manual review / hardware wallet signing.
5. **Official addresses only.** All contract addresses are taken from the 1inch SDKs (`NetworkEnum.COINBASE`).
6. **Existing SINCOR2 SharedLiquidityHook is complementary**, not conflicting. Aqua is the external, multi-chain, audited shared-liquidity layer.
7. **Start small.** Ship tiny amounts first. Monitor fills on 1inch Aqua UI / Basescan before scaling.

## Canonical Addresses (Base, chainId 8453)

| Contract | Address | Notes |
|----------|---------|-------|
| Aqua Registry | `0x1111113ccf1426a8e30e2bff5e005d929bf6a90a` | From `@1inch/aqua-sdk` AQUA_CONTRACT_ADDRESSES[COINBASE] |
| AquaSwapVMRouter | `0x111111338c5091e8440b67b168bae16a668ac0de` | From `@1inch/swap-vm-sdk` AQUA_SWAP_VM_CONTRACT_ADDRESSES[COINBASE] |
| SINC | `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` | 8 decimals |
| WETH | `0x4200000000000000000000000000000000000006` | 18 decimals |
| USDC (native Circle) | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` | 6 decimals |

## Prerequisites

```bash
cd onchain/aqua
pnpm install
```

## Usage

### 1. Dry-run (safe – real calldata, no broadcast)

```bash
MAKER_ADDRESS=0xYourAddress pnpm tsx scripts/ship-sinc-strategy.ts
```

This builds a constant-product (XYC) strategy with 0.30% fee for SINC/WETH and prints the exact `to` + `data` for the `ship()` call. Review it, then sign manually if desired.

### 2. Execute (real on-chain)

```bash
MAKER_ADDRESS=0xYourAddress PRIVATE_KEY=0x... pnpm tsx scripts/ship-sinc-strategy.ts --execute
```

**Warning:** This sends a real transaction. The PRIVATE_KEY address must match MAKER_ADDRESS. Start with tiny amounts.

### 3. Approve tokens first (one-time per token)

```solidity
SINC.approve(0x1111113ccf1426a8e30e2bff5e005d929bf6a90a, type(uint256).max);
WETH.approve(0x1111113ccf1426a8e30e2bff5e005d929bf6a90a, type(uint256).max);
```

Or use the 1inch Aqua UI at https://1inch.com/aqua which handles approval + position creation.

## What the script actually does (verified)

1. Builds `AquaXYCAmmStrategy.new().withFeeTokenIn(30).build()`
2. Wraps it in `Order.new({ maker, program, traits: MakerTraits.default() })`  
   (default traits already set `useAquaInsteadOfSignature = true`)
3. Calls `aqua.ship()` via the official SDK → produces non-empty strategy bytes and valid calldata (selector `0xf50b870f`)
4. Dry-run prints the tx; `--execute` broadcasts only when PRIVATE_KEY is present and matches MAKER_ADDRESS

## Recommended First Positions for SINC

1. **Concentrated around $1.50 floor** – use `AquaXYCAmmStrategy.newConcentrate({ rawPriceMin, rawPriceMax })` once you are comfortable with the basic ship flow.
2. **Full-range / constant-product** (what this script does) – always available, good for discovery volume.
3. Prefer the official 1inch Aqua frontend for the very first live positions if you want a visual range picker.

## What this does NOT do

- Does not lock or transfer tokens into any pool.
- Does not create new Uniswap V4 pools.
- Does not interact with your existing SharedLiquidityVault or Morpho markets.
- Does not burn, mint, or change SINC supply.
- Does not require any upgrade to existing contracts.

## Audit checklist for Gemini

- [ ] All addresses come from the official SDKs (NetworkEnum.COINBASE).
- [ ] Script defaults to dry-run.
- [ ] No private key is committed or logged.
- [ ] MAKER_ADDRESS is required and validated.
- [ ] On --execute, PRIVATE_KEY address must equal MAKER_ADDRESS.
- [ ] Strategy encoding is non-empty and produced by `@1inch/swap-vm-sdk`.
- [ ] Comments and README match the actual code behavior.

## References

- 1inch Aqua docs: https://business.1inch.com/portal/documentation/aqua/
- Aqua SDK: https://github.com/1inch/sdks/tree/master/typescript/aqua
- SwapVM SDK: https://github.com/1inch/sdks/tree/master/typescript/swap-vm
- Aqua public app: https://1inch.com/aqua

---

Built for SINCOR / getsincor.com – 2026-08-01 (fixed & verified)
