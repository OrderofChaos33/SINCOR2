# SINC Liquidity Amplification: Uniswap V4 Shared Hook + Aqua/Fluid Hybrid
**Technical Specification & Phased Implementation Plan**

**Version**: 1.0  
**Date**: 2026-07-10  
**Status**: Spec complete. Research verified. Ready for user mobile review → feature branch stub → tests → audit.  
**Related**: GitHub Issue #78 (BOSS TASK)  
**Owner**: Grok (spec + safe execution) + Court (priority & final approvals)  
**Rule**: Everything production-ready, thoroughly tested, ZERO build breaks. Docs/specs first.

---

## 1. Executive Summary

Extend SINCOR2's existing hardened Uniswap V4 hook infrastructure (SincLimitOrderHook + IntentHookV2) with a **shared/virtual liquidity layer** inspired by 1inch Aqua + Fluid unified accounting. Goal: 5-40x effective liquidity depth for SINC pairs without capital fragmentation or full lock-up for LPs.

LPs deposit once → virtual balances allocated across:
- Concentrated LP provision (V4 hook)
- Yield-bearing collateral (Aave/Morpho via RehypothecationAdapter patterns)
- Optional agent-task collateral or SINC utility boosts

Idle capital earns real yield on top of trading fees. SINC stakers get priority, lower fees, and yield share. Treasury captures protocol fees + MEV-style donations. Self-funding flywheel for SINCOR2/DAE.

**Why now**: Existing hooks are production-hardened and tested (Bunni adversarial passed on IntentHookV2). Aqua virtual accounting is live on Base. V4 custom accounting/BaseCustomAccounting scaffolding is mature. First-mover advantage on Base for agent-economy-native liquidity.

**Non-goals**: No new risky primitives. No illegal activity. Grey-area dynamic fees/yield routing OK if utility-focused and disclosed.

---

## 2. Goals & Success Metrics

- **Primary**: 5-40x effective TVL multiplier on key SINC pairs vs standard V3/V4 pools.
- **LP Experience**: Single deposit → real APY (fees + external yield) > pure emissions. No fragmentation.
- **Protocol**: Deeper liquidity → higher volume → more fees/treasury inflows → SINC utility/staking demand → buy pressure + burn.
- **Integration**: Seamless with A2A marketplace routing (prefer deep SINC pairs), treasury/AccountingHub, settlement.
- **Security**: Pass Bunni-style adversarial sequences + FullMath hardened patterns + full Foundry fuzz. Audit path (CertiK or equivalent).
- **Timeline Target**: Spec (done) → stub on feature branch (after your approval) → tests + testnet → audit → mainnet incentives bootstrap (weeks, not months).

**KPIs to track** (Dune + on-chain):
- TVL multiplier vs baseline
- LP realized APY (fees + yield)
- SINC pair volume share
- Treasury fee/MEV inflows
- Staked SINC participation rate

---

## 3. Architecture Overview (Leverage Existing – Do Not Reinvent)

**Foundation (already production-grade)**:
- `onchain/src/SincLimitOrderHook.sol`: Extends OZ LimitOrderHook + anti-sandwich dynamic fees (BASE 0.30% → SANDWICH 3.00% same-block).
- `contracts/hooks/IntentHookV2.sol`: Hardened V4 hook (reentrancy guard, safe FullMath, protocol fee capture, live MEV donation with safe fallbacks to treasury/Hub, AccountingHub integration, Bunni-hardened proportional reduction).
- `onchain/src/RehypothecationAdapter.sol` + `AccountingHub.sol`: Ready for yield routing and tracking.
- `infrastructure/liquidity.py`: Treasury monitoring, fee routing, health checks (extend for new layer).
- Python side: marketplace/settlement, dynamic_pricing_engine, etc.

**New Component**: `SincSharedLiquidityHook` (or extension of SincLimitOrderHook) using V4 **custom accounting** (inspired by BaseCustomAccounting + Aqua virtual balances).

**Data Flow**:
1. LP deposits SINC/USDC (or other pair) to hook-managed vault/position.
2. Hook mints virtual shares; records in per-LP / per-strategy virtual balance map (Aqua-style, no full lock).
3. Virtual liquidity allocated: X% to V4 concentrated LP, Y% to yield collateral (Aave/Morpho), optional Z% to SINC utility/agent collateral.
4. Swaps hit hook → dynamic fees (utilization/vol-based + anti-sandwich) + protocol fee to treasury.
5. Yield accrues to virtual positions; LPs claim or auto-compound.
6. SINC stakers: boost multiplier on their virtual allocation (priority routing in marketplace, fee discount, extra yield share).
7. AccountingHub records everything for treasury/DAE visibility.

