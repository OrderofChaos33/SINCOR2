# CANONICAL ADDRESSES — SINCOR / AXIOM (Base mainnet, chain 8453)

**Single source of truth. If any other file disagrees, this file wins.**

**2026-08-16 CEO DIRECTIVE:** AXIOM (AXM) is now the **sole** platform and A2A settlement token. SINC is legacy only.

## Live, verified contracts

| Role | Address | Notes |
|---|---|---|
| **AXIOM (AXM) token** | `0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822` | **PRIMARY** — sole platform utility + A2A settlement + billing |
| **Treasury** | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` | Platform treasury (A2A routing, fees) |
| **SINC token (legacy)** | `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` | 8 decimals. Legacy holders only. No new billing. |
| **SincBondingCurve (legacy)** | `0x75dE341a2BC81806198364F125d4Cde36527619C` | Residual SINC sale. Do not expand. |
| **SincLimitOrderHook** | `0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0` | Legacy |
| **SincGenesisNFT (soulbound)** | `0xF3Bd56788b5E56DE638AF5dDffFA478838A68d09` | Legacy |
| **Uniswap v4 PoolManager** | `0x498581fF718922c3f8e6A244956aF099B2652b2b` | Infra |
| **Uniswap v4 PositionManager** | `0x7C5f5A4bBd8fD63184577525326123B519429bDc` | Infra |

## Environment overrides

```bash
AXIOM_CONTRACT_ADDRESS=0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822
TREASURY_ADDRESS=0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac
# SINC retained for legacy reads only
SINC_CONTRACT_ADDRESS=0x9C8cd8d3961F445D653713dE65C6578bE11668e7
SINC_BONDING_CURVE=0x75dE341a2BC81806198364F125d4Cde36527619C
BASE_CHAIN_ID=8453
```

## Stale addresses — do not use

| Address | Why wrong |
|---|---|
| `0xb627F53E08AD7d455e787d052C18D6877020E2BF` | Old bonding curve |
| `0x25cA41Dac29f892c72A53500853eC45a5FfF90aa` | Superseded bonding curve |
| `0x49E392de962Fa835B862F59E78611c69E930b5C4` | Dead-liquidity v2 SINC |
| `0xAf9B539D8043C634b7E611818518BA7E850F289e` | Legacy treasury |

---

## Deployed 2026-07-19 (shared-liquidity stack)

| Contract | Address | Status |
|----------|---------|--------|
| SharedLiquidityVault | `0xeA90a257e5Dae20a0472C4812775F28614459bb6` | LIVE |
| SharedLiquidityHook (staging) | `0x5A20BfEc6Caa3A94246eCCCb36F27F4980152dC0` | LIVE — production pool attachment pending |

- Deployer/guardian (temporary, rotate to multisig): `0xdba7180cdd90D12B9Bc2F15080ddFD9B14fEf31a`
