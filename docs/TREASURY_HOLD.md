# Treasury HOLD — numbers, not vibes

Locked 2026-09-10. Code: `src/sincor2/treasury_hold.py`. Destination: `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`.

1. **100%** of realized platform fees (projected=false + tx_hash) → Treasury. Platform take is **500 bps**.
2. **100% HOLD** on ~$192.62 on-chain USDC. **0%** Morpho / Aave / LP / SharedLiquidity.
3. **100% HOLD** on ~$800 founder cash. **0%** load-to-chain.
4. Vault intake **0%** until first conversion. After conversion: **80%** cash / **20%** vault, and only if cash ≥ **$10,000 USDC**.
5. Underwriting go-live: **$10,000 USDC** backing + conversion proof + human `EXECUTE_LIVE`. Founder signer is the only mover. Halt file `data/TREASURY_EXEC_HALT` is ON by default.

Roles: Founder signer broadcasts. Treasury exec queues intents. Auditor gates. Nobody else moves funds.
