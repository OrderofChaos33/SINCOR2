# CANONICAL ADDRESSES — SINCOR / AXIOM (Base mainnet, chain 8453)

> **LOCKED 2026-09-01:** Official SINC floor is **$0.15** ($150M FDV / 1B). **$1.50 is not a floor.**
> Human spec: [`TOKEN_CANON.md`](TOKEN_CANON.md) · machine spec: [`TOKEN_CANON.json`](TOKEN_CANON.json).
>
> **DO NOT BUY** retired SINC `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` or Uniswap V2 pool `0x85372932f9b151a076815d92cf71a97980ffd667`.
> Live SINC: `0xe1D836087F6573b665d25CE088793E916D7892f8`. Official buy: https://getsincor.com/buy

**Runtime source of truth:** [`src/sincor2/onchain/constants.py`](src/sincor2/onchain/constants.py).  
This markdown is the human index. Settlement, A2A, billing, and startup `symbol()` / `decimals()` probes import the Python module. Ship both files in the same change; never copy token literals into other runtime modules.

**2026-08-19 CEO UPDATE:** Live SINC token is now `0xe1D836087F6573b665d25CE088793E916D7892f8` (8 decimals). Previous address `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` is retired.

**2026-08-18 CEO CORRECTION:** The live AXIOM (AXM) A2A Settlement token is `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a` (deployed 2026-08-18). Previous address `0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822` is stale/dead and must not be used.

**2026-08-16 CEO DIRECTIVE (still active):** AXIOM (AXM) is the **sole** platform and A2A settlement token for new flows. SINC remains for residual / legacy holders.

## Live, verified contracts

| Role | Address | Notes |
|---|---|---|
| **AXIOM (AXM) token** | `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a` | **PRIMARY** — A2A settlement + billing (deployed 2026-08-18) |
| **Treasury** | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` | Platform treasury (A2A routing, fees) |
| **SINC token** | `0xe1D836087F6573b665d25CE088793E916D7892f8` | **8 decimals, 1B supply, $0.15 floor ($150M FDV).** |
| **SincLimitOrderHook** | `0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0` | Legacy |
| **SincGenesisNFT (soulbound)** | `0xF3Bd56788b5E56DE638AF5dDffFA478838A68d09` | Legacy |
| **Uniswap v4 PoolManager** | `0x498581fF718922c3f8e6A244956aF099B2652b2b` | Infra |
| **Uniswap v4 PositionManager** | `0x7C5f5A4bBd8fD63184577525326123B519429bDc` | Infra |

## Environment overrides

```bash
AXIOM_CONTRACT_ADDRESS=0x4c3fb66f14fbaa2088c9ae91017ba770da53715a
TREASURY_ADDRESS=0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac
SINC_CONTRACT_ADDRESS=0xe1D836087F6573b665d25CE088793E916D7892f8
SINC_FLOOR_USD=0.15
BASE_CHAIN_ID=8453
```

## Stale addresses — do not use

| Address | Why wrong |
|---|---|
| `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` | Previous SINC address (retired 2026-08-19) |
| `0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822` | Previous incorrect AXM entry (dead PumpClawToken) |
| `0x75dE341a2BC81806198364F125d4Cde36527619C` | Retired bonding curve |
| `0xb627F53E08AD7d455e787d052C18D6877020E2BF` | Old bonding curve |
| `0x25cA41Dac29f892c72A53500853eC45a5FfF90aa` | Superseded bonding curve |
| `0x49E392de962Fa835B862F59E78611c69E930b5C4` | Dead-liquidity v2 SINC |
| `0xAf9B539D8043C634b7E611818518BA7E850F289e` | Legacy treasury |
| `0x85372932f9b151a076815d92cf71a97980ffd667` | Rogue Uniswap V2 SINC/USDC pool — do not buy |

---

## Deployed 2026-07-19 (shared-liquidity stack)

| Contract | Address | Status |
|----------|---------|--------|
| SharedLiquidityVault | `0xeA90a257e5Dae20a0472C4812775F28614459bb6` | LIVE |
| SharedLiquidityHook (staging) | `0x5A20BfEc6Caa3A94246eCCCb36F27F4980152dC0` | LIVE — production pool attachment pending |

- Deployer/guardian (temporary, rotate to multisig): `0xdba7180cdd90D12B9Bc2F15080ddFD9B14fEf31a`
