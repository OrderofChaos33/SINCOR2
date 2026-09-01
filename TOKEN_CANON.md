# TOKEN CANON — LOCKED 2026-09-01

**This file is the only public price / contract / allocation spec.**  
If any page, tweet, whitepaper, pitch deck, token list, or CEO brief disagrees with this file, this file wins. Update this file in the same commit as `TOKEN_CANON.json` and `src/sincor2/onchain/constants.py`.

Lock date: **2026-09-01**  
Chain: **Base mainnet, chainId 8453**  
Decision: **Official SINC floor is $0.15. $1.50 is not a floor.**

---

## DO NOT BUY THESE

| What | Address | Why |
|---|---|---|
| Retired SINC (v1) | [`0x9C8cd8d3961F445D653713dE65C6578bE11668e7`](https://basescan.org/token/0x9C8cd8d3961F445D653713dE65C6578bE11668e7) | 100M supply leftover. Not the live token. |
| Rogue / dead Uniswap V2 SINC/USDC pool | [`0x85372932f9b151a076815d92cf71a97980ffd667`](https://www.geckoterminal.com/base/pools/0x85372932f9b151a076815d92cf71a97980ffd667) | Quotes ~$0.001 against the retired token. Near-zero liquidity. |
| Retired bonding curve | [`0x75dE341a2BC81806198364F125d4Cde36527619C`](https://basescan.org/address/0x75dE341a2BC81806198364F125d4Cde36527619C) | Marked retired in runtime constants. Not an official buy path. |
| Dead AXM label | `0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822` | PumpClawToken. Not AXIOM. |
| Dead-liquidity v2 SINC pointer | `0x49E392de962Fa835B862F59E78611c69E930b5C4` | Stale. |
| Legacy treasury | `0xAf9B539D8043C634b7E611818518BA7E850F289e` | Stale. |

**Rule:** If MetaMask, DexScreener, GeckoTerminal, or a Telegram bot shows SINC at anything other than the official floor policy below, you are looking at the wrong contract or a junk pool. Do not swap there.

---

## LIVE CONTRACTS

| Role | Address | Decimals | Supply on explorer | Status as of 2026-09-01 |
|---|---|---|---|---|
| **SINC (live)** | [`0xe1D836087F6573b665d25CE088793E916D7892f8`](https://basescan.org/token/0xe1D836087F6573b665d25CE088793E916D7892f8) | 8 | 1,000,000,000 | Live pointer. Explorer: 1 holder, 0 transfers. |
| **AXIOM / AXM (live)** | [`0x4c3fb66f14fbaa2088c9ae91017ba770da53715a`](https://basescan.org/token/0x4c3fb66f14fbaa2088c9ae91017ba770da53715a) | 18 | 1,000,000,000 | Live A2A settlement pointer. Explorer: 1 holder. Source not verified on Basescan. |
| **Treasury** | [`0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`](https://basescan.org/address/0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac) | — | — | Fees / A2A routing. Single EOA until multisig rotation. |
| USDC (Base native) | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` | 6 | — | Stable routing only. |

Official commercial buy path: **https://getsincor.com/buy**  
Price API: **https://getsincor.com/api/price/official**

---

## PRICE — ONE NUMBER

| Term | Value | Meaning |
|---|---|---|
| **Official floor** | **$0.15 USD / SINC** | The only number that may be called “official price” or “floor.” |
| FDV at floor | **$150,000,000** | 1,000,000,000 × $0.15. |
| **$1.50** | **Not the floor** | Legacy / planned concentrated-liquidity **ceiling / sell wall**. Never call this the official price. Never print “$1.50 floor LIVE.” |
| Spot | Official path only | Secondary quotes are not official. Aggregator junk is not official. |

Plan reference prices (USD) stay $297 / $997 / $2,997. Convert to SINC at the $0.15 floor until a live official spot feed exists on the live token.

---

## SUPPLY — STOP MIXING 100M AND 1B

| Token generation | Address | Max supply | Status |
|---|---|---|---|
| **v1 RETIRED** | `0x9C8cd8…168e7` | **100,000,000** | Dead for product. Historical only. |
| **v2 LIVE** | `0xe1D836…892f8` | **1,000,000,000** | Current SINC. |

Do not publish the 65 / 5 / 20 / 10 million-unit bucket table as if it applies to the 1B token. That table was written for v1.

---

## ALLOCATION — HONEST STATUS

On-chain proof required before any bucket is advertised as funded.

### SINC v2 (1B) — current

Live-token allocation proofs are **MISSING**. Basescan shows 1 holder and 0 transfers on the live token as of lock date. Do not invent a distribution or lock/vest hashes.

**Public copy until proofs exist:** 1B fixed supply. Allocation proof pending. Do not buy junk pools.

### SINC v1 (100M) — historical only

| Bucket | Amount | Notes |
|---|---|---|
| Bonding curve + LP seed | 65M | Historical design. |
| Concentrated $1.50 *ceiling* LP | 5M | Origin of the $1.50 number. Ceiling, not floor. |
| Sell-side limit-order ladder | 20M | Hook ladder design. |
| Sablier 24-month vest | 10M | Publish stream id if it still exists. |

---

## CLAIMS YOU MAY NOT MAKE UNTIL PROVEN

- “CertiK Skynet 97/100” on the **live** SINC address unless a report URL names `0xe1D836087F6573b665d25CE088793E916D7892f8`.
- “Buy on the bonding curve” while `0x75dE…619C` is in `STALE_ADDRESSES`.
- “$1.50 floor LIVE.”
- “100M supply” for live SINC.

## COPY BLOCKS

**One-liner**  
SINC is `0xe1D836087F6573b665d25CE088793E916D7892f8` on Base. Official floor $0.15 ($150M / 1B). Buy at getsincor.com/buy. Do not buy `0x9C8cd8…` or the V2 pool.

**X post**  
Live SINC (Base): `0xe1D836087F6573b665d25CE088793E916D7892f8`  
Official floor: $0.15 ($150M FDV / 1B)  
Buy: getsincor.com/buy  
Canon: github.com/OrderofChaos33/SINCOR2/blob/main/TOKEN_CANON.md  
Retired — do not buy: `0x9C8cd8d3961F445D653713dE65C6578bE11668e7`  
Rogue V2 pool: `0x85372932f9b151a076815d92cf71a97980ffd667`
