# Agent Profit Loops — Executive Summary

This document describes the autonomous capital-efficiency loops added for SINCOR agents.

## Why these loops

With a thin public pool and limited USDC, the highest-ROI path is:

1. Keep a continuous two-sided quote surface alive (limit-order ladder + anti-sandwich hook).
2. Turn every fee / MEV / spread dollar back into more inventory or deeper ladder.
3. Park idle USDC in the highest-quality permissionless yield available today (Fluid fUSDC via SincFluidAdapter).
4. Never take open-ended leverage until Fluid/Morpho SINC markets are live and oracles are trusted.

## Loop ranking by expected risk-adjusted return (Aug 2026)

| Rank | Loop | Capital required | Risk | Autonomy |
|------|------|------------------|------|----------|
| 1 | Ladder MM + fee recycle | Working inventory only | Low–Med (inventory risk) | High |
| 2 | Fluid USDC yield → ladder drip | Idle USDC | Low | High |
| 3 | SharedLiquidityVault compound | Already in vault | Low | High |
| 4 | Future Morpho/Fluid smart-collateral leverage | Real collateral + HF discipline | Medium–High | Medium (gated) |

## Safety invariants (enforced in runner)

- Daily and per-tx notional caps.
- Reserve USDC never spent.
- Kill switch on oracle / price deviation.
- Max inventory % of controlled SINC.
- Default dry-run; live requires explicit config change.

## Integration with existing code

- `onchain/src/fluid/SincFluidAdapter.sol` + `sdk/fluid-amplify.js` — Stage 1 live.
- `onchain/src/SharedLiquidityVault.sol` + SharedLiquidityHook.
- `onchain/src/SincLimitOrderHook.sol` — primary quoting surface.
- `onchain/src/SincSwapRouter.sol` — inventory rebalance.
- Preconf / Flashblocks endpoints for early large-order detection.

No new on-chain state is required for the three live loops. The runner only orchestrates calls that already exist.

## Next steps after merge

1. Deploy / confirm SincFluidAdapter address and set `FLUID_ADAPTER`.
2. Fund a dedicated agent wallet with small USDC + SINC inventory.
3. Run `--dry-run` for 24h, then flip `dry_run_default: false` and start with tiny caps.
4. Once Fluid lists SINC-USDC with oracle, enable the leverage loop under the same HF floors.
