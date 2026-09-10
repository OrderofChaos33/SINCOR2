# Treasury HOLD — 2am standing order

Locked 2026-09-10. Destination: `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`. Code: `src/sincor2/treasury_hold.py`. Full: `docs/TREASURY_HOLD.md`.

1. **100%** of realized platform fees (projected=false + tx_hash) → Treasury. Take is **500 bps**.
2. **100% HOLD** on-chain USDC (~$192). **0%** Morpho / Aave / LP / SharedLiquidity.
3. **100% HOLD** founder cash off-chain (~$800). **0%** load-to-chain.
4. Vault **0%** until first conversion; then 80/20 only if cash ≥ **$10,000 USDC**.
5. Underwriting go-live: **$10,000 USDC** backing + conversion proof + founder signer. Halt file `data/TREASURY_EXEC_HALT` is ON. `EXECUTE_LIVE` default off.

Mover: founder signer broadcasts. Treasury exec queues. Auditor gates. Nobody else.

Paste this block under README “What’s new (2026-09)” on merge if the README hunk is not already in.
