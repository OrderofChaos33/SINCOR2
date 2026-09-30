# Architecture

System architecture views, boundaries, and scaling models for SINCOR2.

| Doc | What it is |
|---|---|
| [`overview.md`](./overview.md) | Architecture overview and transition foundation. |
| [`AUCTION_GROUND_TRUTH.md`](./AUCTION_GROUND_TRUTH.md) | Sealed-bid auction ground-truth specification — the canonical spec, verified line-by-line against the code. |
| [`CANONICAL_PATHS.md`](./CANONICAL_PATHS.md) | The one canonical path per money movement (auction + treasury/payment modules); grandfathered, dormant, and quarantined paths labeled. |
| [`MONEY_FLOW.md`](./MONEY_FLOW.md) | Canonical money flow: task → auction → settlement → fee → treasury. If any page disagrees, this doc wins. |
| [`SHARED_STATE_ADOPTION.md`](./SHARED_STATE_ADOPTION.md) | Merge-time guide: adopting the shared durable-state backend (memory/SQLite/Redis) for rate limits, quotas, and idempotency keys. (Wave 30) |
| [`contract_net.md`](./contract_net.md) | Contract-Net task market design notes (additive market path). |
| [`cortex.md`](./cortex.md) | Cortex: memory gate, optimistic settlement, EigenTrust merit. |
| [`registry.md`](./registry.md) | Registry: A2A schema gate + canonical on-chain addresses. |
| [`toa.md`](./toa.md) | Temporal Optimization Agent (TOA). |
