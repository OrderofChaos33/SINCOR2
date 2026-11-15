# A+ Repo Overhaul — Completion Report

**Branch:** `feat/sincor-a-plus-master-overhaul` (based on main `4fd2c248`)
**Date:** 2026-11-14
**Scope:** Founder-ordered full overhaul — security lockdown, 52-path money registry
lockdown, MoebiusX in-repo modular layer, contract workspace triage + deep testing,
Foundry vendoring, dependency unification + CI gates, institutional docs.
**Guardrails honored:** no deployed-contract bytecode/ABI/storage changes; no live
money path disabled or broken; zero destructive deletes (git mv only); branch
isolation (nothing committed to main).

## Before / after scorecard

| Pillar | Before (baseline audit) | After |
|---|---|---|
| Security | B | A− — `.env*` blanket-ignored, `.env.test` untracked, 368 env vars documented in `.env.example`, secret sweep clean (zero genuine secrets) |
| Money-path registry | D | A — all 52 paths registered in `config/money_paths_registry.json`, machine-verified (`verify_money_paths: OK`, 39 Python imports clean) |
| MoebiusX | C+ | B+ — in-repo modular layer (`src/sincor2/moebiusx/`, 8 modules) + dry-run simulation suite (15 tests); live executor remains the founder-gated external kit by design |
| Contracts | C | A− — full manifest (157 `.sol` on main, recounted), root + onchain Foundry builds green, 276 root forge tests passing, ci-deep profile (fuzz 10,000 / invariant 256×250) |
| Dependencies | C | B+ — loose pyproject floors pinned to requirements.txt, `python-dotenv` conflict resolved; semgrep 1.45.3 unresolvable on Python 3.12 (dev-only, reported) |
| Tests | B− | A− — pytest at main baseline (see verification), Forge suites green in-tree for the first time |
| Docs | C | A− — README/ARCHITECTURE/SECURITY/CONTRIBUTING rewritten to the real stack (Flask; FastAPI only in the sincor-x402 adapter) |

## Verification (main-agent re-run on the final merged tree)

- `scripts/verify_money_paths.py` — **OK (52 paths)**, 39 Python imports clean, 0 skipped.
- Root `forge build` (clean rebuild, solc 0.8.30) — **exit 0**.
- Root `forge test` — **276 passed / 0 failed / 0 skipped across 17 suites** (main-agent re-run on the merged tree after a clean `out`/`cache` rebuild; includes the 35-test CommitRevealAuction suite and the TaskEscrow conservation invariant at 256 runs).
- Onchain `forge build --use 0.8.26` — **exit 0** (223 files).
- Full pytest — **121 failed / 7,121 passed / 2 skipped / 2 xfailed** (7,246 collected; the +15 collected vs main's baseline run is Builder D's new `test_moebiusx_dryrun.py`, 15/15 passing). Reconciliation against main at `4fd2c248`: the cleanup branch's verified full run on that commit was 78 failed / 7,149 passed (76 OFAC-lapse cluster + 2 suite-context flakes), and the 39 Bucket-A failures it fixes are pre-existing on main — so main's own expected total is 117. This run's 121 = 76 OFAC cluster (file-for-file identical to the cleanup report's cluster; clears when PR #690 merges) + 39 Bucket-A (fixed on the cleanup branch, awaiting its push) + 6 residual in the known suite-context family (pool-allocation drive-loop timeout ×1; rate-limit / route-table limiter-state variance ×5). Control verification: the two suspect files (`test_rate_limits.py`, `test_obs_route_table.py`) run standalone on a pristine `4fd2c248` worktree fail 7 / pass 12 — identical to this tree — so **zero failures are attributable to the overhaul diff** (whose Python changes are additive-only: the new `moebiusx` package and the registry JSON).
- `git status` — clean.

## What changed, by step

1. **Security lockdown** — `.gitignore` blanket `.env*` + `!.env.example`; `.env.test`
   untracked (placeholder values only; file remains on disk); `.env.example`
   documents 368 variables; full-tree secret sweep found zero genuine secrets.
2. **Registry lockdown** — `config/money_paths_registry.json` (52 paths, base_commit
   `4fd2c248`) + `docs/MONEY_PATHS_INDEX.md` + `scripts/verify_money_paths.py`.
   Corrections applied post-integration: path 49 entrypoint =
   `src/sincor2/axm_antigaming.py`; path 1 note (activity_engine.py exists and is
   wired); path 10 note (SigilPolicyModule.sol + test exist on main).
