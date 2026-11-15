# CONTRACTS MANIFEST — SINCOR2 Solidity workspace

Generated 2026-11-14 on branch `feat/overhaul-contracts` (base `45f34309`) for the
A+ repo overhaul. **No production `.sol` file was moved or modified** — this
manifest is the founder-approved alternative to physical relocation, because
moving sources breaks Foundry `src` roots and remappings.

**Post-integration update (2026-11-14):** the wave-1 classification was taken on
the side-branch tree at `45f34309`, which was missing files that main carries.
After the rebase onto main `4fd2c248`, the full recount
(`find . -name '*.sol' -not -path './node_modules/*' -not -path './lib/*'`) is
**157 files, not 126**. All 31 previously uncounted files are classified below
and every count in this document has been updated. Compiler pins on this tree
also differ from the side branch: root `foundry.toml` sets `solc = "0.8.30"`,
and `onchain/foundry.toml`'s default profile sets `solc = "0.8.28"` (a separate
profile pins the vendored `tools/solc-0.8.26` binary path).

## Workspace layout (two Foundry projects)

| Project | Root | `src` | `test` | solc (foundry.toml) | Pragmas in sources |
|---|---|---|---|---|---|
| Root (A2A auction / escrow / session stack) | `./foundry.toml` | `contracts/` | `test/` | **0.8.30** | `^0.8.24`, `^0.8.27` |
| Onchain (Uniswap v4 / MoebiusX stack) | `onchain/foundry.toml` | `onchain/src/` | `onchain/test/` | **0.8.28** (default profile; vendored `tools/solc-0.8.26` binary pin in a separate profile) | `^0.8.24`–`^0.8.26` |

Build state at baseline (pristine `45f34309`, verified by Builder B):
root `forge build` FAILS (missing vendored `lib/` — forge-std / OpenZeppelin /
v4-core / permit2 / uniswap-hooks are not in the worktree; with forge-std +
OpenZeppelin copied in locally it still fails on the v4 imports and on the
OZ v4-era path `@openzeppelin/contracts/security/ReentrancyGuard.sol` used by
`contracts/hooks/IntentHook*.sol`). `onchain` `forge build` FAILS the same way
(no `onchain/lib/`). No `.sol` file was moved in this overhaul for exactly
this reason: relocation cannot be verified green until the vendoring gap is
closed (founder action item, see completion report).

## Live Base mainnet contracts (addresses repo-sourced ONLY)

Sources: `CANONICAL_ADDRESSES.md` and `onchain/deployments/base-8453.json`.
No address below was invented; contracts without a repo-recorded address are
listed as **not deployed** where the repo itself says so.

| Contract | Source file | Base mainnet address | Basescan | Repo source of address |
|---|---|---|---|---|
| SharedLiquidityVault | `onchain/src/SharedLiquidityVault.sol` | `0xeA90a257e5Dae20a0472C4812775F28614459bb6` | https://basescan.org/address/0xeA90a257e5Dae20a0472C4812775F28614459bb6 | `onchain/deployments/base-8453.json` (LIVE, deployed 2026-07-19) |
| SharedLiquidityHook | `onchain/src/SharedLiquidityHook.sol` | `0x5A20BfEc6Caa3A94246eCCCb36F27F4980152dC0` | https://basescan.org/address/0x5A20BfEc6Caa3A94246eCCCb36F27F4980152dC0 | `base-8453.json` (LIVE — staging hook, production pool attachment pending) |
| SincLimitOrderHook | `onchain/src/SincLimitOrderHook.sol` | `0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0` | https://basescan.org/address/0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0 | `CANONICAL_ADDRESSES.md` (marked Legacy) |
| SincGenesisNFT | `onchain/src/SincGenesisNFT.sol` | `0xF3Bd56788b5E56DE638AF5dDffFA478838A68d09` | https://basescan.org/address/0xF3Bd56788b5E56DE638AF5dDffFA478838A68d09 | `CANONICAL_ADDRESSES.md` (marked Legacy) |
| AXM (AXIOM) token | *no `.sol` source among the 157 files* | `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a` | https://basescan.org/address/0x4c3fb66f14fbaa2088c9ae91017ba770da53715a | `CANONICAL_ADDRESSES.md` (A2A settlement token) |
| SINC token | *no exact source file asserted — see `contracts/legacy/SINC_v3.sol` (archive-candidate, lineage unproven)* | `0xe1D836087F6573b665d25CE088793E916D7892f8` | https://basescan.org/address/0xe1D836087F6573b665d25CE088793E916D7892f8 | `CANONICAL_ADDRESSES.md` |

