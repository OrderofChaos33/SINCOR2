# Cortex: memory gate, optimistic settlement, EigenTrust merit

Additive to the existing 4-tier `MemorySystem`, `ReputationEngine`, and
`SettlementCoordinator`. Those classes are not modified.

## Memory gate (`marketplace/memory_gate`)

| Store | Lifetime | What enters |
|---|---|---|
| Episodic SQLite scratchpad | Task-scoped | Every thought, tool call, error, hallucination |
| Semantic vector vault | Durable | Only traces with merit ≥ 0.75, status not in the poison set |

On `close_task` the scratchpad is **purged**. Failed execution cannot linger
in the agent's context window or leak into retrieval.

Retrieval score:

```
score = cosine(query, trace) * exp(-λ · age_hours)
λ = ln(2) / 24   # 24-hour half-life
```

Recent high-merit patterns outrank stale history at equal similarity.

## Optimistic settlement (`marketplace/optimistic`)

Micro-task credits, assignments, and merit points mutate an off-chain
`StateChannel`. Periodically the operator posts

```
MerkleRoot_batch = keccak-merkle(leaves)
```

to `contracts/OptimisticBatchSettlement.sol` on Base (chain 8453). Challenge
window = **300 blocks**. A valid proof that a posted leaf does not match the
true event freezes the batch. After the window the root finalizes.

Hash-committed bids (`contracts/CommitRevealAuction.sol`):

```
commit = keccak256(price ‖ salt ‖ keccak(agentId))
```

Price is invisible during the commit window, blocking MEV on public auctions.
Reveal must match or the bid is discarded.

## Merit overlay (`marketplace/merit`)

`ReputationEngine` EMA scores remain the first-price routing prior.
Peer 10/10 ratings are **not** used raw:

- Local trust is row-normalized (EigenTrust / PageRank).
- Global trust is pulled toward a pre-trusted auditor prior (damping 0.15).
- `HoneypotAuditor` issues known-answer tasks. Failures are independent
  evidence and apply a 0.15× penalty — speed and cheap tokens cannot fake them.
- A clique with no path from the auditor converges near zero.

HTTP: `/api/cortex/*`.
