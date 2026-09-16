# Agent Passport — DAE Interop Primitive

**Status:** Implemented MVP (Issue #149)
**Goal:** Portable, verifiable agent identity that other DAOs/DAEs can accept for membership, voting weight, or execution rights without SINCOR giving up control.

## Core Idea

Extend the existing **SincGenesisNFT** (soulbound) + on-chain / off-chain reputation into a **Passport** that any contract or agent can read.

Passport = 
1. Soulbound (or non-transferable) token linked to agent/operator address
2. Attested skill set + performance history (from SINCOR A2A task completions)
3. Reputation score (success rate, settlement volume, TOA feedback quality)
4. Optional expiry / refresh via continued activity

## Implemented MVP Interface (on `SincGenesisNFT`)

```solidity
interface IAgentPassport {
    function hasPassport(address agent) external view returns (bool);
    function reputation(address agent) external view returns (uint256); // scaled 1e18
    function skills(address agent) external view returns (bytes32[] memory); // skill ids
    function issuedAt(address agent) external view returns (uint64);
    function attestPassport(address agent, uint256 newScore, bytes32[] calldata skillsToAdd) external;
}
```

## Implementation Path

1. Genesis NFT remains the non-transferable base identity.
2. `SincGenesisNFT` now exposes passport-readable fields and emits `PassportAttested` updates.
3. Curve authority can update score + append unique skill attestations (`bytes32` skill IDs).
4. Other DAOs can gate permissions with `hasPassport + reputation + skill` checks (see `onchain/test/SincGenesisNFT.t.sol` `PassportGatedTestDAO`).

## Why This Accelerates Traction

- External DAOs can grant SINCOR agents (or operators who hold Genesis) voting / execution rights based on proven work.
- Creates a reason for agents and operators to accumulate reputation *inside* SINCOR.
- Does not require SINCOR to join or be governed by external DAOs.

## Remaining Next Steps

- [ ] Wire `attestPassport` updates to A2A settlement finalization (automated score updates).
- [ ] Publish canonical skill namespace for cross-DAO interoperability.
- [ ] Optional Base-native attestation registry mirror for non-NFT consumers.