Treasury `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` (CANONICAL_ADDRESSES.md /
base-8453.json) is a wallet, not a repo contract.

**Not deployed (repo-stated):** `CommitRevealAuction` + `ExecutionEscrowManager`
— `src/sincor2/a2a_inbound_market.py` states "The Solidity
CommitRevealAuction is not yet deployed". They are production protocol
sources awaiting the founder-gated deploy ceremony
(`docs/deployment/AUCTION_DEPLOY_CEREMONY.md`, `scripts/deploy_auction_contracts.py`).

## Classification summary (157 `.sol` files, `lib/` excluded — `lib/` is gitignored and vendored locally)

| Class | Count |
|---|---|
| production (core protocol sources) | 49 |
| library / interface | 9 |
| test (incl. mocks, invariant, fork tests) | 62 |
| sandbox / experimental (incl. proposals) | 19 |
| archive-candidate (already under `contracts/legacy/`) | 3 |
| deployment script | 15 |
| **Total** | **157** |

## Full file classification

### `contracts/` — root project sources (21)

| File | Class | Notes |
|---|---|---|
| `contracts/CommitRevealAuction.sol` | production | Sealed-bid core; NOT deployed (see above) |
| `contracts/ExecutionEscrowManager.sol` | production | Execution/settlement layer; NOT deployed |
| `contracts/IExecutionEscrowManager.sol` | library/interface | Interface for the escrow manager |
| `contracts/OptimisticBatchSettlement.sol` | production | Merkle-root batch settlement; `CHALLENGE_BLOCKS = 300` on this tree |
| `contracts/StakeSlashManager.sol` | production | Stake deposits + adjudicator slashing |
| `contracts/escrow/SincorTaskEscrow.sol` | production | ERC20 task escrow (reward + bond) |
| `contracts/hooks/BribeLoopHook.sol` | sandbox | Experimental v4 hook; tests use mock addresses only |
| `contracts/hooks/IntentHook.sol` | sandbox | Imports OZ v4-era `security/ReentrancyGuard.sol` — breaks root build |
| `contracts/hooks/IntentHookV2.sol` | sandbox | v4 imports + cross-project import of `onchain/src/interfaces/IAccountingHub.sol` — breaks root build |
| `contracts/kya/KYARegistry.sol` | production | KYA attestation pointer registry |
| `contracts/legacy/SINCBondingCurve.sol` | archive-candidate | Legacy; already in `legacy/` |
| `contracts/legacy/SINCPlatformAccess.sol` | archive-candidate | Legacy; already in `legacy/` |
| `contracts/legacy/SINC_v3.sol` | archive-candidate | Legacy; already in `legacy/` |
| `contracts/paymaster/SincorPaymaster.sol` | production | ERC-4337 paymaster (Base 8453) |
| `contracts/security/ScopedPausable.sol` | library | Abstract guardian-pause + sunset base; `SUNSET_DURATION_BLOCKS = 7_776_000` on this tree |
| `contracts/security/SincorSessionValidator.sol` | production | ERC-7579-style session validator |
| `contracts/erc8004/SincorValidationReputationOracle.sol` | production | ERC-8004-style (draft-spec) validation + reputation oracle driven by SIGIL settlements; dedicated test at `test/SincorValidationReputationOracle.t.sol` |
| `contracts/modules/SigilPolicyModule.sol` | production | ERC-7579 SIGIL policy module / hook; dedicated test at `test/SigilPolicyModule.t.sol` |
| `contracts/modules/SincorSettlementHub.sol` | sandbox | Ambiguous → sandbox: the file's own header marks it a founder spec-drop REFERENCE DRAFT, UNVERIFIED and NOT deployed; it is the interface reference for SigilPolicyModule, not a live contract |
| `contracts/security/MoebiusXSessionAccount.sol` | sandbox | Ambiguous → sandbox: the file's own header marks it a PROTOTYPE (testnet/sim only), explicitly NOT a production account (production uses ZeroDev Kernel) |
| `contracts/sla/SLABondManager.sol` | production | SLA bond manager; dedicated tests at `test/SLABondManager.t.sol` (+ adversarial / fix-review suites) |

### `test/` — root project tests (15)

