# CANONICAL ADDRESSES — SINCOR / SINC / AXIOM (Base mainnet, chain 8453)

**Single source of truth. Verified on-chain 2026-06-02. If any other file disagrees, this file wins.**

## Live, correct, verified contracts

| Role | Address | Status |
|---|---|---|
| **SINC token** (canonical) | `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` | 8 decimals, 100M supply (confirmed on-chain), CertiK Skynet 97/100, **Sourcify full-match** |
| **SincBondingCurve** (the live sale) | `0x75dE341a2BC81806198364F125d4Cde36527619C` | holds 64.9M SINC (~65% of supply), **Sourcify full-match** (BaseScan native verify still pending — see `onchain/VERIFY_CURVE.md`) |
| **SincLimitOrderHook** | `0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0` | **Sourcify full-match** |
| **SincGenesisNFT** (soulbound) | `0xF3Bd56788b5E56DE638AF5dDffFA478838A68d09` | **Sourcify full-match** |
| **AXIOM (AXM) token** | `0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822` | live on Uniswap v4, Base |
| Operational treasury (rotated 2026-06-14) | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` | **active** — ~351K SINC swept here after key exposure |
| ~~Old Safe~~ (abandon) | `0x2d61752adF5092052Ff7D366a9884823C07Cdaf8` | **compromised** — do not use |
| Legacy treasury / LP holder | `0xAf9B539D8043C634b7E611818518BA7E850F289e` | owns rogue V2 LP — use `recover_liquidity.ps1 rogue-v2` |
| Deploy hot wallet | `0x7B4082f78CdAc2cB5fa8572b2CA54BeDaaa8f956` | original forge deploy only |
| Uniswap v4 PoolManager | `0x498581fF718922c3f8e6A244956aF099B2652b2b` | infra |
| Uniswap v4 PositionManager | `0x7C5f5A4bBd8fD63184577525326123B519429bDc` | infra |

Authoritative deployment record: `onchain/deployments/8453.json` + `onchain/.env`.

## ⚠️ Needs a human decision — do NOT treat as canonical yet

| Address | What it is | Question |
|---|---|---|
| `0xa8a20f33C56B5B519f65CBf9529ab0d9FD785BA2` | symbol **SIN** / name "Sincor" — referenced by `templates/sin-airdrop.html` | The current campaign promises "free **AXIOM**", but this page airdrops a separate **SIN** token. Intentional, or should the page point to AXM `0xfF7a…7822`? |
| Balancer v3 LBP `0x3b35E92c4f34B8468659612B9742248185F04B00` | verified `LBPool`, created 2026-05-30 | **Holds 0 SINC / $0 — unseeded.** The funded sale is the v4 curve `0x75dE`. Which venue is the real launch? Don't drive traffic to an empty pool. |

## ❌ STALE / WRONG — do not use

| Address | Where it lingers | Why wrong |
|---|---|---|
| **Rogue Uniswap V2 SINC/USDC pair** `0x85372932f9b151a076815d92cf71a97980ffd667` | Blockscout swap, MetaMask swap, Kyber/Li.Fi/Sushi aggregators | **Not the bonding curve.** Dust LP shows fake ~$0.0000001/SINC; 10M-for-$1 quotes are real but catastrophic for supply. LP owned by `0xAf9B…` — burn via `onchain/recover_liquidity.ps1 rogue-v2`. **Never buy SINC here.** |
| `0xd10D86D09ee4316CdD3585fd6486537b7119A073` | `.env.txt` | not canonical SINC |
| `0x059ADAcb7Be9C5526abB7f3e5c6d1A712d1b0c23` | `deploy_bonding_curve.js` | a decimals=0 draft token, not canonical |
| `0x25cA41Dac29f892c72A53500853eC45a5FfF90aa` | `.env.txt` comment | old bonding curve |
| `0xb627F53E08AD7d455e787d052C18D6877020E2BF` | `buy_watcher.js` | old curve — watcher would monitor the wrong contract |
| `0x49E392de962Fa835B862F59E78611c69E930b5C4` | older docs/memory | the dead-liquidity v2 SINC being relaunched |
| `0x346e42653931adfb82d69636e9c8e4ff2745f5c4` | docs | "SINCv3" — shelved (mint/burn/team-control template) |

> **Note for `buy_watcher.js`:** it monitors `0xb627…` for Buy events but the live curve is `0x75dE…`. If you run the watcher for launch SMS alerts, update `CURVE` to `0x75dE341a2BC81806198364F125d4Cde36527619C` first or it will be silent.
