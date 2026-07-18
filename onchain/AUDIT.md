# Security Audit — Shared-Liquidity Stack & SINCLending

**Scope:** `src/SharedLiquidityVault.sol`, `src/SharedLiquidityHook.sol`, `src/SINCLending.sol` (+ interfaces)
**Tooling:** Slither 0.11.x (static analysis, 90+ detectors), manual adversarial review, Foundry test suite (34 tests incl. 1000-run fuzz), solc 0.8.26 via-IR
**Date:** 2026-07-19 · **Status:** 0 high / 0 medium findings open · 34/34 tests green

---

## 1. Findings resolved in this pass

| # | Severity | Contract | Issue | Resolution |
|---|----------|----------|-------|------------|
| F1 | Medium | SINCLending | `closeLoop` wrote collateral state **after** external router/USDC calls (CEI violation) | Restructured: full collateral settlement before any external call |
| F2 | Medium | SINCLending | `liquidate` wrote seize accounting after external calls | Restructured: effects-first (`_repayLedger` + collateral writes), interactions last |
| F3 | Medium | SharedLiquidityVault | `settleUp` updated ledgers after `transferFrom` | Restructured CEI: all ledger writes before token movement |
| F4 | Low | SINCLending | `_repay` mixed ledger + token flows, making CEI impossible for callers | Split into ledger-only `_repayLedger`; transfers handled by callers |
| F5 | Low | SINCLending | `liquidate` reverted with misleading `Unauthorized` on healthy positions | Dedicated `PositionHealthy` error |
| F6 | Low | SINCLending | No zero-address checks on `setOracle`/`setSwapRouter`; no `setTreasury` | Added checks + `setTreasury` |
| F7 | Low | Vault / Hook | `require` string in settleUp; uninitialized locals flagged | Custom errors, explicit initialization |
| F8 | Info | All | Missing events on admin setters (observability gap) | Events added: `StrategyActiveUpdated`, `OwnershipTransferred`, `PriceFloorUpdated`, `RiskParamsUpdated`, `RateModelUpdated`, `OracleUpdated`, `SwapRouterUpdated`, `TreasuryUpdated` |
| F9 | Info | Vault | `deposit` interaction before effects | Reordered CEI |

## 2. Findings reviewed and accepted (documented trust assumptions)

| # | Detector | Rationale |
|---|----------|-----------|
| A1 | `reentrancy-no-eth` in `SINCLending.openLoop` | The borrow→swap→redeposit pattern inherently interleaves external calls. Mitigations: `nonReentrant` on every entry point, router is a **guardian-set trusted address**, whole tx reverts atomically on failure, loop bounded by `maxLoops ≤ 4`. |
| A2 | `calls-loop` in `openLoop` / `simulateLoopROI` | Loops are hard-bounded by variant `maxLoops` (presets 2–4; guardian-configured variants bounded by `maxLTVBps`). No unbounded iteration possible. |
| A3 | `incorrect-equality` zero-checks (`== 0`) | Internal accounting guards, not balance-donation comparisons. Exchange-rate paths use tracked `totalCash`/`totalBorrows`, not `balanceOf`, so direct donations cannot manipulate share price. |
| A4 | `reentrancy-eth` on hook MEV path | `totalMEVDonated` written before the call; `acceptMEVDonation` is `nonReentrant`; treasury is the protocol EOA `0x09E2…12Ac`. Plain 2300-gas ETH sends to the hook already fail by design (use `acceptMEVDonation`). |
| A5 | `incorrect-exp` / `divide-before-multiply` in `lib/v4-core` | Uniswap's audited `FullMath`; known Slither false positives. Libs excluded from our findings via `slither.config.json`. |
| A6 | `block.timestamp` usage | Interest accrual deltas; no deadline/ordering dependence. Linear per-second model is intentionally manipulation-tolerant (no discrete jumps to game). |

## 3. Design-level safety properties (verified by tests)

- **Solvency invariant** (`vault.checkInvariant()`): `balance + outstanding ≥ deposits + fee claims` per token — fuzzed across random draw/settle sequences (1000 runs).
- **Aqua oversubscription bound**: virtual allocations may overlap across strategies, but every execution-time draw is bounded by `min(remaining commitment, free real capital, strategy cap)`.
- **Never-bricks**: every hook↔vault call is try/catch; a depleted allocation skips the pull and the swap proceeds. Verified for unregistered pools and zero-liquidity LPs.
- **Honest loop math**: `simulateLoopROI` shows negative ROI at 0% price change (borrow cost is real). Leverage figures pinned exactly in tests (1.75x / 2.3471x / 3.0507x).
- **Round-up obligations**: `borrowBalance` rounds up 1 wei (Bunni convention); full-repay underflow path clamped and tested.
- **Price floor**: collateral valued at `max(oracle, floor)`; floor break tested through the liquidation path.

## 4. Pre-mainnet checklist (must complete before value deployment)

- [ ] Hook address mining (beforeSwap `1<<7` + afterSwap `1<<6` flags) via CREATE2/HookMiner — see `script/Deploy.s.sol` checklist.
- [ ] Guardian key → hardware wallet or multisig (currently single-key in Deploy script).
- [ ] Oracle: replace mock with production `ISincPriceOracle` (TWAP or floor-controller-attested).
- [ ] Router: production `ISincSwapRouter` adapter (V4 swap path).
- [ ] Shadow deployment with capped strategy limits; run invariant checks after every swap for 48h.
- [ ] Consider external professional audit before TVL > $50k (this automated+manual pass is a strong baseline, not a substitute).

## 5. Continuous assurance

- `ci/onchain-ci.yml` runs `forge build`, `forge test -vvv`, and Slither (`fail_on: medium`) on every push touching `onchain/**`. Move it to `.github/workflows/` to activate (pushing workflow files via API needs a token with the `workflow` scope).
- `slither.config.json` holds the triage baseline; new medium+ findings fail CI immediately.
