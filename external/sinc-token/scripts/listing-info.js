#!/usr/bin/env node
/**
 * SINC Token Listing Information & Links
 * For DEX, Aggregators, and CEX applications
 */

console.log(`
═══════════════════════════════════════════════════════════════════════════════
                        SINC TOKEN LISTING PACKAGE
═══════════════════════════════════════════════════════════════════════════════

TOKEN DETAILS
─────────────────────────────────────────────────────────────────────────────
  Name:           SINCOR
  Symbol:         SINC
  Network:        Base (Chain ID: 8453)
  Contract:       0xd10D86D09ee4316CdD3585fd6486537b7119A073
  Decimals:       18
  Total Supply:   100,000,000 SINC
  
  Basescan:       https://basescan.org/token/0xd10D86D09ee4316CdD3585fd6486537b7119A073

DEPLOYED CONTRACTS
─────────────────────────────────────────────────────────────────────────────
  SINC Token:     0xd10D86D09ee4316CdD3585fd6486537b7119A073
  Bonding Curve:  0x25cA41Dac29f892c72A53500853eC45a5FfF90aa
  AMM Router:     0x7949576312a33Adc76CAC2103506a6D54fADBaB7

═══════════════════════════════════════════════════════════════════════════════
                              DEX LISTINGS
═══════════════════════════════════════════════════════════════════════════════

✅ AERODROME (Live)
   Pool: SINC/WETH
   Trade: https://aerodrome.finance/swap?from=0xd10D86D09ee4316CdD3585fd6486537b7119A073&to=0x4200000000000000000000000000000000000006

📋 UNISWAP V3 (Base) - Add liquidity here:
   https://app.uniswap.org/add/0xd10D86D09ee4316CdD3585fd6486537b7119A073/ETH?chain=base

📋 BASESWAP - Add liquidity:
   https://baseswap.fi/add

📋 SWAPBASED - Add liquidity:
   https://swapbased.finance/#/add

📋 ALIENBASE - Add liquidity:
   https://alienbase.xyz/liquidity

═══════════════════════════════════════════════════════════════════════════════
                           AGGREGATORS
═══════════════════════════════════════════════════════════════════════════════
(Auto-detect once liquidity exists)

📋 1INCH
   https://app.1inch.io/#/8453/simple/swap/SINC
   Token listing form: https://forms.1inch.io/

📋 PARASWAP
   https://app.paraswap.io/
   Auto-detects from DEX pools

📋 KYBERSWAP
   https://kyberswap.com/swap/base
   Auto-detects from DEX pools

📋 0x / MATCHA
   https://matcha.xyz/
   Auto-detects from DEX pools

📋 LLAMASWAP
   https://swap.defillama.com/
   Auto-detects from DEX pools

📋 ODOS
   https://app.odos.xyz/
   Auto-detects from DEX pools

═══════════════════════════════════════════════════════════════════════════════
                        CEX LISTING APPLICATIONS
═══════════════════════════════════════════════════════════════════════════════

🏦 TIER 1 (Major) - Long process, expensive
────────────────────────────────────────────
  Coinbase:    https://www.coinbase.com/assethub
  Binance:     https://www.binance.com/en/my/coin-apply
  Kraken:      https://www.kraken.com/features/listing
  OKX:         https://www.okx.com/support/hc/en-us/articles/360000748432

🏦 TIER 2 (Mid-tier) - Easier listing
────────────────────────────────────────────
  Gate.io:     https://www.gate.io/listing
  KuCoin:      https://www.kucoin.com/land/list-your-token
  MEXC:        https://www.mexc.com/support/categories/360000320551
  Bitget:      https://www.bitget.com/support/articles/360038449511
  Bybit:       https://www.bybit.com/en-US/coin-listing/

🏦 TIER 3 (Easy) - Quick listings
────────────────────────────────────────────
  LBank:       https://www.lbank.info/listing-application
  BitMart:     https://support.bitmart.com/hc/en-us/articles/360016303414
  CoinEx:      https://www.coinex.com/token/listing
  ProBit:      https://www.probit.com/en-us/listing
  Hotbit:      https://www.hotbit.io/coin-application

═══════════════════════════════════════════════════════════════════════════════
                        TOKEN INFO SITES
═══════════════════════════════════════════════════════════════════════════════

📋 COINGECKO - Submit token:
   https://www.coingecko.com/en/coins/add

📋 COINMARKETCAP - Submit token:
   https://coinmarketcap.com/request/

📋 DEXTOOLS - Auto-detects pools
   https://www.dextools.io/app/en/base/pair-explorer

📋 DEXSCREENER - Auto-detects pools
   https://dexscreener.com/base/0xd10D86D09ee4316CdD3585fd6486537b7119A073

📋 GECKOTERMINAL - Auto-detects pools
   https://www.geckoterminal.com/base/tokens/0xd10D86D09ee4316CdD3585fd6486537b7119A073

═══════════════════════════════════════════════════════════════════════════════
                        REQUIRED FOR CEX LISTING
═══════════════════════════════════════════════════════════════════════════════

Most CEXs require:
  ✅ Verified contract (pending)
  ✅ Active trading volume
  ✅ Community/social presence
  ✅ Website
  ✅ Whitepaper
  ✅ Logo/branding
  ⬜ Audit report (recommended)
  ⬜ Legal opinion (some require)

═══════════════════════════════════════════════════════════════════════════════
`);
