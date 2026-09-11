# Treasury HOLD — LIFTED 2026-09-10 20:54 CDT

Founder revoked the 2026-09-10 lock. Code: `src/sincor2/treasury_hold.py`.
Destination for platform fees: `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`.

1. **500 bps** realized platform fees → treasury. Simulated and free-quota paths do not count.
2. **HOLD OFF.** Authorized wallets (Polyclaw Polygon EOA, Base ops wallets with USDC / USDC.e / ETH / POL) may trade and settle.
3. **AXM settlement LIVE** on the paid A2A lane.
4. **Polyclaw LIVE 24/7** when `POLYCLAW_ENABLED=true`, `POLYCLAW_LIVE=true`, and `POLYMARKET_PRIVATE_KEY` is set on Railway.
5. Treasury-exec Morpho/LP/vault broadcasts still need `EXECUTE_LIVE=1` in env and halt file absent. Git default remains off.

Roles: Founder signer broadcasts treasury moves. Polyclaw uses the dedicated CLOB wallet. Auditor gates size.
