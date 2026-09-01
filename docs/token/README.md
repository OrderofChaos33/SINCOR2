# SINC and AXIOM Token Overview

**Locked spec:** [`TOKEN_CANON.md`](../../TOKEN_CANON.md) / [`TOKEN_CANON.json`](../../TOKEN_CANON.json).

> **DO NOT BUY** `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` or Uniswap V2 pool `0x85372932f9b151a076815d92cf71a97980ffd667`.
> Live SINC: `0xe1D836087F6573b665d25CE088793E916D7892f8`. Official floor **$0.15**. **$1.50 is not the floor.** Buy at https://getsincor.com/buy.

SINCOR2 uses two distinct token roles on Base to separate platform utility from task settlement.

## Token roles

### SINC
- Governance and utility-oriented token for ecosystem participation.
- Official **$0.15 USD hard floor** ($150M FDV / 1B tokens). `$1.50` is a ceiling wall from the retired v1 design — never market it as the floor.

### AXIOM (AXM)
- Settlement token for A2A task exchange and marketplace payment flows.
- Used by payment verification and settlement coordination components on Base chain ID `8453`.

## Canonical on-chain references

Runtime: `src/sincor2/onchain/constants.py`. Human index: `CANONICAL_ADDRESSES.md`.

- **Treasury**: `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`
- **SINC**: `0xe1D836087F6573b665d25CE088793E916D7892f8` (8 decimals; retired `0x9C8cd8…`)
- **AXIOM / AXM**: `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a`
- **Base chain ID**: `8453`

## Official price floor
- Protocol design enforces a **$0.15** minimum for SINC in official paths ($150M / 1B).
- Secondary markets or aggregators may show other quotes; those are outside platform control and often the retired token.
