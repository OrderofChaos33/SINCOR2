# SINCOR2 Core Contract Perimeter Freeze — 2026-11-07

Status: **PRE-DEPLOY FREEZE.** The core contracts below are built and verified
but **not yet deployed** (per `docs/ops/AUCTION_DEPLOY_CHECKLIST.md`: "NOT
DEPLOYED"). This freeze is the Execution Standard v2 pre-deploy blocker gate:
exhaustive fuzz + invariant + static analysis against a pinned source hash
set, so any source change after this point invalidates the campaign and
requires a re-run.

Precise language (per Execution Standard v2): this campaign is
**exhaustively fuzz-verified + invariant-verified + statically triaged**.
It is **NOT formal verification** (no theorem prover / symbolic proof of the
contracts was run) and it is **NOT an audit**. Do not market it as either.

- Base commit: `dde9f31f8e7954f82a491527846d47206f7ab82a` (origin/main)
- Branch: `xioix/perimeter-freeze-50k` (local only; not pushed)
- Compiler: solc **0.8.30** (root `foundry.toml` profile), optimizer on,
  optimizer_runs 200, `via_ir = true`, evm_version cancun
- Foundry: forge 1.8.5
- Test files imported from prior verified waves (test-only, unmodified):
  `test/CommitRevealAuction.t.sol`, `test/ScopedPausableSunset.t.sol`
  (branch `xioix/invariant-auction-sunset` @ ba24e2e1),
  `test/CrossContractConservation.invariant.t.sol`
  (`xioix/invariant-conservation` @ b4b40ffb),
  `test/OptimisticBatchSettlement.t.sol`, `test/StakeSlashManager.t.sol`
  (`xioix/invariant-wave2` @ b71f075d). Suites already on main:
  `SincorEscrow`, `SincorSessionValidator`, `SincorPaymaster`,
  `MoebiusXSessionAccount`, `StakeSlashManagerUnstakeClamp`.

## Frozen core set — 11 contracts, 2,693 lines

Inclusion rule: every contract on the A2A money path (auction → escrow →
settlement → stake/slash), plus identity (KYA), gas sponsorship (paymaster),
and session-key custody surfaces that guard it.

| Contract | Lines | SHA-256 |
|---|---|---|
| contracts/CommitRevealAuction.sol | 278 | afdfef05d41d02b96269f4569abf97fdcf7ae49f63628fcbfaf19aa13073d160 |
| contracts/ExecutionEscrowManager.sol | 541 | 495bbd8c020045f414590ad7b9159ecdb69381672d9236bd3238b6ff502efc88 |
| contracts/IExecutionEscrowManager.sol (interface) | 335 | 4ed22313e1f5944b23b60856e2eb4bd4635f077444ba3aed8651beaa683896df |
| contracts/OptimisticBatchSettlement.sol | 97 | f603e003798918f22fe6a7c0a508f66f4c33617ccc041273a551fc42c60e7e4b |
| contracts/StakeSlashManager.sol | 281 | 1f7846f6deac041ab469df6b7770953d306a681c8ae899ba020bf93d4d8209ce |
| contracts/escrow/SincorTaskEscrow.sol | 103 | 84fe78a43c1c644204ebccf8441f54cd9b317a4191c87ae13a272a3bd5a22137 |
| contracts/kya/KYARegistry.sol | 491 | b8cac22676df375078c0331d23c32d1f56f2a28a03f34bb2f05349f86f1f6ed0 |
| contracts/paymaster/SincorPaymaster.sol | 224 | b6e412d112bcf2d38e0f68fdb76e676d1cc0a699f7e88bf76e2c7226cbe54817 |
| contracts/security/ScopedPausable.sol | 62 | 2d94bbdedff857c7190e98b65779009a739d4390faa8af2f8706ec8e46a73b61 |
| contracts/security/SincorSessionValidator.sol | 115 | d30472a9b4f818830355f8f2772926610e7f4034bcd917fba5ea04f22ed44ccc |
| contracts/security/MoebiusXSessionAccount.sol | 166 | 22ee567dfb5131de81e30ad4f7b950ff7f032e8b4f14c8aa66a9847665d15dd5 |

## Excluded — with reasons

- `contracts/hooks/{BribeLoopHook,IntentHook,IntentHookV2}.sol` — cannot
  compile against the current Uniswap v4-core pin (documented blocker in
  `foundry.toml`: `IPoolManager.SwapParams` API removed upstream Apr 2025);
  not deployed; not on the A2A money path. Migrating them is a contract
  change and a separate dispatch. All test runs below therefore use
  `--skip BribeLoopHook --skip IntentHook`.
- `contracts/legacy/{SINCBondingCurve,SINCPlatformAccess,SINC_v3}.sol` —
  superseded token/access contracts, not part of the current money path.
- `onchain/src/**` — a SEPARATE Foundry project (solc 0.8.26, its own
  `onchain/foundry.toml`, fuzz runs=1000, invariant runs=100/depth=50) holding
  the DeFi SKU stack. It needs its own freeze campaign; it was NOT covered
  by this one and must not be described as covered.

## Baseline (default config, fuzz runs=256)

`forge test` (hooks skipped): **187 passed / 0 failed / 0 skipped, 11 suites,
299.98s wall.** Includes CrossContract invariant at runs=256, depth=500
(128,000 calls, 0 reverts): 6/6 conservation invariants passed.

## 50,000-run fuzz campaign (FOUNDRY_FUZZ_RUNS=50000; foundry.toml untouched)

| Suite | Tests | Result | Wall |
|---|---|---|---|
| CommitRevealAuction (7 fuzz tests) | 47 | 47 passed / 0 failed | 50s |
| ScopedPausableSunset (6 fuzz tests) | 20 | 20 passed / 0 failed | 18s |
| OptimisticBatchSettlement (4 fuzz tests) | 23 | 23 passed / 0 failed | 14s |
| StakeSlashManager + UnstakeClamp (4 fuzz tests) | 39 | 39 passed / 0 failed | 39s |
| SincorTaskEscrow (no fuzz tests exist — unit only) | 14 | 14 passed / 0 failed | <1s |
| SincorSessionValidator (no fuzz tests — unit only) | 9 | 9 passed / 0 failed | <1s |
| SincorPaymaster (no fuzz tests — unit only) | 19 | 19 passed / 0 failed | <1s |
| MoebiusXSessionAccount (no fuzz tests — unit only) | 13 | 13 passed / 0 failed | <1s |

Note the gap honestly: fuzz depth exists only where fuzz tests exist
(auction, pausable sunset, settlement, stake/slash — exactly the
money-path-critical set). Escrow (ExecutionEscrowManager/SincorTaskEscrow),
session validator, paymaster, and MoebiusX account are covered by unit
tests + the cross-contract invariant handlers, not by dedicated fuzz tests.
Writing those fuzz suites is follow-on work, not a silent omission.

Known documented behavior, re-confirmed not a bug: at exactly
`submittedBlock+300`, OptimisticBatchSettlement `challenge()` and
`finalize()` are both callable; ordering decides. Pinned by tests.

## Invariant campaign

- CrossContractConservation (handlers: MoneyPathHandler 17 selectors,
  StakeConservationHandler 5 selectors; actors span auction + escrow +
  batch settlement + stake manager):
  - runs=256, depth=500 → 128,000 calls, 0 reverts, 6/6 invariants PASS
    (eth conservation, escrow obligations = balance, stake obligations =
    balance, stake token conservation, batch holds no value, live escrow
    funding identity) + full-lifecycle conservation test PASS.
  - Deepened run (FOUNDRY_INVARIANT_RUNS=512, FOUNDRY_INVARIANT_DEPTH=500):
    **256,000 calls, 0 reverts, 6/6 invariants PASS**, 519.67s wall
    (log: `invariant-deep.log` in this worktree). Two suites matched the
    CrossContract pattern (the conservation invariant suite + the lifecycle
    suite): 3 tests passed / 0 failed total.

## Static analysis — Slither 0.11.6 (installed scratch venv; solc 0.8.30, via-ir, optimizer 200)

Coverage: all 11 frozen contracts analyzed individually + a combined unit
(Auction, EscrowManager + interface, OBS, StakeSlash, ScopedPausable).
Raw detector totals across per-contract runs: 117 results, ALL triaged
below. Zero untriaged findings.

### Waiver log (every finding, triaged)

| # | Detector / impact | Location | Triage |
|---|---|---|---|
| W-01 | reentrancy-eth, High ×2 | ExecutionEscrowManager._resolveDisputeSlash → _payout | FALSE POSITIVE (mitigated). All escrow state is finalized BEFORE any payout: state=Finalized, disputeActive=false, stakes/deposits zeroed, dispute deleted (L340-346 precede payouts L347-351). _payout's only post-call write (pendingWithdrawals += amount) fires ONLY when the call FAILED, i.e. no value was delivered; reentry during a failing receive hits finalized state and reverts, and a reentrant withdraw() pays only the caller's own previously-queued balance once (zeroed before its call). Accounting stays exact. Defense-in-depth note: contract carries no ReentrancyGuard; adding nonReentrant to the resolve paths is a cheap hardening for a future TDD dispatch — not required to close this finding. |
| W-02 | reentrancy-balance, High ×1; reentrancy-no-eth, Medium ×1 | MoebiusXSessionAccount.executeAsSession | FALSE POSITIVE as an exploit. Function is nonReentrant; cap checks are post-call but ATOMIC — any per-call/cumulative breach reverts the entire call including the token transfer. Cross-function writers (grant/revoke/allowCall) are onlyOwner; the session key is not the owner. Design note recorded: outflow accounting trusts spendToken.balanceOf deltas — sound for the owner-configured spend token (USDC/Base in the MoebiusX deployment); a fee-on-transfer or rebasing token must never be configured as spendToken. |
| W-03 | incorrect-exp, High ×1; divide-before-multiply, Medium ×9 | OpenZeppelin Math.sol (dependency of SincorSessionValidator) | FALSE POSITIVE — dependency code, not SINCOR source. `^` in mulDiv is intentional XOR inside Hensel/Newton-Raphson inversion; divide-before-multiply hits are the published OZ mulDiv/invMod algorithms. Excluded from SINCOR findings; listed so the count is honest. |
| W-04 | incorrect-equality, Medium ×2 | ExecutionEscrowManager._payout (amount == 0), withdraw (amount == 0) | FALSE POSITIVE. Strict equality against zero on an exact wei amount is the intended guard, not a balance/price comparison. |
| W-05 | timestamp, Low ×22 | Auction, StakeSlash, KYA, TaskEscrow, SessionValidator, MoebiusX | ACCEPTED DESIGN. Deadlines/windows are hour-to-day scale; Base sequencer timestamp drift (seconds) cannot move an outcome. Same class as forge lint block-timestamp warnings. |
| W-06 | reentrancy-benign ×1, reentrancy-events ×4, Low | ExecutionEscrowManager | FALSE POSITIVE / informational. Event-after-call ordering only affects log sequencing on the failure-queued path; no state depends on it. |
| W-07 | missing-zero-check, Low ×3 | OptimisticBatchSettlement ctor (treasury_, operator_); MoebiusXSessionAccount ×1; SincorPaymaster ×1 | ACCEPTED, fix recommended (trivial). Constructor-time, deployer-supplied addresses; the deploy ceremony verifies addresses before broadcast. Adding zero checks is a safe one-line hardening for a future dispatch. |
| W-08 | low-level-calls, Informational ×6 | StakeSlashManager._safeTransfer/_safeTransferFrom; MoebiusX | ACCEPTED. Deliberate safe-transfer wrappers that check return data / success flag; required for non-standard ERC20 compatibility. |
| W-09 | naming-convention ×6, immutable-states ×4, pragma/solc-version ×7, assembly ×38, too-many-digits ×4 — Informational/Optimization | across core + OZ deps | COSMETIC. No security effect. immutable-states suggestions (owner, operator) noted for a future gas pass. |

Slither totals by run: combined unit 39 (2 High, 2 Medium, 21 Low, 12
Info, 2 Opt); KYARegistry 1 Low; SincorPaymaster 3 (1 Low, 1 Info, 1 Opt);
SincorTaskEscrow 20 (3 Low, 16 Info, 1 Opt); SincorSessionValidator 44
(1 High + 9 Medium — ALL inside OZ Math.sol, W-03; rest Info/Low);
MoebiusXSessionAccount 8 (1 High, 1 Medium — W-02; rest Low/Info);
ScopedPausable 2 Info.

## Verdict

- No real bug was found by this campaign. No contract code was changed.
- The frozen set is **exhaustively fuzz-verified (50k runs where fuzz
  tests exist), invariant-verified (funds conservation across the money
  path), and statically triaged (zero untriaged Slither findings)**.
- It is NOT formally verified. Remaining for that claim: a symbolic /
  theorem-proving pass (e.g. Certora/Halmos-style proofs of the
  conservation invariants), fuzz suites for escrow/session/paymaster,
  the W-01/W-07 defense-in-depth hardenings, and an independent human
  audit. Per Execution Standard v2, none of that may be marketed as done.
