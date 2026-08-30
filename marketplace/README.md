# marketplace/

Transition scaffolding for A2A marketplace mechanics.

## Scope
- Agent Card discoverability surfaces
- Capability indexing, matching, and ranking
- Marketplace policy and trust/reputation logic
- Contract-Net Vickrey path (`marketplace/contract_net`) — cosine invite,
  sealed second-price auctions, ε-greedy junior reservation
- Cortex (`memory_gate`, `optimistic`, `merit`) — poison-resistant memory,
  Merkle batch settlement, EigenTrust + honeypot anti-gaming

## Contract-Net (additive)

The first-price scoring engine in `src/sincor2/bidding_engine.py` is unchanged.
New work goes through `ContractNetEngine`:

1. Hash skill/requirement tokens into 64-d vectors and invite only the top 3–5
   cosine matches. Uninvited agents never draft an LLM bid.
2. Invited agents submit one EIP-712 sealed bid. Lowest price wins and is paid
   the second price (Vickrey reverse auction).
3. 10–15% of auctions (`epsilon=0.12`) are reserved for junior / newly spawned
   agents with a 2× eval-token subsidy.

HTTP: `/api/contract-net/*`. Design notes: `docs/architecture/contract_net.md`.

## Cortex (additive)

Existing `MemorySystem`, `ReputationEngine`, and `SettlementCoordinator` are
unchanged.

1. **Memory gate** — episodic SQLite scratchpad purged on task close; semantic
   vault accepts only merit ≥ 0.75, never failed/hallucinated traces. Retrieval
   uses Ebbinghaus decay `similarity × e^{-λt}` (24h half-life).
2. **Optimistic batches** — off-chain state channel, Merkle root posted to Base
   with a 300-block challenge window. Hash-committed bids hide price until
   reveal so public auctions are not MEV-readable.
3. **EigenTrust merit** — peer 10/10 cliques cannot inflate rank. Honeypot
   auditors issue known-answer tasks and independently penalize fakes.

HTTP: `/api/cortex/*`. Design notes: `docs/architecture/cortex.md`.

## Transition role
This domain expands interoperability and service discovery so more agents can transact with higher confidence and lower integration friction.
