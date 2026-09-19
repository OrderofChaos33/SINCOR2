# SINC + AXIOM — Onchain Contracts

Foundry project for all SINCOR ecosystem smart contracts, deployed on **Base** (chainId 8453).

Locked public spec: [`TOKEN_CANON.md`](../TOKEN_CANON.md).

---

## Token overview

| Token | Symbol | Contract (Base mainnet) | Supply | Decimals | Role |
|-------|--------|------------------------|--------|----------|------|
| SINC  | SINC   | `0xe1D836087F6573b665d25CE088793E916D7892f8` | 1 B   | 8  | Platform utility token |
| AXIOM | AXM    | `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a` | 1 B   | 18 | A2A inter-agent settlement |

Both tokens: fixed supply claimed. Verify source and holders on Basescan before repeating “verified / audited” claims. Live SINC explorer snapshot 2026-09-19: 3,455 holders. Overview widget may still show Transfers=0 (indexer). Original 2026-09-01 lock snapshot was 1 holder / 0 transfers.

**Official price floor:** $0.15 USD per SINC ($150M FDV / 1B tokens). **$1.50 is a ceiling wall, not a floor.**

**Treasury:** `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`
