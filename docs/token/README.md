# SINC and AXIOM Token Overview

SINCOR2 uses two distinct token roles on Base to separate platform utility from task settlement.

## Token roles

### SINC
- Governance and utility-oriented token for ecosystem participation.
- Supports contribution incentives, policy alignment, and marketplace utility design.
- Official **$0.15 USD hard floor** ($150M FDV / 1B tokens) enforced in the Morpho-compatible Chainlink oracle and platform checkout.
- Useful for long-horizon coordination, staking-oriented mechanics, and promotion criteria inside the broader SINCOR2 economy.

### AXIOM (AXM)
- Settlement token for A2A task exchange and marketplace payment flows.
- Referenced throughout the runtime as the token used to quote and settle agent-to-agent work.
- Used by payment verification and settlement coordination components on Base chain ID `8453`.

## Canonical on-chain references

Runtime: `src/sincor2/onchain/constants.py`. Human index: `CANONICAL_ADDRESSES.md`.

- **Treasury**: `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`
- **SINC**: `0xe1D836087F6573b665d25CE088793E916D7892f8` (8 decimals; retired `0x9C8cd8…`)
- **AXIOM / AXM**: `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a` (live 2026-08-18; previous `0xfF7aF6ff…` is dead)
- **Base chain ID**: `8453`
- **Morpho oracle / setup / staking**: `onchain/src/morpho/`

## Mechanics

### Official price floor
- Protocol design enforces a **$0.15** minimum for SINC in official paths ($150M / 1B).
- `SincChainlinkOracle.price()` (Morpho `IOracle`, 1e36 scale) never returns below floor.
- Secondary markets or aggregators may show other quotes; those are outside platform control.

### Treasury routing
- Marketplace settlements and treasury-aware fee flows should route to the canonical treasury address.
- Liquidity and settlement modules should record payer, payee, token, amount, timestamp, and task reference.
- Treasury routing events should be observable and auditable for operational review.

### Deflationary mechanics
The runtime documentation in `src/sincor2/a2a_integration.py` describes two supply-tightening mechanisms around AXIOM usage:

1. A2A payment receipts may split so that a burn component is sent to the dead address.
2. Treasury routing preserves an ecosystem funding stream distinct from the burn mechanism.

When implementing or updating token mechanics, keep the burn logic, treasury accounting, and user-facing quotes aligned.

### Governance and utility considerations
- Use SINC for governance-facing incentives, contributor recognition, and utility-layer coordination.
- Use AXIOM for marketplace settlement where explicit task-level payment confirmation is required.
- Avoid mixing token roles without documenting the policy reason and the accounting treatment.

## Compliance notes

- SINC and AXM are **utility tokens**, not securities or investment products.
- No income, price, or performance guarantees.
- Healthcare / regulated vertical outputs are decision-support only.
- Users must verify contract addresses against this document before signing transactions.

## Operational expectations

- Verify chain and token address before confirming settlement.
- Record all economically meaningful events in a durable ledger.
- Apply reconciliation checks between quotes, confirmed payments, and treasury journal entries.
- Treat token-handling code as financially sensitive infrastructure.
