# Treasury HOLD — LIFTED

Lifted by founder 2026-09-10 20:54 CDT. Prior lock 2026-09-10 is revoked.
Code: `src/sincor2/treasury_hold.py`. Full: `docs/TREASURY_HOLD.md`.
Treasury: `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`.

## Live now
1. **AXM A2A paid lane is LIVE.** Realized fees (`projected=false` + `tx_hash`) still take **500 bps** to treasury. Simulated / `free_call` / `0xSIMULATED` stay off the realized ledger.
2. **Polyclaw is LIVE 24/7** on its own Polygon wallet (`POLYCLAW_LIVE=true` + `POLYMARKET_PRIVATE_KEY`). Not the Base treasury key.
3. **HOLD is OFF.** On-chain USDC / USDC.e / ETH / POL in the trading + ops wallets may be used by authorized live agents.
4. Treasury exec broadcast still requires Railway `EXECUTE_LIVE=1` and no halt file. Default in source stays `false` so a clone cannot spend.
5. Founder signer is still the only treasury broadcaster. Auditor still gates vault/LP size-ups.

## Still not automatic
- Do not point Polymarket at the treasury EOA.
- Do not treat `EXECUTE_LIVE` default in git as on.
- Kill switch in `bankroll` can still halt Polyclaw mid-cycle.