All class **test**: `test/BribeLoopHook.t.sol`, `test/CommitRevealAuction.t.sol`,
`test/MoebiusXSessionAccount.t.sol`, `test/SLABondAdversarial.t.sol`,
`test/SLABondFixReview.t.sol`, `test/SLABondManager.t.sol`,
`test/SigilPolicyModule.t.sol`, `test/SincorEscrow.t.sol`,
`test/SincorOracleAdversarial.t.sol`, `test/SincorPaymaster.t.sol`,
`test/SincorSessionValidator.t.sol`,
`test/SincorValidationReputationOracle.t.sol`,
`test/StakeSlashManagerUnstakeClamp.t.sol`, `test/SunsetExpiryBoundary.t.sol`,
`test/invariant/TaskEscrowConservation.invariant.t.sol`.

(`CommitRevealAuction.t.sol`, `SunsetExpiryBoundary.t.sol`, and
`invariant/TaskEscrowConservation.invariant.t.sol` were added in this overhaul;
the other 12 were present on main but missing from the side-branch tree the
first classification was taken on.)

### `onchain/src/` — onchain project sources (48)

Production (38): `AccountingHub.sol`, `Axiom.sol`, `ComplianceGuard.sol`,
`LiquidityAmplifierHook.sol`, `RehypothecationAdapter.sol`, `SINCLending.sol`,
`SharedLiquidityHook.sol` (live, staging), `SharedLiquidityVault.sol` (live),
`SincBondingCurve.sol`, `SincGenesisNFT.sol` (live, legacy), `SincLimitOrderHook.sol` (live, legacy),
`SincPriceOracle.sol`, `SincSwapRouter.sol`, `fluid/SincFluidAdapter.sol`,
`morpho/SincChainlinkOracle.sol`, `morpho/SincMorphoSetup.sol`,
`morpho/SincStaking.sol`, `p23/CollectionRegistry.sol`,
`p23/FractionalVault.sol`, `p23/NftPricingOracle.sol`, `p24/BondingCurve.sol`,
`p24/ContentPolicyGuard.sol`, `p24/CreatorTokenFactory.sol`,
`p24/FeeSplitDistributor.sol`, `p24/RevenueAccrual.sol`,
`sincor/AgentStakeSlash.sol`, `sincor/EpochSessionKeyValidator.sol`,
`sincor/MemoryBoundSettlement.sol`, `sincor/Sinc4626Vault.sol`,
`sincor/SincFeeFlywheel.sol`, `sincor/SincGovernanceController.sol`,
`sincor/SincPSM.sol`, `sincor/SincRiskModule.sol`,
`sincor/SincSovereignRevenueRouter.sol`, `sincor/SincStrategyRouter.sol`,
`sincor/SincVaultFactory.sol`, `sincor/SincConstants.sol` (constants library
in the production group), `hooks/OLTWAMMIHook.sol` (self-described production
v4 TWAMM-internalization hook; dedicated test at
`onchain/test/OLTWAMMIHook.t.sol`; no deployment address recorded in the repo).

Library / interface (7): `fluid/FluidInterfaces.sol`,
`interfaces/AggregatorV3Interface.sol`, `interfaces/IAccountingHub.sol`,
`interfaces/ISharedLiquidityVault.sol`, `interfaces/ISincLoop.sol`,
`interfaces/ISincLoopInfra.sol`, `interfaces/ISincVaultSystem.sol`.

Sandbox (3, duplicated under `onchain/proposals/hooks/`):
`hooks/AutoCapitalizeMonetizeHook.sol`, `hooks/MoebiusMEVHook.sol`,
`hooks/PhantomCreditToken.sol`.

### `onchain/test/` — onchain project tests (39)

All class **test**: `AutoCapitalizeMonetizeHook.t.sol`,
`ComplianceGuard.t.sol`, `EpochSessionBinding.t.sol`, `Integration.t.sol`,
`LiquidityAmplifierHook_Integration_Tests.t.sol`, `MoebiusMEVHook.fork.t.sol`,
`MoebiusMEVHook.t.sol`, `OLTWAMMIHook.t.sol`, `P24AccessControl.t.sol`, `P24Deploy.t.sol`,
`SINCLending.Decimals.t.sol`, `SINCLending.t.sol`,
`SharedLiquidityHook.t.sol`, `SharedLiquidityVault.t.sol`,
`SincBondingCurve.Graduation.t.sol`, `SincBondingCurve.Math.t.sol`,
`SincBondingCurve.NFTMint.t.sol`, `SincBondingCurve.NoRug.t.sol`,
`SincBondingCurve.Referral.t.sol`, `SincFluidAdapter.fork.t.sol`,
`SincFluidAdapter.t.sol`, `SincFlywheel.t.sol`, `SincGenesisNFT.t.sol`,
`SincLimitOrderHook.AntiSandwich.t.sol`, `SincLimitOrderHook.t.sol`,
`SincLoopFork.t.sol`, `SincPriceOracle.t.sol`,
`SovereignRevenueRouter.t.sol`,
`invariant/SharedLiquidityVault.invariant.t.sol`,
`mocks/MockAeroExecutionAdapter.sol`, `mocks/MockChainlinkFeed.sol`,
`mocks/MockERC20.sol`, `mocks/MockFluid.sol`, `mocks/MockLoop.sol`,
`mocks/MockSanctionsOracle.sol`, `mocks/MockSearcher.sol`, `mocks/MockSinc.sol`,
`mocks/MockStrategy.sol`, `mocks/MockSwapRouter.sol`.