**Deployment**: New hook address mined for V4 permissions (before/after add/remove liquidity, before/after swap). Compatible with existing pools or new deep SINC pairs.

---

## 4. Technical Design

### 4.1 Hook Permissions (V4 standard)
- `beforeAddLiquidity`, `afterAddLiquidity`
- `beforeRemoveLiquidity`, `afterRemoveLiquidity`
- `beforeSwap`, `afterSwap` (dynamic fee + fee capture)
- Optional `beforeInitialize` for pool gating.

### 4.2 Custom / Virtual Accounting (Core Innovation)
Use V4 `BaseCustomAccounting` pattern or full custom curve if needed.

Key state:
```solidity
mapping(address => mapping(bytes32 => uint256)) public virtualBalances; // lp => strategyHash => virtual amount
mapping(bytes32 => uint256) public totalVirtualLiquidity; // per pool/strategy
```

On add liquidity:
- Accept real tokens.
- Mint virtual shares proportionally.
- Allocate virtual % to LP position + yield adapter call (non-blocking, safe try/catch fallback).

On swap:
- Compute effective liquidity from virtual totals.
- Apply dynamic fee (base + utilization multiplier + anti-sandwich from existing SincLimitOrderHook logic).
- Capture protocol fee safely (IntentHookV2 pattern with FullMath.mulDiv).

On remove:
- Burn virtual shares.
- Proportional real token return (use hardened `_safeProportionalReduction` from IntentHookV2).
- Redeem any yield collateral.

### 4.3 Yield Routing
- Idle virtual liquidity → deposit to Aave V3 or Morpho via lightweight adapter (reuse RehypothecationAdapter.sol patterns).
- Yield accrues to position; claimable or auto to SINC stake.
- Fallback: if adapter fails, keep in hook (safe, never lost).

### 4.4 SINC Utility Integration
- Staked SINC (via existing or new staking) → multiplier on virtual allocation.
- Benefits: higher yield share, priority in A2A task routing for deep liquidity pairs, reduced fees.
- Deflation: portion of fees → SINC buyback/burn or treasury SINC accumulation.

### 4.5 Dynamic Fees & Anti-MEV
- Base from existing hook.
- Add utilization-based component (higher utilization = higher fee to protect LPs).
- Keep/ extend anti-sandwich (same-block fee spike).
- MEV donation path via IntentHookV2 receive() + acceptMEVDonation (already live).

### 4.6 Accounting & Treasury
- Every fee/yield/MEV → AccountingHub.record... (IntentHookV2 pattern).
- Safe fallbacks to treasury if Hub unavailable.
- Python `LiquidityManager` extended to monitor virtual layer health + runway.

---

## 5. Implementation Phases (Safe, Gated, Production-First)

**Phase 0: Spec & Alignment (DONE – this document)**
- Research verified (Aqua on Base, V4 hooks, existing SINCOR2 hooks).
- This spec pushed to repo.
- Gap tracker updated.

**Phase 1: User Review + Feature Branch (Next – your mobile decision)**
- You review this spec + tracker on GitHub mobile.
- Reply "proceed to stub" or with changes.
- Grok creates feature branch + minimal stub contract (SincSharedLiquidityHook.sol skeleton inheriting BaseHook + key interfaces).
- No main branch code changes.

**Phase 2: Full Contract + Python Glue (After your approval)**
- Implement virtual accounting, allocation logic, yield adapter calls, dynamic fee math.
- Extend `infrastructure/liquidity.py` for monitoring.
- Add minimal dashboard widgets or events for transparency.
- All changes on feature branch.

**Phase 3: Testing & Simulation (Non-negotiable)**
- Full Foundry test suite (extend existing onchain/test/ patterns: math, integration, anti-sandwich, proportional reduction, reentrancy).
- Adversarial sequences (Bunni dust + reductions – already passed on IntentHookV2; replicate).
- Python-side unit + integration tests.
- Testnet deployment on Base Sepolia (new hook + test pool).
- Simulation of 5-40x depth + LP APY scenarios.

**Phase 4: Audit & Hardening**
- Internal review + specialist math audit if needed.
- External audit path (CertiK recommended, reuse prior relationships).
- Address all findings before mainnet.

**Phase 5: Mainnet + Bootstrap**
- Deploy to Base mainnet.
- Seed initial deep SINC/USDC or SINC/AXM pair with treasury incentives.
- Launch LP incentives (real yield + SINC rewards for depth).
- Marketplace awareness: "SINC Liquidity Engine – deeper pairs = better agent routing".
- Monitor via Dune + on-chain dashboards.
- Iterate (multi-pool, agent-routed swaps, etc.).

