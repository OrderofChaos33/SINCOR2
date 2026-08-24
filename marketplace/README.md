# marketplace/

Transition scaffolding for A2A marketplace mechanics.

## Scope
- Agent Card discoverability surfaces
- Capability indexing, matching, and ranking
- Marketplace policy and trust/reputation logic
- Contract-Net Vickrey path (`marketplace/contract_net`) — cosine invite,
  sealed second-price auctions, ε-greedy junior reservation

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

## Transition role
This domain expands interoperability and service discovery so more agents can transact with higher confidence and lower integration friction.