### `onchain/script/` — deployment scripts (15)

All class **deployment script**: `01_DeployGenesisNFT.s.sol`,
`04_MineHookAddress.s.sol`, `05_DeployHook.s.sol`, `06_DeployAxiom.s.sol`,
`07_DeployP24.s.sol`, `BindEpochValidatorToHooks.s.sol`, `Deploy.s.sol`,
`DeployEpochValidatorStack.s.sol`, `DeployLiquidityAmplifierHook.s.sol`,
`DeployMoebius.s.sol`, `DeploySharedLiquidityHook.s.sol`,
`DeploySincFluidAdapter.s.sol`, `DeploySovereignRevenueRouter.s.sol`,
`InitializeMoebiusPool.s.sol`, `SmokeTest.s.sol`.

### `onchain/proposals/` — sandbox (10)

Sandbox hooks (7): `hooks/AutoCapitalizeMonetizeHook.sol`,
`hooks/BaseLendingLoopHookContainer.sol`, `hooks/LendingLoopHookFactory.sol`,
`hooks/LoopAmplifyHook.sol`, `hooks/MoebiusMEVHook.sol`,
`hooks/PhantomCreditToken.sol`, `hooks/YieldWrapLendingHook.sol`.
Sandbox scripts (2): `script/07_DeployLiquidityAmplifierHook.s.sol`,
`script/DeployProfitHooks.s.sol`.
Test (1): `test/LendingLoopHooks.t.sol`.

### `factoring/` — standalone SealFactoring project (6)

A third, standalone Foundry project (`factoring/foundry.toml`), missed entirely
by the side-branch classification.

| File | Class | Notes |
|---|---|---|
| `factoring/contracts/SealFactoring.sol` | sandbox | Ambiguous → sandbox: `factoring/foundry.toml` itself calls this the "Lane-4 SealFactoring prototype", deliberately separate from both main Foundry projects |
| `factoring/test/SealFactoring.t.sol` | test | |
| `factoring/test/SealFactoringAdversarial.t.sol` | test | |
| `factoring/test/SealFactoringFixReview.t.sol` | test | |
| `factoring/test/SealFactoringRound2.t.sol` | test | |
| `factoring/test/SealFactoringRound2Review.t.sol` | test | |

### `packages/sincor-x402/tests/fixtures/` — SDK test fixtures (2)

| File | Class | Notes |
|---|---|---|
| `packages/sincor-x402/tests/fixtures/MockUSDC.sol` | test | File header: TEST FIXTURE, local test chains only, never deploy |
| `packages/sincor-x402/tests/fixtures/SincorSettlementHub.sol` | test | File header: TEST COPY vendored for cross-implementation tests only, do not deploy (test copy of the sandbox Hub draft above) |

### `src/sincor2/` — stray Solidity source in the Python package (1)

| File | Class | Notes |
|---|---|---|
| `src/sincor2/SincorsettlementEngine.sol` | sandbox | Ambiguous → sandbox: a standalone 79-line settlement-engine prototype (pragma `0.8.28`) sitting outside both Foundry `src` roots, referenced only by a 2026-10-09 builder plan; the production settlement source is `contracts/ExecutionEscrowManager.sol` |

## Compiler / verification notes

- Root project: `solc = "0.8.30"` in `foundry.toml` (sources pragma `^0.8.24` /
  `^0.8.27`). Onchain project: `solc = "0.8.28"` in the default profile of
  `onchain/foundry.toml` (a separate profile pins the vendored
  `tools/solc-0.8.26` binary). Factoring project: standalone profile in
  `factoring/foundry.toml`.
- Basescan verification links exist above only for the four repo-addressed
  source contracts; token addresses link to the token contracts themselves.
- Constants on THIS tree (do not confuse with stacked-PR branches):
  `OptimisticBatchSettlement.CHALLENGE_BLOCKS = 300`,
  `ScopedPausable.SUNSET_DURATION_BLOCKS = 7_776_000`.
