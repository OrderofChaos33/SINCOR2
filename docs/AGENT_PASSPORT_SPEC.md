# Agent Passport — DAE Interop Primitive

**Status:** Design (Issue #149)
**Goal:** Portable, verifiable agent identity that other DAOs/DAEs can accept for membership, voting weight, or execution rights without SINCOR giving up control.

## Core Idea

Extend the existing **SincGenesisNFT** (soulbound) + on-chain / off-chain reputation into a **Passport** that any contract or agent can read.

Passport = 
1. Soulbound (or non-transferable) token linked to agent/operator address
2. Attested skill set + performance history (from SINCOR A2A task completions)
3. Reputation score (success rate, settlement volume, TOA feedback quality)
4. Optional expiry / refresh via continued activity

## Minimal Interface Other Protocols Can Call

```solidity
interface IAgentPassport {
    function hasPassport(address agent) external view returns (bool);
    function reputation(address agent) external view returns (uint256); // scaled 1e18
    function skills(address agent) external view returns (bytes32[] memory); // skill ids
    function issuedAt(address agent) external view returns (uint64);
}
```

## Implementation Path

1. Keep Genesis NFT as the base identity (already deployed).
2. Add a lightweight PassportRegistry or extend the NFT with ERC-5192 / custom views that expose the above.
3. Reputation updated by SINCOR settlement events (SINC/AXM payments + task completion attestations).
4. Other DAOs add a simple modifier or gate: `require(IAgentPassport(passport).reputation(msg.sender) >= threshold)`.

## Why This Accelerates Traction

- External DAOs can grant SINCOR agents (or operators who hold Genesis) voting / execution rights based on proven work.
- Creates a reason for agents and operators to accumulate reputation *inside* SINCOR.
- Does not require SINCOR to join or be governed by external DAOs.

## Next Steps

- [ ] Minimal PassportRegistry contract (or NFT extension)
- [ ] Event emission on successful A2A task settlement that updates reputation
- [ ] Example gate contract for a test DAO
- [ ] Agent Card field advertising passport support