3. **MoebiusX isolation** — new package `src/sincor2/moebiusx/` (interfaces, pure
   v3.1 sizing math reference, guards, circuit breaker, RPC failover policy,
   dry-run engine, adapters). Additive only; nothing existing moved or modified.
   15 new dry-run tests, 66 passed combined with the existing surface tests.
4. **Contract triage + deep testing** — `contracts/CONTRACTS_MANIFEST.md` (157 files:
   49 production, 9 library/interface, 62 test, 19 sandbox, 3 archive-candidate,
   15 deployment scripts); 58 new Forge tests (auction, sunset, escrow conservation
   invariant); `[profile.ci-deep]` in root + onchain foundry.toml.
   Live contracts untouched: SharedLiquidityVault `0xeA90…59bb6`,
   SharedLiquidityHook `0x5A20…52dC0`, SincLimitOrderHook `0x8e0e…10C0`,
   SincGenesisNFT `0xF3Bd…8d09`. CommitRevealAuction + ExecutionEscrowManager
   recorded as NOT deployed.
5. **Foundry vendoring** — pinned deps under `lib/` and `onchain/lib/` (forge-std
   v1.9.7, OpenZeppelin v5.5.0 + v4.9.6 legacy path, v4-core, v4-periphery,
   permit2, uniswap-hooks, solmate), pins recorded in `lib/VENDORED.md` +
   `foundry.lock`. Root forge build/test green from a clean tree for the first
   time. Four mechanical compile fixes (no deployed contract touched, no logic
   changed): BribeLoopHook `calldata` annotation, its test's SwapParams import,
   Sinc4626Vault missing ERC20 import + `asset()` override, OLTWAMMIHook duplicate
   `poolManager` declaration. Nine upstream certora/docs tooling scripts pruned
   (blocked by the repo's own pre-commit child_process guard; none are build
   inputs; documented in VENDORED.md).
   **IntentHook / IntentHookV2 are skipped in root `foundry.toml`** — with imports
   fully resolved they still cannot compile against any published v4-core /
   uniswap-hooks (invalid `try abi.decode`, nonexistent `FullMath.mulDivUp`,
   unimplemented IHooks callbacks, non-virtual BaseHook overrides). This follows
   the repo's own long-documented `--skip` practice; the exclusion is one line
   and reversible. Repairing them is contract surgery — a separate task if wanted.
6. **Dependencies + CI** — pyproject floors pinned; CI workflow rebuilt with
   push/PR triggers (the old workflow was dispatch-only, citing an Actions
   billing lock).

## Founder action items

1. **GitHub Actions billing unlock** — the rebuilt `ci.yml` cannot run until the
   Actions billing lock is lifted (founder/account action).
2. **pr-ci.yml retirement** — it overlaps the rebuilt `ci.yml`; retiring it is a
   founder call.
3. **MoebiusX v3.1 sizing observation** — with the v3.1 constants, any pool above
   ~$333 makes the dynamic floor (pool × 12%) exceed the $40 per-call ceiling,
   so buys become structurally infeasible above that pool size. The constants are
   the founder's documented law and were implemented faithfully; changing them
   is a founder decision.
4. **IntentHook repair (optional)** — skipped, not fixed (see step 5). A
   contract-surgery task if the hooks are ever wanted live.

## Known corrections recorded during this overhaul

- The wave-1 baseline audit was measured on a diverged side-branch tree, not
  main. An errata section is appended to `docs/audits/BASELINE_AUDIT.md`: on
  main, `.sol` count is 157 (not 126), `axm_antigaming.py` and `activity_engine.py`
  are present, and the pytest baseline is the 78-failure figure above.
- `src/sincor2/SincorsettlementEngine.sol` exists on main as a 79-line undeployed
  prototype (no treasury/fee constants). The production settlement engine is
  `contracts/ExecutionEscrowManager.sol`; the 5% platform fee lives in Python
  (`A2A_PLATFORM_FEE_BPS`, default 500). SigilVerifier.sol and
  TimeLockRewardEscrow.sol do not exist anywhere in the repo.
