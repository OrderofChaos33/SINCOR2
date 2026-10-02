# CNP-10 — Hierarchical CNP: composable subcontracting + nested seals

**Status:** built on branch `xioix/cnp-10-hierarchical` (unmerged). Founder-authorized 2026-10-01.
**Code:** `src/sincor2/hierarchical_cnp.py` · **Tests:** `tests/pytest/test_cnp10_hierarchical.py` (25 tests).

## What it is

The flat CNP engine (`marketplace/contract_net/engine.py`) runs one sealed-bid
Vickrey round per task. CNP-10 layers a **bounded task tree** on top:

- An award winner may **subcontract** part of their task. The subcontract is
  awarded by *reusing the flat CNP engine recursively* — one engine round per
  tree level (`HierarchicalTaskGraph.subcontract` → `ContractNetEngine.run`).
- Every award is bound into a **nested seal chain**: each subcontract's seal
  embeds `parentSealHash`, so any leaf seal walks back to the genesis
  (depth-0) seal. A child seal with a broken parent chain is rejected.
- **Depth limits** (`HierarchicalConfig.max_depth`, default 3, hard cap 8)
  and **cycle prevention** (task ids unique graph-wide; a task can never be
  its own ancestor) keep the hierarchy a bounded tree.

## Seal design

`NestedSeal` is an EIP-712 envelope signed over the ContractNet domain
(chainId-bound, same domain as bids):

```
NestedSeal(bytes32 parentSealHash, string agentDid, address agentAddress,
           string taskId, string[] capabilities, uint256 expiry,
           uint256 nonce, uint256 depth)
```

- `seal_id` is **derived** (`keccak256` of the digest), never self-declared.
- secp256k1 only by default; HMAC is demo-only and default-deny
  (`allow_hmac_seals`), mirroring the bid path.
- Distinct `primaryType` ⇒ distinct typehash ⇒ no cross-replay with
  `Bid` / `SigilSeal` envelopes.

## Authorization model

Referencing a parent seal hash is public knowledge — *extending* a chain is
not. By default (`require_winner_authorization=True`) a child seal must be
signed by the **parent task's award winner**. A forged child seal signed by
anyone else is rejected even when its signature is cryptographically valid.
An optional fail-closed `authorized_signers` allowlist can further restrict
which addresses may appear anywhere in a verified chain.

## Threat model (adversarial review)

| Attack | Mitigation |
|---|---|
| Unbounded recursion / griefing via hostile registry | All walks are iterative with an absolute iteration cap (`4 * (max_depth + 2)`); `max_depth ≤ 8` hard cap |
| Cycle smuggling through seal references (A↔B, self-parent) | Visited-set on the walk **plus** strict depth-decrease-by-1 per level — a cycle cannot satisfy both |
| Parent-chain forgery (attacker mints child over victim's parent hash) | Parent must be *registered* and fully re-verified; signer must be the parent award's winner |
| Depth smuggling (forged parent at wrong depth) | `parent.depth == child.depth - 1` enforced at every step |
| Orphaned subcontracts | `register_subcontract` requires a registered parent node with an intact chain; unknown parent hash ⇒ `SealChainError` |
| Seal replay across tasks | Digest binds `taskId`; per-signer nonces; duplicate `seal_id` registration rejected |
| Indefinite seals | TTL capped at 30 days at mint; expiry checked at every verification |
| Cross-chain replay | Same chainId-bound EIP-712 domain as the bid path (W-3) |
| Failed auctions getting sealed | Awards without a winner can never be registered |

## CNP-1 integration point

CNP-1 (`SigilSeal`) was built on an unmerged branch and is **not** imported
here. `NestedSeal` reuses the CNP-1 `SigilSeal` field names and semantics
verbatim where they overlap (`agentDid` / `agentAddress` / `capabilities` /
`expiry` / `nonce`; same `did:sincor:<label>` shape, same capability
charset rules, same 30-day TTL cap) and adds `parentSealHash`, `taskId`,
`depth`.

When CNP-1 lands, the migration is:

1. Replace `mint_nested_seal` / `verify_nested_seal` internals with the
   CNP-1 `SigilSeal` type — either extend its typed-data with the three
   hierarchical fields or sign `keccak256(sealDigest || parentSealHash ||
   taskId || depth)` alongside it.
2. Route seal signing through the CNP-1 `SealEnclave` (opaque key handles)
   instead of raw `sign_digest`; the graph's `SealKeyResolver` becomes an
   enclave adapter.
3. **No changes** to `verify_seal_chain` or `HierarchicalTaskGraph` — they
   depend only on the seal *interface* (`seal_id`, `parent_seal_hash`,
   `depth`, `agent_address`, `expiry`), not on envelope construction.

## API surface

- `mint_nested_seal(net_config, ...)` / `verify_nested_seal(seal, net_config, *, now, ...)` — single-seal ops.
- `verify_seal_chain(leaf, registry, net_config, *, now, max_depth, ...)` → root-first chain.
- `HierarchicalTaskGraph(engine, config, key_resolver)`:
  - `run_root(task, agents, ...)` — engine auction + genesis seal.
  - `subcontract(parent_node_id, task, agents, ...)` — engine auction + child seal chained to parent.
  - `register_root` / `register_subcontract` — low-level, explicit seals (adversarial testing, external award pipelines).
  - `lineage(node_id)`, `verify_node_chain(node, ...)`, `verify_graph(...)` — audits.

## Acceptance mapping

- 3-level nesting end-to-end → `test_three_level_nesting_end_to_end`
- Cycles rejected → `test_task_id_cycle_rejected`, `test_seal_reference_cycle_rejected`, `test_self_parent_seal_rejected`
- Depth cap enforced → `test_depth_cap_enforced`
- Child seal with broken parent chain rejected → `test_broken_parent_chain_rejected`, `test_orphaned_subcontract_rejected`
- 20+ tests → 25 tests in `tests/pytest/test_cnp10_hierarchical.py`