**Rollback**: Feature flag or kill switch in hook for emergency pause.

---

## 6. Security, Testing & Audit Plan

**Hardening Principles** (copy from IntentHookV2 – already proven):
- ReentrancyGuard on all state-changing hooks.
- FullMath.mulDiv / mulDivUp everywhere (no naive division).
- Safe fallbacks (never revert user funds; log and treasury fallback).
- Access control (onlyPoolManager, owner for admin).
- Events for all critical actions (fee capture, donation, allocation change).

**Test Coverage Required**:
- Unit math (proportional reduction, virtual share mint/burn, fee calc).
- Integration (add/remove/swap flows with yield).
- Adversarial (exact Bunni sequence + 60 tiny reductions).
- Fuzzing (Foundry invariant tests on virtual balances never understated).
- End-to-end (LP deposit → swap volume → yield claim → treasury capture).
- Gas & slippage benchmarks.

**Audit Checklist**:
- [ ] Internal + math specialist review
- [ ] External security audit (focus on custom accounting + cross-contract calls)
- [ ] Formal verification or heavy fuzz on critical math if budget allows
- [ ] Testnet soak + bug bounty lite before mainnet

---

## 7. Integration Points

- **On-chain**: AccountingHub (record everything), RehypothecationAdapter (yield), existing hooks (co-exist or compose).
- **Python**: Extend LiquidityManager with virtual layer health + new routing events. Update dynamic_pricing_engine if needed for SINC pair priority.
- **Marketplace / A2A**: Expose new capability tag "deep-liquidity-provider". Route high-value tasks to agents using deep SINC pairs when beneficial.
- **Dashboards**: Add virtual TVL, LP APY, yield sources to admin/operator views.
- **Treasury/DAE**: All inflows visible; portion can auto-allocate to SINC staking or buybacks.

---

## 8. Risks, Mitigations & Rollout Notes

**Risks**:
- Smart contract / accounting bug → Mitigation: hardened patterns from live hooks + exhaustive tests + audit.
- IL for LPs → Mitigation: concentrated ranges + yield buffer + virtual allocation flexibility.
- Yield adapter failure → Mitigation: safe try/catch + fallback to hook-held liquidity (never lost).
- Regulatory perception of "yield" → Mitigation: frame as utility + real revenue share for LPs supporting agent economy; full disclosure.
- Low initial adoption → Mitigation: treasury-seeded incentives + SINC stake boosts + marketplace routing preference.

**Rollout**: Conservative. Testnet first. Incentives bootstrap only after audit. Kill switch ready.

---

## 9. Code Structure Sketches (High-Level)

**New/Extended Hook Skeleton** (inherits BaseHook + reuses LimitOrder/Intent patterns):
```solidity
contract SincSharedLiquidityHook is BaseHook, ReentrancyGuard {
    // virtual balance maps, totalVirtual per strategy
    // allocation percentages (owner tunable or governance)
    // yieldAdapter address

    function getHookPermissions() public pure override returns (Hooks.Permissions memory) { ... }

    function _beforeAddLiquidity(...) internal override { ... virtual mint + allocate ... }
    function _afterSwap(...) internal override { dynamicFee + capture + yield accrual ... }
    // safe proportional remove using IntentHookV2 pattern
}
```

**Python Extension** (infrastructure/liquidity.py):
```python
class LiquidityManager:
    def monitor_virtual_layer(self): ...
    def route_virtual_yield(self): ...
```

Full implementation details in subsequent PRs on feature branch.

---

## 10. References & Assets
- Existing: `onchain/src/SincLimitOrderHook.sol`, `contracts/hooks/IntentHookV2.sol`, `onchain/src/RehypothecationAdapter.sol`, `onchain/src/AccountingHub.sol` + interface, `infrastructure/liquidity.py`
- V4: BaseHook, BaseCustomAccounting, Hooks library, FullMath
- Aqua: Virtual balance registry pattern (live on Base)
- Related docs: `docs/superpowers/runbooks/2026-05-20-post-launch-liquidity-acceleration.md`, `docs/funding/artifacts/uniswap-funding-brief.md`
- Issue #78 for context and discussion

---

**Ready for your review on mobile, Court.**

Reply with "proceed to feature branch stub" or edits. We keep the build clean and production-grade. This is the highest-leverage move for SINC self-funding right now.

Grok – executed safely, thoroughly, affirmatively.