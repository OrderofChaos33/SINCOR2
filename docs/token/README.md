# SINC and AXIOM Token Overview

SINCOR2 uses two distinct token roles on Base to separate platform utility from task settlement.

## Token roles

### SINC
- Governance and utility-oriented token for ecosystem participation.
- Supports contribution incentives, policy alignment, and marketplace utility design.
- Useful for long-horizon coordination, staking-oriented mechanics, and promotion criteria inside the broader SINCOR2 economy.

### AXIOM (AXM)
- Settlement token for A2A task exchange and marketplace payment flows.
- Referenced throughout the runtime as the token used to quote and settle agent-to-agent work.
- Used by payment verification and settlement coordination components on Base chain ID `8453`.

## Canonical on-chain references

- **Treasury**: `0xAf9B539D8043C634b7E611818518BA7E850F289e`
- **SINC**: `0x9C8cd8d3961F445D653713dE65C6578bE11668e7`
- **AXIOM / AXM**: `0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822`
- **Base chain ID**: `8453`

## Mechanics

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

## Operational expectations

- Verify chain and token address before confirming settlement.
- Record all economically meaningful events in a durable ledger.
- Apply reconciliation checks between quotes, confirmed payments, and treasury journal entries.
- Treat token-handling code as financially sensitive infrastructure.
