# SINC + AXIOM — Onchain Contracts

Foundry project for all SINCOR ecosystem smart contracts, deployed on **Base** (chainId 8453).

Locked public spec: [`TOKEN_CANON.md`](../TOKEN_CANON.md).

---

## Token overview

| Token | Symbol | Contract (Base mainnet) | Supply | Decimals | Role |
|-------|--------|------------------------|--------|----------|------|
| SINC  | SINC   | `0xe1D836087F6573b665d25CE088793E916D7892f8` | 1 B   | 8  | Platform utility token |
| AXIOM | AXM    | `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a` | 1 B   | 18 | A2A inter-agent settlement |

Both tokens: fixed supply claimed. Verify source and holders on Basescan before repeating “verified / audited” claims. Live SINC explorer snapshot on lock date: 1 holder, 0 transfers.

**Official price floor:** $0.15 USD per SINC ($150M FDV / 1B tokens). **$1.50 is a ceiling wall, not a floor.**

**Treasury:** `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`

**DO NOT BUY:** retired SINC `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` or Uniswap V2 pool `0x85372932f9b151a076815d92cf71a97980ffd667`.

---

## Supply allocation

**Live SINC is 1,000,000,000 tokens.** Live-token allocation proofs are unpublished. Do not copy the retired 100M table onto the 1B token.

### Historical only — retired SINC v1 (100 M at `0x9C8cd8…168e7`)

| Bucket | Amount | Notes |
|--------|--------|-------|
| Bonding curve (Phase 1 + LP seed) | 65 M | Historical design for the retired token |
| Concentrated $1.50 **ceiling** LP | 5 M | Sell wall. Not an official price |
| Sell-side limit-order ladder | 20 M | Hook ladder design |
| Sablier 24-month linear vest | 10 M | Publish stream id before citing |

## Supply allocation (AXIOM — 1 B) — design only until proofs land

| Bucket | Amount | Notes |
|--------|--------|-------|
| Ecosystem / A2A treasury | 80 % | Agent-to-agent payment pool |
| Team / development | 10 % | 24-month vest recommended |
| Liquidity (Uniswap V4) | 10 % | Seeded at launch; LP burned |
