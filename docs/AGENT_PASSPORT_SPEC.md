# Agent Passport — DAE Interop Primitive

**Status:** Design → Implementation path (updated 2026-08-19 with network-side gaps)  
**Goal:** Portable, verifiable agent identity that other DAOs/DAEs/marketplaces can accept for membership, voting weight, or execution rights without SINCOR giving up control.

## Core Idea

Extend the existing **SincGenesisNFT** (soulbound) + on-chain / off-chain reputation into a **Passport** that any contract or agent can read.

Passport =  
1. Soulbound (or non-transferable) token linked to agent/operator address  
2. Attested skill set + performance history (from SINCOR A2A task completions)  
3. Reputation score (success rate, settlement volume, TOA feedback quality)  
4. Optional expiry / refresh via continued activity  
5. Quality tier (experimental → verified → production → staked)

## Minimal Interface Other Protocols Can Call

```solidity
interface IAgentPassport {
    function hasPassport(address agent) external view returns (bool);
    function reputation(address agent) external view returns (uint256); // scaled 1e18
    function skills(address agent) external view returns (bytes32[] memory); // skill ids
    function qualityTier(address agent) external view returns (uint8);
    function issuedAt(address agent) external view returns (uint64);
}
```

## Agent Card Surface

Every registered Agent Card SHOULD advertise:

```json
"passport": {
  "supported": true,
  "contract": "0x...",  // when live
  "reputation": 0.82,
  "tier": "verified",
  "skills": ["credentialing", "rcm"]
}
```

PublicDirectory and MCP bridge already surface this field when present.

## Implementation Path

1. Keep Genesis NFT as the base identity (already deployed).
2. Add a lightweight PassportRegistry or extend the NFT with ERC-5192 / custom views.
3. Reputation updated by SINCOR settlement events (AXM payments + task completion attestations).
4. Other DAOs / marketplaces add a simple gate: `require(IAgentPassport(passport).reputation(msg.sender) >= threshold)`.
5. On registration (examples/onboarding/register.py) optionally link wallet → passport mint / update.

## Why This Accelerates Traction

- External agents carry track record across marketplaces → lower switching cost and higher stickiness.
- High-value verticals (healthcare) can require minimum passport reputation / tier.
- Creates a reason for agents and operators to accumulate reputation *inside* SINCOR first.

## Next Steps

- [ ] Minimal PassportRegistry contract (or NFT extension)
- [ ] Event emission on successful A2A task settlement that updates reputation
- [ ] Example gate contract for a test DAO / external marketplace
- [ ] Wire register_agent() to optionally mint/update passport
- [ ] Surface passport in public directory ranking (already prepared)
