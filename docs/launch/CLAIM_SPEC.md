# SINCOR Phase 2 — Claim Specification

**Document**: `docs/launch/CLAIM_SPEC.md`  
**Version**: 1.0  
**Locked by**: 2026-08-12  
**Legal posture**: All claims are utility access credits. No investment value implied. No ROI language anywhere.

---

## Overview

The Harvest Moon claim is a one-time, fixed-pool distribution of SINC utility access credits funded exclusively from a pre-allocated treasury slice. There is **no new mint**. The pool is small by design — its purpose is to bootstrap agent usage, not to reward speculation.

---

## Architecture Decision: Merkle-Tree Claim (Selected)

**Selected**: Merkle-tree claim contract (`HarvestClaim.sol`)

| Property | Value |
|----------|-------|
| Mechanism | EIP-712-compatible Merkle proof claim |
| Root setter | One-time set by deployer; ownership transferred to treasury multi-sig immediately after |
| Anti-sybil | One claim per wallet address (enforced on-chain via `claimed` bitmap) |
| Engagement gate | Wallet must have called at least one public SINCOR agent skill (checked off-chain in eligibility script; included in Merkle leaf) |
| Claim window | 30 days from root activation (configurable at deploy time) |
| Pool size | ≤ 1–2% of treasury-controlled SINC supply (exact amount set at deploy time) |
| Language | "SINC utility access credit — grants access to agent compute. No investment value implied." |

**Why Merkle over EIP-712 signed messages**: Merkle proofs are fully on-chain verifiable with no signing key risk at claim time. The signing key is only needed during tree generation (off-chain), after which it is no longer needed and cannot be used to generate new proofs.

---

## Token Pool

- Source: Treasury wallet `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`
- Transfer to contract: Manual treasury tx before root activation
- Maximum allocation: 1% of treasury-controlled supply (hard cap encoded in contract)
- **No new mint under any circumstance**

---

## Eligibility Rules

An address is eligible if **all** of the following are true:

1. **Wallet age**: Address has ≥ 1 on-chain transaction older than 30 days on Base mainnet
2. **Prior interaction**: Address has successfully called at least one public SINCOR agent skill (logged in `EligibilityRecord` table)
   - OR: Address is on the pre-vetted warm list curated by Negotiator + Caretaker agents
3. **No prior claim**: Address has not claimed in this campaign (enforced on-chain)
4. **Not a known contract**: Address must be an EOA (no `EXTCODESIZE > 0`)

---

## Anti-Sybil Rules

- One claim per address (bitmap on-chain)
- Engagement gate (skill call or warm list) reduces fresh wallet spam
- Off-chain: wallets flagged as cluster-associated (same funding source, same creation block batch) are excluded during Merkle tree generation
- Rate limiting on eligibility API: 10 checks per IP per minute

---

## Claim Amount

- Fixed amount per eligible wallet (no variable amounts in v1 to keep tree simple)
- Amount: **TBD by Director/Auditor before Aug 12 root lock**
- Encoded as a single `uint256 amount` field in the Merkle leaf

---

## Merkle Leaf Structure

```
leaf = keccak256(abi.encodePacked(address account, uint256 amount))
```

---

## Contract Interface

```solidity
// HarvestClaim.sol
function claim(bytes32[] calldata proof, uint256 amount) external;
function isEligible(address account, uint256 amount, bytes32[] calldata proof) external view returns (bool);
function claimed(address account) external view returns (bool);
function remainingAllocation() external view returns (uint256);
function claimWindowOpen() external view returns (bool);
```

---

## Off-Chain Components

| Component | File | Purpose |
|-----------|------|---------|
| Eligibility script | `scripts/generate_harvest_merkle.py` | Generate root + proofs from wallet list |
| Eligibility API | `GET /api/harvest/eligibility?address=` | Agent-readable eligibility check |
| Claim initiation | `POST /api/harvest/claim` | Serve proof to wallet for on-chain claim |
| Status | `GET /api/harvest/status` | Public stats: claims, remaining, window |

---

## Compliance Language

Every public-facing surface must use only:

> "SINC utility access credits — grants access to SINCOR agent compute services. This is not an investment. SINC tokens have no guaranteed value and are only redeemable for agent platform services."

No mention of: price, returns, investment, yield, profit, appreciation, airdrop value.

---

## References

- Contract: `onchain/src/HarvestClaim.sol`
- Tests: `onchain/test/HarvestClaim.t.sol`
- Deploy: `onchain/script/DeployHarvestClaim.s.sol`
- Gate: `docs/launch/HARVEST_GATE.md`
