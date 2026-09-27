# SINCOR2 — Deep Specs for the 16 Expansion DeFi Builds

Companion to `docs/DEFI_PROJECTS_COORDINATION.md` (the 10 core builds with deep specs).
These 16 specs deliberately exceed that depth: every section carries numeric parameters,
named repo integration points, and testable acceptance criteria — a bidding agent must
know exactly what "done" looks like.

Auction task listings for all 26 builds live in `~/workspace/sincor2-auction-tasks/`
(24 tasks per build; descriptions reference these specs).

Projects: P01 Yield Aggregator Vault · P04 MEV Protection & Capture · P05 DeFi Risk Mutual ·
P06 Perp DEX Hedging Swarm · P08 RWA Tokenization Vaults · P10 Flash Loan Arbitrage Engine ·
P14 Prediction Market Automation · P16 Best-Execution DEX Aggregator ·
P17 On-Chain Options Protocol · P19 Decentralized Credit Underwriting ·
P20 DeFi Compliance Automation · P21 DAO Treasury Management ·
P22 Stablecoin Yield Maximizer · P23 NFT-Fi Liquidity Pools ·
P24 SocialFi Revenue Share · P25 Agent-Managed Portfolio.

---

---

## P01 — Yield Aggregator Vault (Deep Spec)

**Catalog line:** fee_bps=10 · risk_score=0.18 · target_apr=0.062 · live_blocked=false ·
gates=(dry_run_default, risk_budget, single_strategy_cap) · max_alloc_pct=1.00 · min_capital_usd=1.00
**Treasury:** `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` (canonical, from `src/sincor2/defi/catalog.py`)

**Grounding (verified 2026-09-27):** Morpho Blue is an immutable (~650-line) permissionless lending primitive; MetaMorpho vaults are ERC-4626 curators that allocate across Blue markets with per-market supply caps and curator/allocator roles; Gauntlet-curated vaults use a 4-of-7 Safe owner with a 259,200s (3-day) timelock. ERC-4626 first-deposit inflation is stopped by an OpenZeppelin `_decimalsOffset()` virtual-share offset (3–12; 6 recommended for 6-decimal underlyings). Hyperliquid funding settles hourly at 1/8 of the 8-hour formula, capped at 4%/h, paid on oracle price; taker 4.5 bps / maker 1.5 bps. Delta-neutral basis trades (Ethena model): long spot/LST + equal-notional short perp, collect funding; when funding turns negative the position bleeds and must unwind.

---

## 1. What/How

An ERC-4626 vault (`YieldAggregatorVault`) on Base that takes USDC deposits, allocates across three adapters behind a common `IStrategyAdapter` interface, harvests yield, and routes a 10 bps performance cut to Treasury. A Python agent rebalances once per 8-hour funding epoch. A delta-neutral sleeve deposits LST collateral, borrows the native asset, and holds an exact-matching perp short on Hyperliquid; it unwinds the instant the funding basis turns negative.

### Step-by-step flow

1. **Deposit.** User calls `deposit(uint256 assets, address receiver)`. Shares = `assets × totalSupply / totalAssets`, rounded DOWN (vault-favourable). Contract enforces `_decimalsOffset() = 6` (virtual 10⁶ shares) so the first-deposit inflation attack requires a donation ~10⁶× the victim's deposit. Minimum initial deposit: 100 USDC (1e8 base units). Emits `Deposit`, then the vault pulls USDC into its cash sleeve.
2. **Allocate.** The rebalancer agent's signed intent calls `allocate(strategyId, amount)`. On-chain checks: `strategyId` active, `strategyTvl[id] + amount ≤ cap[id] × vaultTvl` (single_strategy_cap gate), caller holds REBALANCER_ROLE, dry-run gate not tripped. Adapter receives USDC via `supply(amount)` and returns a position report (assets, APR, utilization).
3. **Strategy set (numeric caps, % of vault TVL):**
   - `MorphoAdapter` (id 1): supply into the Gauntlet-curated USDC MetaMorpho vault on Base (address pinned in `strategyConfig`, settable only via 48h timelock). **Cap 60%.** On-chain guard: refuse allocation if the target Blue market utilization > 90% (checked via adapter `canSupply()`).
   - `CashAdapter` (id 2): idle USDC buffer held in-vault. **Cap 100%, floor 10%** (liquidity buffer — withdraws never wait on external liquidity).
   - `SharedVaultAdapter` (id 3): delegates to `SharedLiquidityHook` vault (`onchain/src/SharedLiquidityHook.sol`, verified in repo). **Cap 30%.**
   - Sum check: allocations beyond caps revert; deposit when all yield adapters are full routes to cash with a `PartialFill` event + no revert of the deposit itself.
4. **Rebalance (every 8 hours, cron).** Agent `src/sincor2/defi/p01/rebalancer.py` pulls: (a) Morpho market APYs (Morpho API / subgraph), (b) Aave v3 Base APYs, (c) TOA yield forecasts. It recomputes target weights under the risk budget (deployed ≤ 85% TVL, cash ≥ 10%). It fires a rebalance **only if** allocation drift > 500 bps on any adapter **or** any adapter APR moved > 150 bps since last epoch (thrashing guard). In `dry_run_default` mode the agent writes intents to the audit log and exits — no transaction is ever built.
5. **Harvest + fee.** On every rebalance and unwind, the vault calls `accrueFees()`: realized yield = `totalAssets(now) − totalAssets(lastHarvest)` (floored at 0). Fee = realized_yield × 10 bps, minted as shares to the treasury address (`0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`). `sweepFees()` pushes the treasury's shares; transfer is wrapped in try/catch so a misbehaving treasury contract can never brick harvest. Python `fee_ledger.py` mirrors `FeeSwept` events for the dashboard.
6. **Delta-neutral hedge sleeve (coordination-spec loop).** `HedgeManager.sol` records: LST collateral deposited (e.g., cbETH), native borrowed (ETH), perp short notional. The Python `hedge_agent.py` opens the short via the Hyperliquid adapter, sized so **|net delta| ≤ 2% of sleeve NAV** (band checked every epoch and on any >3% spot move). Kill rules (all automatic):
   - Funding basis (annualized funding − borrow cost) < **−10 bps** for 2 consecutive epochs → instant unwind.
   - Margin ratio < **300% of maintenance margin** on the perp leg → unwind.
   - Unwind is one atomic sequence: close short (reduce-only IOC) → repay borrow → return LST to vault. Partial-fill fallback: if the short close fills < 95%, the agent retries with 50 bps slippage widening, max 5 attempts, then pages the guardian.
7. **Withdraw.** `withdraw/redeem` pulls from cash first, then adapters in reverse-priority order (Shared → Morpho). Round DOWN on assets-out. A circuit breaker pauses **deposits** (never withdrawals) if any adapter reports a loss > 200 bps of its allocation in one epoch.

### Key contracts / modules

| Component | Location | Role |
|---|---|---|
| `YieldAggregatorVault.sol` | `onchain/src/hooks/p01/` (new) | ERC-4626 core, share math, cap registry, fee accrual, roles |
| `IStrategyAdapter.sol` + 3 adapters | `onchain/src/hooks/p01/adapters/` (new) | Morpho / cash / shared-vault supply-withdraw + position reports |
| `HedgeManager.sol` | `onchain/src/hooks/p01/` (new) | LST collateral, borrow, short-notional ledger |
| `rebalancer.py`, `hedge_agent.py`, `fee_ledger.py`, `monitor.py` | `src/sincor2/defi/p01/` (new) | Agent loop, hedge execution, fee mirror, metrics |
| `yield_rebalancer.yaml`, `hedge_executor.yaml` | `agents/defi/p01/` (create) | Agent budgets, memory, alert thresholds |
| Deposit/fee plumbing | extends `onchain/src/SharedLiquidityHook.sol` (verified exists) | Shared liquidity patterns, non-bricking call wrappers |

### Actors

- **Depositors** — USDC holders; permissionless deposit/withdraw.
- **Rebalancer agent** (REBALANCER_ROLE) — computes and (post-dry-run-lift) executes allocation intents.
- **Hedge executor** — opens/closes perps via Hyperliquid adapter; holds no vault custody.
- **Guardian** (GUARDIAN_ROLE, founder multisig) — pause deposits, force unwind, lift the dry-run gate.
- **Curator/Allocator** — MetaMorpho-side roles for the underlying Morpho vault (external, Gauntlet-operated).

### Numeric parameter table (canonical for all tasks)

| Parameter | Value |
|---|---|
| Protocol fee | 10 bps on **realized yield only** |
| Target gross APR | 6.2% (0.062) |
| single_strategy_cap: MorphoAdapter | 60% of TVL |
| single_strategy_cap: SharedVaultAdapter | 30% of TVL |
| CashAdapter floor / cap | 10% / 100% |
| Rebalance cadence | 8h funding epoch; drift trigger 500 bps; APR-move trigger 150 bps |
| Hedge delta band | |net delta| ≤ 2% of sleeve NAV |
| Hedge kill: funding basis | < −10 bps annualized, 2 consecutive epochs → unwind |
| Hedge kill: margin | margin ratio < 300% of maintenance → unwind |
| Risk budget | ≤ 85% TVL deployed to yield adapters |
| First-deposit guard | `_decimalsOffset()=6`, min 100 USDC initial deposit |
| Morpho utilization guard | defer to cash if Blue market utilization > 90% |
| Subgraph staleness | reject allocation inputs older than 60 blocks |
| Circuit breaker | pause deposits if any adapter loss > 200 bps in one epoch |

---

## 2. Why

**Who pays:** USDC depositors pay implicitly — the 10 bps cut is taken from harvested yield before share-price accrual, exactly like a MetaMorpho performance fee. **Why they pay:** a retail depositor cannot rebalance across Morpho Blue markets, maintain a cash buffer, and run a delta-neutral funding sleeve every 8 hours; the vault does it for a fee that is ~1/25th of a typical 250 bps fund management fee. **Revenue path to Treasury:** every rebalance and unwind mints fee shares to `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`. At $10M TVL and 6.2% gross APR, annual harvested yield ≈ $620k; 10 bps of that ≈ **$620/year to Treasury**, scaling linearly with TVL. The fee_ledger mirror makes this auditable on the dashboard. **What breaks without this:** capital sits idle or in single-market exposure; basis-trade yield is inaccessible to non-technical users; SINCOR has no yield-venue revenue line.

---

## 3. Build Stack

- **Solidity 0.8.24**, OpenZeppelin Contracts v5: `ERC4626` (with `_decimalsOffset()=6` override), `AccessControl`, `ReentrancyGuard`, `SafeERC20`. No upgradeable proxies — immutable instances, mirroring Morpho Blue's immutability stance.
- **Foundry** for unit/invariant/fuzz tests (`forge-std`); fork tests against Base mainnet pinned block.
- **Python 3.11**, `web3.py`, `eth-tester`/`py-evm` for offline suites; cron via repo scheduler for the 8h epoch; Hyperliquid Python SDK for the perp leg (testnet adapter first).
- **Data:** Morpho public API + Aave v3 subgraph on Base for APYs; TOA forecast hook per repo convention.
- **Verified repo integration points:**
  - Reuse: `onchain/src/SharedLiquidityHook.sol` (base patterns), `src/sincor2/defi/yield_aggregator.py` (existing P01 Python module), `verticals/trading/yield_optimizer_agent.py` (agent pattern), `onchain/script/Deploy.s.sol` (deploy-script convention), `onchain/test/` (Foundry test convention), `scripts/` (ops scripts).
  - New: `onchain/src/hooks/p01/`, `src/sincor2/defi/p01/`, `agents/defi/p01/` (agent YAML dir does not exist yet — created by scaffold task), `docs/p01/`.
  - Noted gap: there is **no `sinax/` module in the repo**; SINAX references in P01 are limited to future attestation hooks, not current code.
- **Reused vs built new:** share accounting, access control, safe transfers, hook plumbing — reused (OZ + repo). Strategy adapters, cap registry, hedge sizing math, fee-on-yield-only accrual, 8h rebalancer with dry-run gate — built new.

---

## 4. Tip

- **Harvest sandwich:** an attacker can deposit right before `accrueFees()` and withdraw right after, capturing fee-diluted share price moves. Mitigate with a 1-hour harvest cooldown and by accruing fees only on rebalance/unwind (predictable, agent-timed), not on a public callable.
- **Morpho liquidity illusion:** a market can show high APY with 99% utilization — supply would strand capital. The 90% utilization guard plus the 10% cash floor exists for this; monitor `canSupply()` every epoch.
- **Subgraph staleness:** Aave/Morpho subgraphs lag during incidents. Inputs older than 60 blocks are rejected and the agent holds the previous allocation (fail-static, never fail-random).
- **Funding-flip latency:** Hyperliquid funding is hourly; a violent flip can cost the sleeve 4%/h × notional. The 2-epoch confirmation on unwind is a deliberate trade-off — document it; the 300% margin buffer is what survives the gap.
- **Monitoring signals (must alert):** share price `convertToAssets(1e18)` drifting down > 50 bps/day; any adapter APR delta > 200 bps vs TOA forecast; hedge |delta| > 1.5% (pre-breach warning); basis crossing 0; `FeeSwept` events stopping while harvests continue.

---

## 5. Acceptance criteria

1. A 10% spot move on the LST leg changes net sleeve NAV by **< 0.5%** (|delta| band holds) in the hedge math test.
2. Fee accounting: a 6.2% APR harvest over one epoch routes **exactly 10 bps** of realized yield (to the wei) to the treasury address; zero-yield epochs mint zero fee shares.
3. Cap enforcement: any `allocate()` that would push an adapter above its cap **reverts**; a deposit when yield adapters are full routes to cash with a `PartialFill` event and never reverts the deposit.
4. Rebalance cadence: the agent emits an allocation intent (or a logged skip with reason) **every 8h epoch ±15 min**; in dry-run mode, zero transactions are built (verified by audit-log assertion).
5. Unwind: funding basis < −10 bps for 2 consecutive epochs → short closed, borrow repaid, LST returned within **6 blocks** of the second confirmation on fork.
6. First-deposit inflation: the known attack (1-wei deposit + large donation) cannot reduce a subsequent 100 USDC deposit's shares by more than **0.01%** (virtual-offset proof in the invariant suite).
7. No-loss invariant: `totalAssets() == Σ adapter reported assets + cash` holds under 10k fuzzed deposit/withdraw/allocate sequences (tolerance: rounding dust ≤ 1 wei per operation).

---

## 6. Risk gates

From catalog metadata — `live_blocked=false`, gates `dry_run_default`, `risk_budget`, `single_strategy_cap`:

- **dry_run_default:** the rebalancer and hedge agent start in intent-only mode. Lifting requires a founder-multisig transaction calling `setDryRun(false)`; the lift transaction hash is recorded in `docs/p01/`. Testnet deploys stay dry-run until the audit phase completes.
- **risk_budget:** ≤ 85% TVL in yield adapters, cash floor 10%, hedge sleeve ≤ 30% of TVL (inside the SharedVaultAdapter cap budget). Enforced on-chain, not just in the agent.
- **single_strategy_cap:** per-adapter caps (60/30/100) enforced in `allocate()` — the agent cannot exceed them even if compromised.
- **What stays dry-run:** all agent intents until multisig lift; mainnet position sizing.
- **What needs auditor + founder-signer path:** the dry-run lift, any cap change (48h timelock), curator key rotation, mainnet deployment of `YieldAggregatorVault` and `HedgeManager`.
- External auditor reviews the full `onchain/src/hooks/p01/` tree before mainnet; all High/Critical findings fixed with regression tests.

---

## 7. Phase definition of done

- **spec:** `docs/p01/ARCHITECTURE.md`, interface definitions, and `ECONOMICS.md` exist; every number in §1's parameter table is ratified; the spec passes the catalog-gate review (dry_run_default, risk_budget, single_strategy_cap each mapped to an enforcing contract/function); a competent builder could start core from the spec alone.
- **scaffold:** `onchain/src/hooks/p01/`, `src/sincor2/defi/p01/`, `agents/defi/p01/`, `docs/p01/`, and test stubs exist; stubs compile (`forge build`) and collect (`pytest`); agent YAML skeletons validate; CI lints pass on empty bodies.
- **core:** all contracts and agents implement §1 flows; the 8h rebalancer runs against fixtures producing valid allocation plans; the hedge sleeve holds |delta| ≤ 2% and unwinds on the kill rules; fees accrue at exactly 10 bps; every public function is reachable in tests.
- **testing:** unit + integration + 10k-run invariant/fuzz + pinned-block Base fork simulation all green; `docs/p01/FORK_SIM.md` records block number, per-step gas, and unwind latency in blocks; no open test failures.
- **audit:** `docs/p01/AUDIT.md` package frozen and tagged; external auditor sign-off recorded; all High/Critical findings remediated with regression tests; gas report shows ≥ 15% improvement on the rebalance hot path with zero behavior change (fuzz re-run green).
- **docs:** `TECHNICAL.md` (reimplementable from the doc), agent YAMLs finalized, `RUNBOOK.md` rehearsed in a dry run with logged timestamps, `monitor.py` dashboard panel rendering fork-sim data with alerts firing on the simulated funding flip.
- **deploy:** Base Sepolia deployment verified on Basescan with one full rebalance cycle executed and fees observed at the treasury address; `MAINNET_CHECKLIST.md` fully signed with no unchecked boxes before any mainnet action.

---

## P04 — MEV Protection & Capture (Deep Spec)

**Catalog line:** fee_bps=20 · risk_score=0.55 · target_apr=0.09 · live_blocked=true ·
gates=(flow_threshold, no_treasury_key) · max_alloc_pct=0.20 · min_capital_usd=100.00
**Treasury:** `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` (canonical, from `src/sincor2/defi/catalog.py`)

**Grounding (verified 2026-09-27):** Uniswap v4 hooks fire as `beforeSwap → [execute swap] → afterSwap`; `beforeSwap` returns `(bytes4, BeforeSwapDelta, uint24)` and `afterSwap` returns `(bytes4, int128)`; hook permissions are mined into the contract address via CREATE2. EIP-1153 transient storage (`TSTORE`/`TLOAD`) passes state across phases of a single transaction at ~100 gas. MEV flow today: searchers build bundles → builders construct blocks → relays run MEV-Boost → proposers select. Chainlink SVR proved the protocol-recapture pattern: auction the right to backrun an oracle update via Flashbots MEV-Share, split 65/35 protocol/oracle, with fallback to the standard feed on timeout. Base's PoolManager is `0x498581ff718922c3f8e6a244956af099b2652b2b`. Base runs a centralized sequencer (no PBS mempool), so L2 capture targets sequencer-visible flow and private-mempool backruns, not L1-style bundle auctions.

---

## 1. What/How

A Uniswap v4 hook (`MEVCaptureHook`, extending the repo's `MoebiusMEVHook`) that meters per-block swap flow in transient storage, detects sandwich/backrun signatures in `afterSwap`, and feeds a Python bidder agent that captures backrun value with a dedicated, proceeds-funded EOA. Captured value routes to Treasury with a 20 bps protocol cut. The treasury EOA is cryptographically and procedurally excluded from ever signing.

### Step-by-step flow

1. **Flow metering.** `beforeSwap` writes `(poolId, block.number, sender, sqrtPriceX96_before, amountSpecified)` to transient storage. `afterSwap` appends `(sqrtPriceX96_after, realizedDelta)`. Cost target: **≤ 5,000 gas** added per swap (transient writes only, no SSTORE on the hot path). A per-block flow ledger (transient, keyed by `(blockHash, poolId)`) accumulates notional per pool.
2. **Detection (in `afterSwap`, transient state only).** Two signatures:
   - **Sandwich:** within one block, pattern A→V→B where A and B share a sender (or funded-by relation), V is a large victim swap with price impact > **50 bps**, and B reverses A's direction. Flagged when attributable impact ≥ flow_threshold.
   - **Backrun:** a large price-moving swap (impact > 50 bps) followed in the same block by an arbitrage-shaped reversal capturing ≥ 30% of the displacement.
   - **flow_threshold gate (noise filter):** per-block attributable flow must exceed **$1,000 notional** AND 50 bps price impact before any capture intent is raised. Below threshold → logged as noise, never bid. False-positive budget: ≤ 5% on the historical fixture battery.
3. **Capture bidding (off-chain agent, `bidder.py`).** Consumes hook `FlowFlagged` events via RPC log subscription. For each flag it computes expected capture = estimated arb profit − gas − 3× gas safety margin (**bid shading rule: expected_net ≥ 3 × gas_cost**, else stand down). Submits a defensive backrun via a private-mempool path (Flashbots Protect-style on Base-compatible relays) from the **bidder EOA**, which is funded **only from previously captured proceeds** — initial float capped at 20% of tick capital (max_alloc_pct=0.20), minimum viable float $100 (min_capital_usd).
4. **Capture accounting.** Captured tokens land in the hook's capture vault (ERC-6909 claims on the PoolManager or plain ERC-20 escrow). `recordCapture(poolId, token, amount, estimateId)` credits the ledger. **Estimate-vs-actual reconciliation: |estimate − actual| / actual ≤ 1%**, else the event is flagged for review and the bidder's confidence weight is decayed.
5. **Routing to treasury.** 20 bps protocol fee on captured value + 100% of residual capture → **pull-based claims** by the treasury address. `claimCapture()` is non-bricking (try/catch per claimant). Worked example: capture of 1.0 ETH-equivalent → 0.002 ETH fee + 0.998 ETH residual to `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`.
6. **User protection rules (opt-in per order, via hookData flag).** Protected orders get: slippage guardrail (revert if execution price deviates > **100 bps** from the quoted price at submission), private-mempool routing hint, and automatic revert of the fill if the hook flagged the block as toxic before the user's swap executed. Target: **sandwich loss reduced ≥ 80%** vs unprotected baseline in fork replay.
7. **no_treasury_key enforcement (the hard boundary).** (a) `SignerRegistry` contract + Python config: the treasury EOA is on a deny-list checked at every signing call — any attempt reverts/raises. (b) Deployment script asserts the treasury address appears in **zero** signer fields. (c) CI job greps all configs/YAMLs for the treasury address in any key/signer field and fails the build on a hit. (d) The hook contains no `delegatecall`/`selfdestruct` paths. A dedicated adversarial test suite attempts treasury-key usage from every module and must fail closed.

### Key contracts / modules

| Component | Location | Role |
|---|---|---|
| `MEVCaptureHook.sol` | `onchain/src/hooks/p04/` (new; extends `onchain/src/hooks/MoebiusMEVHook.sol`, verified exists) | beforeSwap/afterSwap metering, transient flow ledger, capture vault |
| `IMEVCaptureHook.sol` | `onchain/src/hooks/` (new) | Capture accounting + distribution claim interface |
| `SignerRegistry.sol` | `onchain/src/hooks/p04/` (new) | Treasury-EOA deny-list, code-level |
| `bidder.py`, `flow_listener.py` | `src/sincor2/defi/p04/` (new) | Event consumption, capture estimation, private-mempool submission, nonce mgmt |
| `mev_bidder.yaml` | `agents/defi/p04/` (create) | Bidder budgets, flow_threshold, gas ceiling, key rotation |
| Reuse: `onchain/test/MoebiusMEVHook.t.sol`, fork test pattern | Existing | Detection fixtures, fork replay harness |

### Actors

- **Swappers/LPs** on hooked v4 pools — flow sources; protection is opt-in.
- **Bidder agent** (BIDDER_ROLE) — off-chain; the only signer of capture transactions; never the treasury EOA.
- **Guardian** (GUARDIAN_ROLE) — pauses detection/bidding, rotates bidder keys, tunes flow_threshold within ±50% bounds.
- **Treasury** — passive pull-claimant of captured value. Signs nothing, ever.

### Numeric parameter table (canonical for all tasks)

| Parameter | Value |
|---|---|
| Protocol fee | 20 bps on captured MEV → treasury |
| Target APR (on bidder float) | 9% (0.09) |
| flow_threshold | $1,000 notional/block AND 50 bps price impact |
| Price-impact flag | 50 bps victim displacement |
| Backrun capture bar | reversal captures ≥ 30% of displacement |
| Bid shading | expected_net ≥ 3 × gas_cost, else stand down |
| Estimate-vs-actual | ≤ 1% deviation |
| Bidder float cap | 20% of tick capital; min $100 float |
| Hot-path gas budget | ≤ 5,000 gas added per swap |
| Detection false-positive budget | ≤ 5% on historical fixtures |
| Protection target | sandwich loss −80% vs baseline (opt-in) |
| Protected-order slippage guardrail | 100 bps vs quoted price |
| live_blocked | **true** — live capture stays blocked until auditor + founder release |

---

## 2. Why

**Who pays:** MEV searchers/attackers effectively pay — the hook observes flow they'd otherwise extract for free and the bidder captures the backrun leg; the 20 bps cut comes out of captured value, not user funds. Protected users pay nothing extra (protection is a hookData flag). **Why it works:** Chainlink SVR already proved protocols can recapture oracle-update MEV via auction (65/35 split, fallback-safe); this generalizes the pattern to swap flow on v4 pools SINCOR hooks. **Revenue path to Treasury:** every capture routes 20 bps + residual to `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` via pull claims. At $50k/month captured flow (9% APR on a modest bidder float implies this order), Treasury sees ~$100/month in fees plus the full residual — the residual is the real line item, the fee is the accounting cut. **What breaks without this:** SINCOR's own pools leak value to external sandwich bots; users get worse fills; the MEV that SINCOR's flow creates accrues to strangers.

---

## 3. Build Stack

- **Solidity 0.8.24**, `v4-core` + `v4-periphery` (`BaseHook`), OpenZeppelin `ReentrancyGuard`, `SafeERC20`. EIP-1153 transient storage via inline assembly (`tstore`/`tload`) — 0.8.24 supports the opcodes post-Cancun.
- **Foundry** for unit/fuzz/fork tests; fork target Base mainnet, PoolManager `0x498581ff718922c3f8e6a244956af099b2652b2b`.
- **Python 3.11**, `web3.py` log subscriptions, `eth-tester` for offline suites; private-mempool submission via relay RPC (config-pluggable, Base-compatible).
- **Verified repo integration points:**
  - Reuse: `onchain/src/hooks/MoebiusMEVHook.sol` (detection flow data — the task set's stated base), `onchain/test/MoebiusMEVHook.t.sol` + `.fork.t.sol` (test harness), `onchain/script/Deploy.s.sol` (deploy convention), `scripts/` (ops).
  - New: `onchain/src/hooks/p04/`, `src/sincor2/defi/p04/`, `agents/defi/p04/`, `docs/p04/`, `scripts/p04/`.
- **Reused vs built new:** hook lifecycle plumbing, v4 pool interaction, fork-test harness — reused. Flow metering in transient storage, sandwich/backrun signature detection, capture vault accounting, proceeds-funded bidder with signer deny-list, estimate reconciliation — built new.

---

## 4. Tip

- **Base has no public PBS mempool.** L1-style bundle auctions don't apply; detection must work on sequencer-ordered flow and capture goes through private submission. Don't design for MEV-Boost relays — design for relay-compatible private submission on L2.
- **Reorg/idempotency:** key the flow ledger by block hash, not just number; on reorg, replay is a safe no-op (accounting keyed by `(blockHash, poolId, txIndex)`).
- **Detection griefing:** an attacker can spam sub-threshold swaps to pollute the ledger. The $1,000/50 bps threshold plus the ≤5% false-positive budget is the defense; monitor flag-rate spikes as a griefing signal.
- **Bidder EOA depletion:** the bidder stands down gracefully when float < gas reserve (2× the gas ceiling) — it must NEVER fall back to any treasury-adjacent key. Alert on depletion; refill only from captured proceeds or an explicit founder-signed top-up.
- **Monitoring signals (must alert):** flag rate > 3× 7-day median; estimate-vs-actual breach > 1%; any signing attempt with the treasury EOA (critical, pages immediately); bidder float below gas reserve; capture vault balance growing while claims stall.

---

## 5. Acceptance criteria

1. Detection battery on historical fixtures: known sandwich/backrun blocks flagged, organic-volatility blocks not flagged; **precision ≥ 90%, recall ≥ 80%**, false positives ≤ 5%.
2. Full attack-to-capture cycle on a local v4 fork: scripted sandwich → detect → bid → capture → route; treasury receipts equal **estimate ± 1%**, and the 20 bps fee is exact to the wei on a 1.0 ETH-equivalent capture.
3. no_treasury_key: adversarial suite attempts treasury-EOA signing from every module (hook, bidder, deploy script, config) — **all fail closed**; CI grep finds zero treasury-address occurrences in signer fields.
4. Protection: fork replay of protected vs unprotected orders shows **≥ 80% sandwich-loss reduction** for opted-in orders.
5. Gas: swap-path metering overhead **≤ 5,000 gas** per swap (before/after measured on fork).
6. Reorg safety: replaying a reorged block's flow events is a **safe no-op** — ledger balances unchanged, proven in fuzz.
7. Bidder economics: on fork fixtures, bidder net PnL (capture − gas) is positive and every bid satisfies the **3× gas shading rule**; sub-threshold flow produces zero bids.

---

## 6. Risk gates

From catalog metadata — `live_blocked=true`, gates `flow_threshold`, `no_treasury_key`, risk_score=0.55:

- **What stays dry-run / blocked:** the bidder NEVER submits live transactions until the live-block is released; testnet deploys run detection + simulated capture only. The `live_blocked` catalog flag is enforced in code: `bidder.py` refuses to build a real transaction unless `LIVE_RELEASE_FILE` exists and is signed by the founder key.
- **flow_threshold:** tunable by the guardian within ±50% of the $1,000/50 bps baseline; changes logged; raising it is always allowed, lowering requires the auditor-signed parameter change.
- **no_treasury_key:** the non-negotiable invariant — code deny-list + deploy assertion + CI grep + adversarial test suite. Any change touching signing paths re-runs the full adversarial suite.
- **Auditor + founder-signer path required for:** live-block release, bidder EOA key rotation, any capture-vault code change, treasury claim-address change (multisig + 48h timelock).
- The hook's capture vault is **not** a general fund: only `recordCapture` (hook) can credit it; only the treasury pull-claim can debit it.

---

## 7. Phase definition of done

- **spec:** `docs/p04/ARCHITECTURE.md` (MEV taxonomy, detection model, bidding module, capture routing), frozen interfaces, `ECONOMICS.md` with the worked sandwich-capture example; the trust-boundary diagram proves the treasury EOA is never a signer; spec ratifies flow_threshold and no_treasury_key with the numeric table from §1.
- **scaffold:** `onchain/src/hooks/p04/`, `src/sincor2/defi/p04/`, `agents/defi/p04/`, `docs/p04/` exist; stubs compile and collect; `mev_bidder.yaml` skeleton validates; CI lints pass.
- **core:** hook meters flow within the 5k gas budget and flags sandwich/backrun at the §1 thresholds; bidder captures scripted flow on fork with proceeds-only funding; 20 bps + residual routes to treasury via pull claims; no_treasury_key enforced at code, deploy, and CI layers; protection rules cut sandwich loss ≥ 80% on fixtures.
- **testing:** unit (24/24) + attack-to-capture integration + 10k-run invariant/fuzz (accounting identity, signer rejection, reorg idempotency) + pinned-block Base fork simulation with precision/recall and net-capture numbers in `docs/p04/FORK_SIM.md`; all green.
- **audit:** `docs/p04/AUDIT.md` frozen and tagged; threat model covers bidder-key compromise, detection griefing, accounting drift, trust-boundary breach; all High/Critical findings fixed with regression tests; gas report shows ≥ 15% hot-path improvement, fuzz re-run green.
- **docs:** `TECHNICAL.md` reimplementable; `mev_bidder.yaml` finalized; `RUNBOOK.md` (proceeds-only funding, key rotation, threshold tuning, key-compromise response) rehearsed with logged timestamps; `monitor.py` panel renders fork-sim data with trust-boundary alerts tested.
- **deploy:** Base Sepolia deployment verified; scripted MEV flow proves end-to-end capture with treasury receipts in `docs/p04/DEPLOY.md`; `MAINNET_CHECKLIST.md` signed with treasury-EOA exclusion re-verified at deploy; live-block stays engaged.

---

## P05 — DeFi Risk Mutual (Deep Spec)

**Catalog line:** fee_bps=25 · risk_score=0.48 · target_apr=0.11 · live_blocked=true ·
gates=(reserve_ratio, claim_window) · max_alloc_pct=0.15 · min_capital_usd=200.00
**Treasury:** `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` (canonical, from `src/sincor2/defi/catalog.py`)

**Grounding (verified 2026-09-27):** Nexus Mutual's proven mechanics: members pool capital → NXM staking backs covers → cover pricing blends actuarial inputs with staker risk-signalling (staking on a contract lowers its premium; coverage costs start under ~1% annually) → claims assessed by member/assessor voting with quorum rules (voting power must exceed 5× the cover amount in the original design; fraudulent voters face stake burns) → approved claims execute payout from the capital pool **without discretionary override**. Solvency is guarded by a Minimum Capital Requirement (MCR): no covers sell until pool value ≥ MCR, and the MCR ratchets up (capped ~1%/day) when the pool is over-capitalized. Later iterations (InsurAce, Unslashed, Risk Harbor) moved pricing and claims toward algorithmic/oracle validation. P05 keeps Nexus's mutual structure but replaces subjective member voting with **SINAX-attested claims** (machine-checkable loss proof + assessor attestation), and prices from a Python underwriter agent's risk scores.

---

## 1. What/How

A mutual pool contract (`RiskMutual.sol`) on Base: capital providers deposit stablecoins for mutual shares; cover buyers pay premiums for smart-contract-exploit cover on listed protocols; an underwriter agent publishes signed risk scores; claims require SINAX attestation plus assessor review inside a fixed claim window; payouts are pull-based with pro-rata haircuts if reserves are insufficient. 25 bps of every premium routes to Treasury at collection.

### Step-by-step flow

1. **Capitalize.** `depositCapital(uint256 amount)` mints mutual shares (ERC-4626-style accounting, `_decimalsOffset()=6`). Capital is locked against outstanding cover: `redeemCapital` reverts (or queues) to the extent that `reserves − lockedCover × reserve_ratio_floor` would go negative. **reserve_ratio gate: reserves ≥ 130% of outstanding cover obligations** — enforced on every `underwrite`, not just at setup.
2. **List a protocol.** Member governance vote (quorum: ≥ 10% of mutual shares voting, ≥ 60% yes) adds a protocol to the insurable list. Parameters (reserve_ratio, claim_window, premium base rate) change only via **48h timelock** after a passed vote.
3. **Price cover.** The underwriter agent (`underwriter.py`) computes a risk score 0..1 per protocol from: audit count/recency (30%), TVL and TVL concentration (20%), exploit history (25%), code-change velocity (15%), oracle/dependency risk (10%). Scores are EIP-712 signed, published on a 24h cadence, versioned. **Staleness guard:** scores older than 7,200 blocks (~24h on Base) block new covers.
   - Premium formula (annualized): `premium = base_rate × tier_multiplier(score) × cover_amount × (duration_days / 365)`
   - `base_rate = 200 bps`; tier multipliers: score ≤ 0.20 → 0.5× (100 bps floor); 0.20–0.50 → 1.0×; > 0.50 → 2.0×; hard cap **1,500 bps**.
   - Worked: $100k cover, 90 days, score 0.35 → 200 bps × 1.0 × $100,000 × 90/365 = **$493.15 premium**. 25 bps of that = **$1.23 to Treasury** at collection.
4. **Buy cover.** `buyCover(protocolId, coverAmount, durationDays)` collects `premium + 25 bps protocol fee` in one transfer; fee is forwarded to `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` immediately (non-bricking try/catch). Underwrite reverts if it would breach the 130% reserve ratio. Cover NFTs (ERC-721) record protocol, amount, start, expiry.
5. **File claim.** Within the **claim_window (30 days post-incident)**, the cover holder calls `fileClaim(coverId, attestation)`. The attestation bundle must contain: (a) SINAX-style machine proof of loss — on-chain balance-delta evidence of the exploit (verifier interface `ISinaxAttestationVerifier`; **note:** no `sinax/` module exists in the repo yet — the verifier ships as a whitelisted-assessor EIP-712 signature scheme with the interface reserved for the future SINAX module); (b) incident timestamp within the cover period. Late claims revert. Double-claims (same coverId + incident hash) revert.
6. **Assess.** ASSESSOR_ROLE reviewers vote; quorum follows the Nexus rule: **assessor voting weight must exceed 5× the cover amount**; 14-day review window; simple majority approves. Assessors who vote for a claim later proven fraudulent have their staked bond slashed (bond: 1,000 mutual-share units, set at appointment).
7. **Payout.** Approved claims are paid **pull-based**: `claimPayout(claimId)` transfers from reserves. If aggregate approved claims exceed free reserves, a **pro-rata haircut** applies: `payout_i = claim_i × reserves / Σclaims`, computed to the wei with a public `shortfallFactor`. A failed transfer never bricks other claimants (per-claimant try/catch + pull pattern).
8. **Capital-provider yield.** Underwriting surplus (premiums − payouts − fees) accrues to mutual share price. Target 11% APR at full, loss-free utilization; the spec documents the loss scenarios where this goes negative.

### Key contracts / modules

| Component | Location | Role |
|---|---|---|
| `RiskMutual.sol` | `onchain/src/hooks/p05/` (new) | Capital pool, cover inventory, reserve accounting, claims, payouts |
| `IRiskMutual.sol` | `onchain/src/hooks/` (new) | buyCover / fileClaim / assessClaim / redeemCapital |
| `ISinaxAttestationVerifier.sol` | `onchain/src/hooks/p05/` (new) | Attestation interface; initial impl = assessor EIP-712 allowlist |
| `underwriter.py`, `score_feed.py` | `src/sincor2/defi/p05/` (new) | Risk scoring, signed score publication, heartbeat |
| `risk_underwriter.yaml` | `agents/defi/p05/` (create) | Score cadence, data sources, alert thresholds |
| Reuse: `dae/governance.py` (vote pattern), OZ `TimelockController`, `ERC721` | Existing | Member votes, 48h timelock, cover NFTs |

### Actors

- **Capital providers** — deposit stablecoins, earn underwriting surplus, absorb haircuts.
- **Cover buyers** — pay premiums for exploit cover on listed protocols.
- **Underwriter agent** (UNDERWRITER_ROLE) — publishes signed risk scores; cannot move funds.
- **Assessors** (ASSESSOR_ROLE, bonded) — review claims; slashed for fraudulent approvals.
- **Guardian** (GUARDIAN_ROLE) — pauses underwriting/claims in emergencies; **cannot** block in-window claim payouts (pause explicitly excludes `claimPayout`).
- **Members** — mutual shareholders; vote listings and parameter changes.

### Numeric parameter table (canonical for all tasks)

| Parameter | Value |
|---|---|
| Protocol fee | 25 bps of premiums → treasury, at collection |
| Target APR (capital providers) | 11% (0.11), loss-free full utilization |
| reserve_ratio floor | 130% of outstanding cover obligations |
| claim_window | 30 days post-incident to file |
| Assessor review window | 14 days |
| Assessor quorum | voting weight > 5× cover amount |
| Assessor bond | 1,000 mutual-share units, slashable |
| Premium base rate | 200 bps annualized; cap 1,500 bps |
| Score tiers | ≤0.20 → 0.5×; 0.20–0.50 → 1.0×; >0.50 → 2.0× |
| Score staleness | > 7,200 blocks blocks new covers |
| Score cadence | 24h signed publication |
| Governance | 10% quorum, 60% yes; 48h timelock on param changes |
| max_alloc_pct / min_capital | 15% per cover line; $200 min pool capital |
| live_blocked | **true** — no live underwriting until auditor + actuarial review + founder release |

---

## 2. Why

**Who pays:** cover buyers pay premiums (100–1,500 bps annualized depending on risk tier) — priced below the expected-loss cost of an uninsured exploit for rational buyers, exactly Nexus's sub-1% entry pricing logic extended by risk tiers. Capital providers earn the underwriting spread. **Why they pay:** a single exploit can erase a position; the mutual converts tail risk into a known premium, and staked-assessor attestation replaces slow subjective voting. **Revenue path to Treasury:** 25 bps of every premium is forwarded to `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` at collection — the only revenue line that is independent of claims experience. At $2M of annual cover written at average 300 bps premium, premiums ≈ $60k and Treasury takes **$150/year at 25 bps**, scaling with cover volume; the mutual's growth, not its loss ratio, drives this. **What breaks without this:** SINCOR users and partner protocols carry uninsured smart-contract risk; one exploit in the ecosystem destroys more trust (and TVL) than the mutual's entire capitalization.

---

## 3. Build Stack

- **Solidity 0.8.24**, OpenZeppelin `AccessControl`, `ReentrancyGuard`, `SafeERC20`, `ERC721` (cover NFTs), `TimelockController` (48h), `EIP712` (score + attestation signatures).
- **Foundry** for unit/invariant/fuzz; fork tests replay a real historical exploit.
- **Python 3.11**, `web3.py`, `eth-account` (EIP-712 signing per repo convention — byte-identical digests, secp256k1 on the money path).
- **Verified repo integration points:**
  - Reuse: `dae/governance.py` (member-vote pattern), `onchain/script/Deploy.s.sol` (deploy convention), `marketplace/settlement.py` (payout-settlement patterns), OZ preset patterns used across the repo's hooks.
  - New: `onchain/src/hooks/p05/`, `src/sincor2/defi/p05/`, `agents/defi/p05/`, `docs/p05/`, `scripts/p05/`.
  - **Noted gap:** `sinax/` does not exist in the repo — the attestation verifier ships as an interface + assessor-signature implementation; the SINAX module integration is a named future work item, not current code.
- **Reused vs built new:** mutual share accounting, timelock governance, cover-NFT pattern, EIP-712 signing — reused/adapted. Reserve-ratio-guarded underwriting, tiered premium formula, pro-rata shortfall math, assessor bonding/slashing, SINAX-shaped attestation interface — built new.

---

## 4. Tip

- **Correlated risk kills mutuals:** one shared dependency (e.g., a common oracle or bridge) exploited across five covered protocols can trigger simultaneous claims exceeding reserves. The 130% reserve ratio is a floor, not a target — monitor **concentration by shared dependency**, and cap any single dependency cluster at 40% of outstanding cover.
- **Attestation is the attack surface:** forged loss proofs are the #1 threat. The assessor EIP-712 allowlist is a bootstrap trust assumption — document it as such; the assessor bond (1,000 units) must exceed the gas cost of filing by 100× to deter spam.
- **Adverse selection:** the protocols most eager to buy cover are the riskiest. The score-tier multiplier (2.0× above 0.50) plus the 1,500 bps cap must be re-fit quarterly against actual loss data; if loss ratio exceeds 70% of premiums for two quarters, the base rate ratchets +50 bps automatically (coded rule, not discretion).
- **Claim-window edge:** exploits discovered on day 31 are excluded — harsh but necessary for reserving. The runbook must include a "late discovery" governance path (supermajority 75% vote can extend once per incident, max +14 days).
- **Monitoring signals (must alert):** reserve ratio trending toward 140% (pre-breach warning); score staleness approaching 7,200 blocks; any protocol's exploit chatter (feed into underwriter as an emergency rescore trigger); shortfall factor < 1.0 (haircut active — page immediately).

---

## 5. Acceptance criteria

1. Reserve gate: an `underwrite` that would push `reserves / outstandingCover` below **130% reverts**; fuzzed sequences of deposits/covers/claims never breach the floor.
2. Pricing: three fixture protocols (scores 0.15 / 0.35 / 0.70) produce premiums of exactly **100 / 200 / 400 bps** annualized on identical terms; stale scores (> 7,200 blocks) block new covers.
3. Claims: valid attestation + in-window → payout succeeds; missing attestation → revert; filing on day 31 → revert; same (coverId, incidentHash) twice → revert.
4. Shortfall: simulated aggregate claims at 150% of reserves pay **pro-rata to the wei**; one claimant's reverting receiver does not block others (pull pattern proven).
5. Fees: 25 bps of every premium lands at the treasury address **at collection** (event + balance assertion).
6. Governance: listing vote passes only with ≥ 10% quorum and ≥ 60% yes; parameter changes execute only after the **48h timelock**; admin cannot bypass.
7. Pause safety: guardian pause blocks new underwriting but **never blocks in-window `claimPayout`** (tested explicitly).

---

## 6. Risk gates

From catalog metadata — `live_blocked=true`, gates `reserve_ratio`, `claim_window`, risk_score=0.48:

- **What stays dry-run / blocked:** all underwriting. Testnet deploys run scripted claims only. `live_blocked` is enforced in `buyCover` via a `liveUnderwritingEnabled` flag defaulting false; release requires auditor sign-off + actuarial review + founder-multisig transaction.
- **reserve_ratio:** 130% floor, on-chain, every underwrite. Raising the floor is guardian-executable; lowering requires governance vote + 48h timelock.
- **claim_window:** 30 days, immutable per cover at purchase (stored on the cover NFT); global default changeable only by governance + timelock.
- **Auditor + founder-signer path required for:** live underwriting release, reserve-ratio reduction, attestation-verifier implementation swap (toward the real SINAX module), initial capitalization plan.
- External audit covers `RiskMutual.sol`, the premium formula, and the attestation verifier; actuarial review signs off the base rate and tier multipliers.

---

## 7. Phase definition of done

- **spec:** `docs/p05/ARCHITECTURE.md` (pool lifecycle, Nexus-style cover model, SINAX-gated claims, 130% solvency model, 30-day claim window), frozen interfaces, `ECONOMICS.md` with three worked premium tiers to the wei; solvency proof sketch shows the pool cannot pay beyond reserves.
- **scaffold:** `onchain/src/hooks/p05/`, `src/sincor2/defi/p05/`, `agents/defi/p05/`, `docs/p05/` exist; stubs compile and collect; `risk_underwriter.yaml` skeleton validates; CI lints pass.
- **core:** pool, tiered pricing, underwriter agent with versioned signed score feeds, attestation-gated claims, pro-rata payouts, 25 bps treasury routing, governance with 48h timelock — all implemented; pricing fixtures produce the §1 premiums; pause never blocks in-window claims.
- **testing:** unit (24/24) + cover-to-claim integration + 10k-run invariant/fuzz (reserve floor, no double-claim, payout ≤ reserves) + pinned-block fork simulation of a real historical exploit with timeline, payouts, and reserve impact in `docs/p05/FORK_SIM.md`; all green.
- **audit:** `docs/p05/AUDIT.md` frozen and tagged; threat model covers attestation forgery, score manipulation, governance capture, reserve drain; actuarial review signed; all High/Critical findings fixed with regression tests; gas report shows ≥ 15% claim-path improvement, fuzz re-run green.
- **docs:** `TECHNICAL.md` reimplementable with explicit actuarial assumptions; `risk_underwriter.yaml` finalized; `RUNBOOK.md` (score cadence, exploit response, claim-window ops, late-discovery path) rehearsed with logged timestamps; `monitor.py` panel renders fork-sim data with reserve-approach and exploit alerts firing.
- **deploy:** Base Sepolia deployment verified; pool capitalized, test covers underwritten, scripted claim executed end-to-end with hashes in `docs/p05/DEPLOY.md`; `MAINNET_CHECKLIST.md` signed — no live underwriting without ratified reserve parameters and the release transaction.

---

## P06 — Perp DEX Hedging Swarm (Deep Spec)

**Catalog line:** fee_bps=12 · risk_score=0.62 · target_apr=0.18 · live_blocked=true ·
gates=(delta_band, funding_sign, liq_buffer) · max_alloc_pct=0.15 · min_capital_usd=250.00
**Treasury:** `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` (canonical, from `src/sincor2/defi/catalog.py`)

**Grounding (verified 2026-09-27):** Hyperliquid perps: funding settles **hourly** as 1/8 of the 8-hour formula `F = P + clamp(interest − P, ±0.0005)`, capped at 4%/h, **paid on oracle price, not mark price**; mark price (median of three constructions) drives margining and liquidation; maintenance margin = half the initial margin at the tier's maximum position size (1.25%–16.7% by tier); partial liquidations precede full; below 2/3 of maintenance without book absorption → backstop liquidation; negative account value → auto-deleveraging. Fees: taker 4.5 bps / maker 1.5 bps. Delta-neutral funding harvest (long spot/LST + equal short perp) is an operations game, not a signal game: round-trip entry/exit ≈ 4 taker fills ≈ 0.5%, so at 10% funding APR a position must be held ~18 days to break even; entries only make sense when funding is elevated and persistent; when funding flips negative the short leg pays and the position must close.

---

## 1. What/How

A swarm of Python hedge agents plus an on-chain `PerpHedgeEngine` that holds LST collateral (cbETH on Base), sizes an exact-matching perp short on Hyperliquid, collects funding, and unwinds automatically on funding flips or margin stress. A contract-level live-block gate (`liveEnabled=false` default) makes live intents impossible until a conversion proof is presented. 12 bps of realized P&L routes to Treasury.

### Step-by-step flow

1. **Collateral intake.** Agent deposits LST (cbETH) into `PerpHedgeEngine`; engine records `collateralAmount`, `collateralPrice` (oracle, staleness guard: reject prices older than 120 seconds), and computes spot exposure in USD.
2. **Sizing.** The sizing engine computes the perp short notional: `shortNotional = spotExposureUSD × (1 + liq_headroom)`, where `liq_headroom = 5%` (deliberate slight over-hedge so drift stays inside the band longer; the band absorbs it). On-chain sizing matches the Python reference model (`sizing.py`) **within 1 wei** across the fuzz vector range — the fuzz task proves this.
3. **Gate checks before open (all must pass, all on-chain where marked):**
   - **delta_band:** projected |net delta| ≤ **3% of NAV** (on-chain).
   - **funding_sign:** current annualized funding ≥ **+8%** (entry threshold; agent-side, read from Hyperliquid API; also require the 24h average ≥ +5% to avoid spike-chasing).
   - **liq_buffer:** projected margin ratio ≥ **4× maintenance margin** (on-chain health check).
   - **live gate:** `liveEnabled == true` (on-chain; defaults false).
4. **Open.** Engine escrows collateral; agent opens the short via the Hyperliquid adapter using **isolated margin** (bounds max loss to the position margin; cross margin is forbidden by config). Position sized at effective 1× (no position-sizing multiplier beyond 1× notional) — the yield comes from funding, not direction.
5. **Maintain.** Funding monitor polls every check-in cycle (15 min): reads hourly funding, computes annualized rate, checks delta drift from oracle moves. Rebalance fires only when |delta| exits the 3% band, with **1% hysteresis** (re-enter requires returning inside 2%) — zero redundant rebalances.
6. **Kill rules (automatic unwind):**
   - **funding_sign flip:** annualized funding negative for **2 consecutive hourly checkpoints** → unwind.
   - **liq_buffer breach:** margin ratio < **2× maintenance** → agent-initiated unwind; margin ratio < **1.5×** → contract pauses new opens (existing positions untouched; withdrawals/unwind never paused).
   - **Oracle staleness:** price older than 120s → no new actions; existing positions hold (fail-static).
7. **Unwind.** Atomic sequence: close short (reduce-only IOC, 50 bps slippage bound) → realize P&L → compute 12 bps protocol fee on **positive realized P&L only** → route fee to treasury → return LST + residual to the swarm vault. Pull-style payout fallback: if the recipient reverts, funds move to `pendingWithdrawals` (same pattern as the repo's ExecutionEscrowManager fix) — unwind can never brick.
8. **Fee routing.** `accrueHedgeFees()` on every close/rebalance: `fee = max(0, realizedPnl) × 12 bps` → `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`. Funding collected while open is not fee'd until realized at close (no phantom fees on unrealized funding).

### Key contracts / modules

| Component | Location | Role |
|---|---|---|
| `PerpHedgeEngine.sol` | `onchain/src/p06/` (new) | Collateral custody, sizing, gates, unwind, fee accrual |
| `IPerpHedgeEngine.sol`, `IHedgeAgentAdapter.sol`, `ILiveBlockGate.sol` | `onchain/src/p06/interfaces/` (new) | Frozen interfaces, NatSpec |
| `sizing.py`, `funding_monitor.py`, `position_loop.py` | `src/sincor2/defi/p06_perps/` (new — note: task files use `p06_perps/`, distinct from the `p0N/` convention) | Reference sizing model, funding polling, open/rebalance/unwind orchestration |
| `p06-hedge-agent.yaml` | `configs/agents/` (create per task files) | Budgets, memory, check-in cadence, gate parameters |
| Hyperliquid adapter | new module under `src/sincor2/defi/p06_perps/` | Testnet-first; API-wallet signing; isolated-margin only |

### Actors

- **Hedge agents** (swarm) — monitor funding, compute sizing, submit intents; hold no custody beyond the engine.
- **PerpHedgeEngine** — the only custodian of LST collateral; enforces all on-chain gates.
- **Guardian** — pauses opens, triggers emergency unwind, rotates API wallets; cannot lift the live gate (that requires the conversion-proof ceremony).
- **Founder signer** — the sole party that can present the conversion proof flipping `liveEnabled`.

### Numeric parameter table (canonical for all tasks)

| Parameter | Value |
|---|---|
| Protocol fee | 12 bps on **positive realized P&L only** → treasury |
| Target gross APR | 18% (0.18) |
| delta_band | |net delta| ≤ 3% of NAV; 1% hysteresis (re-enter at 2%) |
| funding_sign entry | annualized funding ≥ +8% now AND 24h avg ≥ +5% |
| funding_sign kill | negative for 2 consecutive hourly checkpoints → unwind |
| liq_buffer target / agent-unwind / contract-pause | 4× / 2× / 1.5× maintenance margin |
| liq_headroom (sizing over-hedge) | 5% |
| Position sizing multiplier | 1× notional (funding yield, no directional multiplier) |
| Margin mode | isolated only; cross margin forbidden |
| Oracle staleness | 120 seconds |
| Check-in cadence | 15 min |
| Break-even hold (10% APR, taker fills) | ~18 days — positions are days-to-weeks, not intraday |
| Round-trip cost assumption | ~0.5% (4 taker fills at ~0.13%) |
| max_alloc_pct / min_capital | 15% per position set; $250 min |
| live_blocked | **true** — `liveEnabled=false` default; every live path reverts without conversion proof |

---

## 2. Why

**Who pays:** the position pays for itself — funding is a peer-to-peer transfer from perp longs to shorts; the swarm harvests it with a hedged book. The 12 bps fee is taken from realized profit, so losing positions pay nothing. **Why the edge exists:** perp funding is persistently positive in bull regimes (15–60%+ annualized on hot alts per 2026 market data); retail cannot run the two-leg book with disciplined unwind rules — the swarm's edge is operational (monitoring, fast rebalancing, unemotional unwind), not predictive. **Revenue path to Treasury:** 12 bps of every profitable close to `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`. At $1M deployed earning the 18% target, annual realized P&L ≈ $180k → Treasury ≈ **$216/year at 12 bps**, scaling with deployed capital and funding regime. **What breaks without this:** SINCOR's delta-neutral yield thesis (P01's hedge sleeve, P11) has no execution engine; funding premium leaks to whoever runs the bots; a funding flip without automatic unwind turns a "neutral" book into a directional loss.

---

## 3. Build Stack

- **Solidity 0.8.24**, OpenZeppelin `ReentrancyGuard`, `SafeERC20`, `AccessControl`; fixed-point math via OZ `Math.mulDiv` (no floating point; 1-wei parity with Python required).
- **Foundry** for unit/fuzz/invariant; fork tests against Base.
- **Python 3.11**, `web3.py`, Hyperliquid Python SDK (testnet first), `eth-tester` for offline suites; 15-min check-in via repo scheduler.
- **Verified repo integration points:**
  - Reuse: `onchain/script/Deploy.s.sol` (deploy-script convention), `onchain/test/` (Foundry conventions), `scripts/` (ops), `verticals/trading/` (agent patterns), TOA telemetry hooks per repo convention.
  - New: `onchain/src/p06/`, `src/sincor2/defi/p06_perps/`, `configs/agents/p06-hedge-agent.yaml`, `docs/ops/p06-*.md`, `docs/audits/p06/`, `scripts/deploy/p06/`.
  - **Deliberate deviation (from the task files, kept):** P06 uses `onchain/src/p06/` and `src/sincor2/defi/p06_perps/` rather than the `hooks/p0N` + `defi/p0N` layout of P01/P04/P05 — the engine is not a Uniswap hook, so it does not live under `onchain/src/hooks/`.
- **Reused vs built new:** OZ guards, deploy/test conventions, scheduler, TOA hooks — reused. Perp sizing engine with 1-wei Python parity, delta-band controller with hysteresis, funding monitor with flip-kill, liq-buffer health ladder, live-block gate, pull-fallback unwind — built new.

---

## 4. Tip

- **Funding is paid on oracle price, not mark.** Size and P&L accounting must use the same price basis the venue settles on, or the books drift by the oracle/mark spread every hour.
- **Isolated margin only.** Cross margin lets one bad position eat the account; the config must reject cross-margin mode at the adapter layer, not just in docs.
- **Testnet margin tiers are tighter** than mainnet — a sizing that passes on testnet with 4× buffer may behave differently live; re-run the buffer ladder against mainnet tier tables before the conversion-proof ceremony.
- **The 18-day break-even is the strategy's honesty check.** Any "opportunity" with funding that wouldn't survive 18 days at current rates after the 0.5% round trip is churn, not yield — the entry gate (8% now / 5% 24h avg) encodes this.
- **API wallets, not main keys:** the agent signs via Hyperliquid API wallets with withdraw disabled; key rotation is a guardian action with a logged ceremony.
- **Monitoring signals (must alert):** funding crossing below +2% annualized (pre-flip warning); |delta| > 2% (pre-breach); margin ratio < 3× (approaching agent-unwind); oracle staleness > 60s; any `liveEnabled` state change (critical); fee accrual stopping while closes continue.

---

## 5. Acceptance criteria

1. Sizing parity: on-chain sizing matches the Python reference **within 1 wei** across the full fuzz vector range of collateral and price inputs.
2. Delta discipline: 10k fuzzed price/funding/shock sequences keep |net delta| ≤ 3% of NAV; rebalances fire only on band exit (hysteresis proven: **zero redundant rebalances** in band-edge unit tests).
3. Funding kill: recorded funding-flip fixtures trigger agent unwind **within one 15-min check-in cycle** of the second consecutive negative hourly checkpoint.
4. Liq buffer: fork simulation across 2025-style volatility shocks shows **zero liquidations**; margin ratio never prints below 2× without an unwind already in flight.
5. Live gate: every live path (open, rebalance, settle) **reverts** with `liveEnabled=false` and succeeds after the conversion proof; gate state is queryable by the agent before any action.
6. Fees: profitable closes route **exactly 12 bps** of positive realized P&L to the treasury address; losing closes route zero fees and are not penalized.
7. Unwind safety: the full open→rebalance→unwind cycle completes on a Base Sepolia fork with all fees accounted; a reverting fee recipient falls back to pull-withdrawals and **never bricks** the unwind.

---

## 6. Risk gates

From catalog metadata — `live_blocked=true`, gates `delta_band`, `funding_sign`, `liq_buffer`, risk_score=0.62 (highest of the four):

- **What stays dry-run / blocked:** everything live. The engine deploys with `liveEnabled=false`; the Python agent runs in intent-logging mode. No live position can exist before the conversion-proof ceremony (founder-signed proof + auditor attestation of the mainnet tier-table check).
- **delta_band:** 3% band / 1% hysteresis, on-chain enforced at open and checked every check-in; band changes require guardian + 48h timelock.
- **funding_sign:** entry/kill thresholds in the agent YAML; the kill rule (2 consecutive negative hours → unwind) is **not relaxable** below 2 checkpoints by any role.
- **liq_buffer:** the 4×/2×/1.5× ladder is on-chain; lowering any rung requires the full auditor + founder path.
- **Auditor + founder-signer path required for:** conversion-proof/live release, API-wallet rotation, any change to sizing math, buffer-ladder changes, treasury fee-address change.
- Position cap: 15% of swarm capital per position set; $250 minimum — sub-scale positions are rejected (gas would eat the edge).

---

## 7. Phase definition of done

- **spec:** `docs/architecture/p06-perp-dex-hedging-swarm.md` (contract system, agent loop, delta-neutral strategy, gate table), three frozen interfaces with NatSpec, `docs/economics/p06-hedge-economics.md` with a runnable Python model reproducing the worked numbers within 0.1%; every gate mapped to its enforcing contract/function; 12 bps fee route traced to the treasury address.
- **scaffold:** `onchain/src/p06/`, `src/sincor2/defi/p06_perps/`, `configs/agents/p06-hedge-agent.yaml`, test stubs exist; interfaces compile under solc 0.8.24 with zero errors; Python imports resolve; CI lints pass.
- **core:** sizing engine with 1-wei Python parity; liq-buffer health ladder with pull-fallback unwind; funding monitor enforcing the sign gate with backoff and TOA telemetry; live-block gate reverting every live path pre-proof; position loop completing open→rebalance→unwind; delta-band controller with hysteresis showing zero redundant rebalances; edge cases (stale oracle, venue downtime, partial fills, funding staleness) degrading gracefully with per-case incident docs.
- **testing:** unit (gate revert matrix asserted explicitly) + agent-to-contract integration on an in-process chain + 10k+ fuzz/invariant runs green (delta-neutrality within band, unwind never bricks) + Base fork simulation report with hedge P&L, funding captured, and treasury fee accrual at reproducible block pins; all green.
- **audit:** package frozen at `docs/audits/p06/` with contract inventory, gate-enforcement map, and the conversion-proof precondition stated explicitly; threat model covers oracle manipulation, liquidation, live-gate bypass; zero open high/medium findings; gas report in `docs/audits/p06/gas.md` shows measurable savings with zero behavioral change.
- **docs:** `docs/p06-perp-dex.md` (a new builder can implement a compatible agent from it; all code references resolve); agent YAML boots with no code changes; `docs/ops/p06-runbook.md` covers funding-flip and liquidation incidents; monitoring hooks report real values or `unknown`, never fabricated health.
- **deploy:** idempotent `scripts/deploy/p06/` deploys to Base Sepolia with the live gate engaged (`liveEnabled=false`) and the live path reverting on testnet as expected; `docs/ops/p06-mainnet-checklist.md` signed — no mainnet action possible without the conversion-proof step checked.

---

## P08 — RWA Tokenization Vaults: Deep Spec

> Project: P08 RWA Tokenization Vaults | Category: rwa | fee_bps: 15 | risk_score: 0.40
> target_apr: 0.08 | min_capital_usd: 500 | max_alloc_pct: 0.10 | live_blocked: true
> Gates: `compliance_pack`, `kyc_flag`
> Reference: ~/workspace/sincor2/src/sincor2/defi/catalog.py (ProtocolSpec 8, P08_RWA)

## 1. What/How

P08 is a compliance-first ERC-4626-style tokenization vault family for off-chain yield-bearing real-world assets (US T-bills/T-bill ETFs, investment-grade private credit). The architecture copies the industry-proven shape of Ondo USDY/OUSG and BlackRock BUIDL: a regulated custodian holds the asset off-chain, an SPV/legal wrapper gives token holders an enforceable claim, and an on-chain token contract enforces transfer eligibility. P08's hard invariant is **yield-only-after-gate**: deposits are always permitted, but yield accrues and distributes only after the `compliance_pack` gate passes, and capital sits in dry-run (off-chain simulated) mode until promotion. The `kyc_flag` gate is a per-address eligibility flag consulted on every yield-bearing path.

Step-by-step flow:

1. **Deposit** — any address deposits USDC into `RWAVault.deposit()`. Shares are minted 1:1 at NAV on first deposit, then at `NAV = totalAssets / totalSupply`. A pull-pattern (pendingWithdrawals + `withdraw()`) is used for distributions so a reverting recipient can never brick payouts. Deposits are always accepted regardless of gate state; only *yield accrual* is gated.
2. **Dry-run ledger** — the off-chain Python capital manager (`src/sincor2/defi/p08_rwa/capital.py`) mirrors every deposit in a simulated book with zero on-chain yield exposure. NAV shadow-pricing runs against this book. Capital is "dry-run" until promotion.
3. **Compliance pack assembly** — the Python compliance agent (`src/sincor2/defi/p08_rwa/agent.py`) assembles a `CompliancePack`: KYC attestation IDs for all depositors, custodian proof-of-reserve attestation, asset NAV report, jurisdiction/geo list, and SINAX audit proof of the vault bytecode hash. Pack is submitted to the gate contract.
4. **Gate evaluation** — `ComplianceGate.evaluate(packHash, vault)` requires: (a) every depositor address has `kyc_flag == true` in `KYCRegistry`, (b) custodian attestation < 24h old, (c) NAV oracle deviation < 2% vs the last reported NAV, (d) no address on the sanctions blocklist. All four must pass in one atomic call.
5. **Promotion** — on gate pass, the agent calls `promoteToLive()` (timelocked 48h, 2-of-3 multisig). Capital is atomically marked live in both the on-chain flag and the dry-run ledger; the transition is idempotent (re-calling is a no-op). Pre-gate yield is mathematically zero: `accrueYield()` reverts when `gateOpen == false`.
6. **Yield accrual** — post-gate, the NAV oracle pushes daily NAV updates (T+1 business day cadence, matching tokenized-treasury primary issuance norms). Yield = NAV drift × shares. 15 bps (0.15%) of every distribution is swept to Treasury `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`.
7. **Redemption** — redemptions are always permitted (even mid-gate, even post-revocation) against NAV minus a 2-business-day settlement delay (T+2), with a compliance re-check that only blocks *yield* claims, never principal. If a KYC flag is revoked mid-accrual, that holder's accrued yield is frozen (not seized) and principal remains redeemable.
8. **Write-down handling** — custodian write-down reports are injected via the NAV oracle; share price adjusts downward. If NAV drops > 5% in one update, accrual pauses and the agent pages the founder signer before resuming.

Numeric parameters (locked):

| Parameter | Value |
|---|---|
| Protocol fee to Treasury | 15 bps (0.15%) of every yield distribution |
| Target gross APR | 8.0% (blended: ~4.3% T-bill sleeve, ~9–11% private-credit sleeve) |
| Min capital per tick | $500 |
| Max allocation per tick | 10% of vault capital |
| NAV oracle cadence | daily, T+1 business day |
| Custodian attestation freshness | ≤ 24h |
| NAV deviation tripwire | 2% vs last reported NAV |
| Redemption settlement | T+2 business days |
| Write-down circuit breaker | >5% single-update NAV drop → accrual pause |
| Promotion timelock | 48h, 2-of-3 multisig |
| Token standard | ERC-20 + ERC-4626 share math; permissioning via gate, not via ERC-3643 (deliberate: composability over embedded identity; eligibility enforced at yield layer) |
| Oracle stack | Chainlink Data Feeds / PoR pattern for NAV + reserve attestation; RedStone fallback |
| Transfer rules | deposits open; *yield claims* and *post-promotion distributions* require `kyc_flag`; sanctions-blocklist enforced on every path |

Actors: depositor (any address), compliance agent (Python, off-chain), custodian attestor (off-chain, signed reports), KYC registry admin (attests/revokes flags), founder multisig (promotion + incident response), Treasury (fee sink).

Key contracts/modules: `RWAVault.sol` (ERC-4626 math + pull payouts), `ComplianceGate.sol` (4-check gate), `KYCRegistry.sol` (flag attestations, revocation, events), `NAVOracle.sol` (push oracle with staleness check: revert if `updatedAt` older than 36h), Python `capital.py` (dry-run ledger), Python `agent.py` (pack assembly, monitoring), `configs/agents/p08-compliance-agent.yaml`.

## 2. Why

Who pays and why: non-US institutional and HNW holders of tokenized T-bills/private credit pay for (a) on-chain yield access without touching the compliance apparatus themselves, and (b) a 24/7 secondary redemption path. The yield sources are real: US T-bill rates (~4.3% at writing) and private-credit coupons (~9–11%).

Revenue path to Treasury: 15 bps is taken off *every yield distribution* — not on deposits, not on AUM. At $10M AUM and 8% gross APR, annual yield = $800k, Treasury take = $1,200/yr. This is deliberately small per dollar; the model only works at AUM scale, which is why the catalog pairs P08 with the volume-over-vanity directive. The fee math is pinned in the on-chain `accrueYield` distribution loop and mirrored in the Python fee ledger with per-vault attribution.

What breaks without this: the compliance pack gate is the only thing standing between the vault and operating an unregistered securities distribution. Skip the gate and the vault is Ondo-without-the-allowlist — a regulatory incident, not a product. Skip dry-run and pre-gate capital touches live yield paths, which the task-level invariant explicitly forbids.

## 3. Build Stack

- **Solidity 0.8.24** (repo standard; via-IR where needed). Libraries: OpenZeppelin Contracts v5 (`ERC20`, `ReentrancyGuard`, `SafeERC20`, `AccessControl` for the registry admin role, `TimelockController` for the 48h promotion timelock). No upgradeability proxies on the vault itself — immutable money logic; the gate and registry are timelocked but not proxy-upgradable (audit preference: freeze semantics over upgrade risk).
- **Python 3.11**, stdlib + `web3.py` + `eth_account` (public API only; signing delegated to caller, mirroring the auction-bridge pattern in `src/sincor2/auction_bridge.py`). Reused from repo: `src/sincor2/defi/catalog.py` (TREASURY constant `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`, ProtocolSpec enforcement), `src/sincor2/defi/yield_aggregator.py` (the `EXECUTE_LIVE` env pattern and Treasury fee-flow pattern), `src/sincor2/treasury_settlement.py` (fee sweep accounting).
- **Exact integration points (verified on disk):**
  - `~/workspace/sincor2/src/sincor2/defi/p08_rwa/` (new module; sibling of `yield_aggregator.py`)
  - `~/workspace/sincor2/onchain/src/p08/` (new contracts; sibling of existing `ComplianceGuard.sol` — reuse its access-control pattern for the gate)
  - `~/workspace/sincor2/verticals/compliance/` (existing KYC/AML vertical — the Python agent's attestation calls land here)
  - `~/workspace/sincor2/src/sincor2/sinax/` (SINAX proof of vault bytecode hash in the compliance pack)
  - `~/workspace/sincor2/configs/agents/p08-compliance-agent.yaml` (agent config; matches `configs/agents/` convention used by the repo's A2A agents)
- **Reused vs built new:** reused — ERC-4626 share math pattern, OZ guards, EXECUTE_LIVE/Treasury conventions, compliance vertical, SINAX attestation. Built new — the 4-check compliance gate, KYC flag registry with revocation, dry-run capital ledger with atomic promotion, pull-pattern distribution with yield-freeze-on-revocation, T+2 redemption queue under compliance review.

## 4. Tip

- **The oracle is the weakest link, not the gate.** Every credible RWA blowup since 2022 traced to off-chain legs (custody gaps, stale reserve reporting), not smart-contract bugs. Monitor attestation freshness and NAV staleness as production-critical signals; the on-chain `NAVOracle` must hard-revert on updates older than 36h.
- **Revocation semantics:** freezing yield but never principal is a legal-design choice — test it explicitly, because the "obvious" implementation freezes everything and creates a redemption-hostage incident.
- **Share-price manipulation:** the first-deposit inflation attack is mitigated by minting a dead-share floor (1,000 shares burned to address(0) at deployment) — standard ERC-4626 defense, do not skip it.
- **Timelock admin is a single point of failure:** the 48h promotion timelock and registry admin keys must be 2-of-3 multisig with keys in separate custody; a compromised registry admin can unilaterally bless dirty addresses.
- **Monitoring signals (page on):** gate pass/fail rate per hour, KYC revocation events, NAV deviation > 1% (warn) / 2% (block), attestation age > 20h (warn) / 24h (block), dry-run vs live capital mismatch (any delta = P0), fee ledger vs on-chain distribution delta (must be 0).
- **Status feed rule:** the dashboard hook reports real values or `unknown`, never fabricated `healthy` (per STATUS_FEED_SCHEMA).

## 5. Acceptance criteria

1. Every yield-bearing path (`accrueYield`, distribution claims) reverts unless `ComplianceGate.evaluate` returns true with all 4 checks passing; a fuzzer cannot find a bypass in 10k+ runs.
2. Pre-gate accrued yield is exactly zero under all call paths; share math matches the Python reference within 1 wei.
3. KYC revocation freezes yield immediately for the revoked address while principal redemption stays available; registry events are queryable for audit.
4. Dry-run promotion is atomic and idempotent: dry-run ledger reconciles to vault state to the wei before and after; re-calling promotion is a no-op.
5. Pull-pattern distributions cannot be bricked: a reverting recipient blocks only their own claim.
6. 15 bps fee on every distribution routes to `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`; fork test asserts the treasury delta equals exactly 0.15% of the distribution.
7. NAV oracle reverts on stale data (> 36h); a > 5% single-update NAV drop pauses accrual and requires multisig resume.
8. First-deposit share inflation is neutralized by the dead-share floor; verified by test.
9. solc 0.8.24 compiles the full contract set with zero errors and zero warnings.

## 6. Risk gates

- **Catalog:** `live_blocked: true` — P08 never emits live intents from the module; capital stays dry-run until the compliance pack gate passes AND the 48h multisig promotion completes. Gates `compliance_pack` and `kyc_flag` are enforced on-chain, not just in docs.
- **Risk budget:** risk_score 0.40 → max_alloc_pct 0.10 per tick; min $500 capital floor before any gate submission (avoids paying attestation costs on dust).
- **Stays dry-run:** everything until `promoteToLive()` executes — scanning, NAV shadow-pricing, pack assembly, gate evaluation dry-runs.
- **Auditor + founder-signer path:** promotion to live requires external audit sign-off (audit-prep package) + 2-of-3 founder multisig execution; KYC registry admin actions are timelocked 48h; NAV oracle updates require the custodian's attested signature, not just the agent's.
- **Incident kill-switch:** the founder multisig can pause accrual and freeze distributions in one call; principal redemptions can never be paused.

## 7. Phase definition of done

- **Spec (3 tasks):** `docs/architecture/p08-rwa-vaults.md`, the three Solidity interfaces, and `docs/economics/p08-rwa-economics.md` exist; a reviewer can trace both gates to their enforcing components, reproduce the 8% APR model, and trace the 15 bps fee to Treasury — no implementation required yet.
- **Scaffold (2 tasks):** `onchain/src/p08/`, `src/sincor2/defi/p08_rwa/`, test stubs, and the agent YAML exist; interfaces compile under solc 0.8.24 with zero errors and the Python agent boots and answers a health ping.
- **Core (7 tasks):** gate, vault, dry-run manager, KYC registry, compliance agent, yield accrual, and edge cases are implemented; the lifecycle fixture (deposit → dry-run → pack → gate pass → promote → accrue → redeem) runs end-to-end, gate-fail fixtures keep capital dry-run with zero yield, and every edge case has a test plus an incident write-up.
- **Testing (4 tasks):** unit, integration, fuzz (10k+ runs, all invariants green), and Base-fork simulation all pass; the fork report records accrual, Treasury fees, and reproducible block pins.
- **Audit (3 tasks):** the audit-prep package is complete with threat model (gate bypass, KYC oracle, share-price manipulation) and gate map; all findings are remediated with zero open high/medium and linked regression tests; the gas report shows before/after with zero behavioral change.
- **Docs (3 tasks):** technical docs let a new builder integrate from docs alone; the agent YAML boots the agent and the runbook covers revocation and write-down incidents; dashboard hooks emit real values or `unknown`.
- **Deploy (2 tasks):** idempotent Base Sepolia deploy scripts deploy with capital in dry-run and the gate closed; the mainnet checklist is signed off with audit sign-off, key custody, and legal review — no mainnet promotion without a gate pass.

---

## P10 — Flash Loan Arbitrage Engine: Deep Spec

> Project: P10 Flash Loan Arbitrage Engine | Category: arb | fee_bps: 30 | risk_score: 0.70
> target_apr: 0.22 | min_capital_usd: 0 | max_alloc_pct: 0.10 | live_blocked: true
> Gates: `profit_floor`, `gas_ceiling`
> OS runtime contract: **opportunity scan only — EXECUTE_LIVE does not enable flash loans.** (catalog.py: "Opportunity scan only. EXECUTE_LIVE does not enable flash loans.")
> Reference: ~/workspace/sincor2/src/sincor2/defi/catalog.py (ProtocolSpec 10, P10_FLASH_ARB)

## 1. What/How

P10 is a two-half engine: an off-chain **opportunity scanner** (Python) that watches DEX venues for price discrepancies, and an on-chain **atomic executor** (`FlashArbExecutor.sol`) that runs borrow → swap → repay in one transaction. The defining constraint is the OS runtime contract: the module is **scan-only**. The executor exists as a reviewed, tested, audited artifact behind a code-level live-block gate — but no live flash loan can be originated from this module. Every execution path is atomic: if repayment fails, the whole transaction reverts as if the loan never happened (the lender's guarantee, not ours).

Step-by-step flow:

1. **Scan** — the Python scanner polls Base DEX venues (Uniswap V3/V4 pools, Aerodrome volatile/stable pools, and any venue allowlisted by the P16-style venue registry) plus mempool-adjacent feeds every 2 seconds (Base block time ≈ 2s). For each token pair it computes the maximum roundtrip discrepancy: `gross_edge = (price_B - price_A) / price_A`.
2. **Candidate scoring** — candidates are scored with `score = gross_edge − Σ swap_fees − flash_premium − slippage_est`. Each candidate carries a TTL of 2 blocks; expired candidates are never emitted to the executor (TTL enforced in the emitter, not just the consumer).
3. **Profit-floor filter** — net profit must clear the floor: `net = notional × gross_edge − flash_fee − gas_cost − slippage_est ≥ profit_floor`, where `profit_floor = max($50, 0.15% of notional)`. Sub-floor candidates are logged and dropped, never reaching the executor. The floor is YAML-configurable (`configs/agents/p10-arb-agent.yaml`).
4. **Gas-ceiling estimator** — pre-execution gas is estimated via `eth_estimateGas` on the exact calldata; if `gas_estimate × (base_fee + priority_fee) > gas_ceiling ($25)`, the candidate is skipped. Estimator must match observed gas within ±15% on fixtures or the gate is untrustworthy and must be retuned.
5. **Execution safety check** — the safety module dry-runs the full calldata via `eth_call` at the pending block: simulates the flash-loan callback, the swaps, and the repayment. If simulated net ≤ 0, or slippage exceeds the bounded calldata limit (max 50 bps per leg), the attempt is aborted before any signature. Slippage-bounded calldata means the executor's `minOut` values are committed at build time, not computed mid-callback.
6. **Atomic execution (gated off in-module)** — `FlashArbExecutor.execute(params)` borrows from the provider, runs the callback (`executeOperation` for Aave-style, `receiveFlashLoan` for Balancer-style — provider abstracted behind `IFlashProvider`), executes the swaps, repays principal + premium, and reverts on any net loss. Callback caller is verified (`require(msg.sender == provider)`) — the classic "anyone can call executeOperation" theft vector is closed by construction. Treasury capture: 30 bps of net profit is routed to `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` inside the same atomic transaction (if the fee transfer fails, everything reverts).
7. **Self-improving loop** — executed and scanned opportunities feed a Python loop that retunes scanner parameters (floor, TTL, venue weights) with TOA ranking; parameter sets are versioned and rollback restores the prior set. Backtest must show fixture P&L improvement without overfitting (walk-forward split, not in-sample).

Numeric parameters (locked):

| Parameter | Value |
|---|---|
| Protocol fee to Treasury | 30 bps (0.30%) of net arb profit, same-transaction |
| Target gross APR | 22% (at full allocation; scan-driven, lumpy — not a yield curve) |
| Profit floor | max($50, 0.15% of notional) — net of fee, gas, slippage |
| Gas ceiling | $25 per attempt, hard |
| Gas estimate tolerance | ±15% vs observed on fixtures |
| Per-leg slippage bound | 50 bps, committed in calldata |
| Flash-loan premium | Aave V3 5 bps (0.05%); Balancer 0 bps; Uniswap V3 flash swap = pool fee tier (5/30/100 bps) |
| Max notional per attempt | $250,000 (flash providers bound by pool TVL per asset) |
| Max allocation per tick | 10% of engine capital |
| Candidate TTL | 2 blocks (~4s on Base) |
| Scan cadence | every block (≈2s) |
| Break-even reference | $1M borrow on Aave V3: $500 fee + ~$12 gas ⇒ 0.051% minimum edge; production floor is 3× that (0.15%) |
| Retry / reorg policy | no automatic retry on revert (a revert means the edge vanished; retrying donates gas); reorgs on Base are rare but the safety sim re-validates at pending block |
| Venue coverage | Uniswap V3 + Aerodrome on Base at minimum; others via allowlist |

Actors: scanner agent (Python), executor contract (Solidity), flash-loan providers (Aave V3, Balancer — external), DEX venues (external), Treasury (fee sink), founder signer (the *separately-gated* live release, outside this module).

Key contracts/modules: `FlashArbExecutor.sol` (atomic execute, callback verification, revert-on-loss), `IFlashProvider` abstraction + `AaveV3Provider`/`BalancerProvider` adapters, `OpportunityScanner` (Python: venue polling, scoring, TTL emitter), `ProfitFloorFilter`, `GasCeilingEstimator`, `ExecutionSafety` (eth_call dry-run), `SelfImprovementLoop` (TOA-ranked parameter tuning), `configs/agents/p10-arb-agent.yaml`.

## 2. Why

Who pays and why: nobody pays P10 directly — the engine harvests transient price discrepancies between venues and keeps the net after costs. The "customer" is the Treasury: every captured arb pays 30 bps. At the 22% target APR on a $1M allocation, that's $220k gross, $660 to Treasury — plus the engine's real product is the *scanner signal itself*, which feeds the P16 aggregator's TOA forecast feed (cross-project revenue: better routing = more P16 fee volume).

Why scan-only matters commercially: real-world data (Base mainnet WETH/USDC) shows DEX-DEX spreads of 0.24–0.37% one-way but **negative roundtrips** (−0.24% to −0.70%) once both-leg AMM fees apply. Bots have compressed Base spreads below 0.02% on the majors. A live executor would mostly burn gas chasing ghosts; the scan-only module is the honest artifact — it proves where edges *would* exist and only a separately-gated, founder-signed release ever flips execution on.

Revenue path to Treasury: 30 bps of net profit per executed arb, same-transaction, revert-if-unpaid. Until the release gate opens, revenue is zero by design — the module's value is the opportunity feed.

What breaks without this: without the profit-floor and gas-ceiling gates, the engine executes negative-EV trades (the failure mode of every naive arb bot); without atomic revert-on-loss, a failed leg leaves inventory exposure; without callback-caller verification, the executor is a theft target.

## 3. Build Stack

- **Solidity 0.8.24.** Libraries: OpenZeppelin v5 (`ReentrancyGuard`, `SafeERC20`); provider interfaces handwritten against Aave V3 `IPool.flashLoan` / `FlashLoanSimpleReceiverBase` and Balancer V2 `IVault.flashLoan` (`receiveFlashLoan`) — no heavy dependencies, adapter pattern keeps provider logic isolated. The executor never holds user funds between calls; it is a pass-through.
- **Python 3.11**: `web3.py` for `eth_estimateGas`/`eth_call` dry-runs, `eth_account` public API only for any signing (the OS contract keeps signing outside this module for flash loans), `asyncio` scanner with per-venue pollers, `pyevm`/eth-tester for unit fixtures. Reused from repo: `src/sincor2/defi/catalog.py` (TREASURY constant, ProtocolSpec risk/cap enforcement), `src/sincor2/defi/yield_aggregator.py` (EXECUTE_LIVE env pattern — here it gates *scanning only*, never execution), `src/sincor2/treasury_settlement.py` (fee accounting), `src/sincor2/forecasting_engine.py` (TOA hook for the self-improvement loop), `onchain/src/hooks/MoebiusMEVHook.sol` (existing MEV-aware patterns — the executor must not fight the hook's flow capture; document the interaction).
- **Exact integration points (verified on disk):**
  - `~/workspace/sincor2/src/sincor2/defi/p10_arb/` (new module; sibling of `yield_aggregator.py`)
  - `~/workspace/sincor2/onchain/src/p10/` (new contracts)
  - `~/workspace/sincor2/configs/agents/p10-arb-agent.yaml` (floor, ceiling, TTL, venue weights)
  - Scanner venue feeds reuse the venue-adapter math from the P16 aggregator (`src/sincor2/defi/p16_dex_aggregator/` once built; until then, fixtures)
- **Reused vs built new:** reused — catalog risk/cap/Treasury conventions, EXECUTE_LIVE pattern, TOA ranking hooks, MoebiusMEVHook flow awareness. Built new — the TTL-emitting scanner, profit-floor/gas-ceiling gate pair, eth_call safety dry-run, callback-caller verification, provider adapters, the scan-only live-block gate, versioned self-improvement loop.

## 4. Tip

- **The edge you simulate is not the edge you get.** State changes between simulation and inclusion; on Base the 2s block time helps, but competing searchers front-run profitable mempool-visible arbs. The safety sim must re-run at the pending block, and the executor must revert cleanly when the edge evaporates — a revert costs gas, a bad fill costs capital.
- **Sandwich competition is the real adversary**, not the venues: your two-leg arb is itself sandwichable if any leg is visible. Prefer private submission paths where available; never set slippage tolerance above 50 bps per leg.
- **Gas estimation lies during congestion:** base fee can 10× mid-execution. The $25 ceiling must be evaluated against the *pending* block's fee, not the last mined block.
- **The scan-only gate is a security boundary, not a TODO:** any "temporary" live-execution flag added during development must fail closed — default deny, and the default-deny path must be the one tests exercise.
- **Monitoring signals:** candidates/sec, floor-reject rate, ceiling-reject rate, sim-vs-observed gas delta, TTL expiry rate, executor revert rate (target: 100% of failures revert with capital intact), Treasury fee per captured arb (target: exactly 30 bps of net).
- **Status feed rule:** report real values or `unknown`, never fabricated `healthy`.

## 5. Acceptance criteria

1. No test, fuzz run (10k+), or fixture can produce a loss-making execution: every failure reverts with capital intact (atomic revert-on-loss proven).
2. Profit-floor matrix: sub-floor candidates (net < max($50, 0.15% notional)) never reach the executor; the floor is YAML-configurable and the filter logs every rejection.
3. Gas-ceiling: attempts with estimated cost > $25 are skipped, never executed; estimator matches observed gas within ±15% on fixtures.
4. TTL: expired candidates are never emitted; the emitter (not just the consumer) enforces the 2-block TTL.
5. Scan-only gate: with the gate engaged, any live execution path reverts; scanning works; gate state is queryable before any action.
6. Callback security: `executeOperation`/`receiveFlashLoan` called by anyone other than the bound provider reverts (theft-vector test).
7. Slippage-bounded calldata: `minOut` committed at build time; a manipulated quote reverts, an honest one passes.
8. Treasury: 30 bps of net profit lands at `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` in the same atomic transaction; fork test asserts the exact delta.
9. Self-improvement: versioned parameter sets, walk-forward backtest shows fixture P&L improvement without overfitting, rollback restores prior parameters exactly.
10. solc 0.8.24 compiles with zero errors and zero warnings.

## 6. Risk gates

- **Catalog:** `live_blocked: true` — P10 never emits live intents. **OS runtime contract (hard): the module is opportunity-scan-only; EXECUTE_LIVE does not enable flash loans.** The executor contract ships behind a code-level live-block gate that reverts any live execution path until an explicit, separately-gated release (founder signer + auditor sign-off) — that release is out of scope for this project.
- **Risk budget:** risk_score 0.70 (highest in the catalog) → max_alloc_pct 0.10, conservative sizing even post-release; min_capital_usd 0 (flash loans need no upfront capital — the risk is gas and failed-execution cost, not principal).
- **Stays dry-run / scan-only:** everything — scanning, filtering, safety dry-runs, fork simulation. Nothing in this project originates a mainnet flash loan.
- **Auditor + founder-signer path:** the audit-prep package must document the scan-only precondition; any future execution release requires a fresh audit of the executor + founder multisig release transaction. The scanner's venue allowlist changes are timelocked.
- **Kill conditions:** if sim-vs-observed gas delta exceeds ±15% for 24h, the scanner halts candidate emission (fail closed); if the Treasury fee transfer ever fails in simulation, the executor is redeployed, not patched in place.

## 7. Phase definition of done

- **Spec (3 tasks):** `docs/architecture/p10-flash-arbitrage.md`, the three Solidity interfaces, and `docs/economics/p10-flash-economics.md` exist; a reviewer can verify every execution path is revert-safe, map both gates to enforcing components, reproduce the 22% APR model, and confirm the scan-only live-block is a code gate, not a comment.
- **Scaffold (2 tasks):** `onchain/src/p10/`, `src/sincor2/defi/p10_arb/`, test stubs, and the agent YAML exist; interfaces compile under solc 0.8.24 with zero errors and the Python scanner agent boots and answers a health ping.
- **Core (7 tasks):** scanner, safety module, profit-floor filter, gas-ceiling estimator, live-block gate, self-improvement loop, and edge cases are implemented; the fixture flow (scan → filter → safety-check → atomic execute) runs end-to-end with the live gate engaged, sub-floor and over-ceiling candidates are provably dropped, and every edge case has a test plus an incident write-up.
- **Testing (4 tasks):** unit, integration, fuzz (10k+ runs, loss-making execution proven impossible, gate bypass proven impossible), and Base mainnet-fork simulation all pass; the fork report records captured arb, Treasury fees, and reproducible block pins.
- **Audit (3 tasks):** the audit-prep package is complete with threat model (reentrancy, callback abuse, gate bypass) and the scan-only precondition documented; all findings remediated with zero open high/medium and linked regression tests; the gas report shows before/after with zero behavioral change on the hot execution path.
- **Docs (3 tasks):** technical docs let a new builder run the engine from docs alone; the agent YAML boots the agent and the runbook covers reverted-execution and gate incidents; dashboard hooks emit real values or `unknown`.
- **Deploy (2 tasks):** idempotent Base Sepolia deploy scripts deploy with the scan-only gate engaged; the mainnet checklist is signed off with audit sign-off, key custody, and monitoring — and explicitly records that no live execution happens without the separate, out-of-scope release step.

---

## P14 — Prediction Market Automation: Deep Spec

> Project: P14 Prediction Market Automation | Category: markets | fee_bps: 15 | risk_score: 0.60
> target_apr: 0.16 | min_capital_usd: 25 | max_alloc_pct: 0.10 | live_blocked: **false**
> Gates: `kelly_cap`, `polyclaw_wallet_only`
> Live path constraint (catalog.py): **"Live path is Polyclaw wallet, never treasury."**
> Reference: ~/workspace/sincor2/src/sincor2/defi/catalog.py (ProtocolSpec 14, P14_PREDICTION)

## 1. What/How

P14 is an automated prediction-market trading agent that ingests market data (Polymarket CLOB via the OpenClaw integration surface), produces calibrated win probabilities, sizes positions with fractional Kelly, and executes **only through the Polyclaw wallet** — a dedicated, isolated hot wallet. The treasury EOA is never touched: every signing call passes through a fail-closed wallet adapter that hard-blocks treasury keys. Settlement and pricing are kept separate the way Polymarket itself does it: conditional-token settlement (Gnosis CTF: 1 YES + 1 NO = 1 USDC, always mintable/burnable 1:1) with CLOB pricing on top.

Step-by-step flow:

1. **Ingest** — `MarketDataFeed` polls the Polymarket CLOB (order books, market metadata) and resolution sources. Requirements: retry with backoff, rate-limit handling, staleness check (data older than 60s is marked unusable for sizing — never trade on stale books). Minimum market quality bar: ≥ $10k 24h volume and a resolvable oracle (UMA Optimistic Oracle with its ~2h dispute window, or Chainlink Data Streams for short-duration markets). Markets are allowlisted by category in the YAML.
2. **Forecast** — `ForecastEngine` consumes market prices + event metadata and outputs calibrated win probabilities with confidence intervals. Calibration is tracked continuously via Brier score against resolved markets; the engine must beat the naive baseline (market-implied price as probability) on the scripted corpus. Confidence² penalizes low-confidence estimates in sizing.
3. **Edge check** — a trade is only considered when `|p_model − p_market| ≥ 2%` (minimum edge) AND expected value ≥ 3% AND signal confidence ≥ 60%. These three are hard vetoes, not suggestions.
4. **Kelly sizing** — `KellySizer` computes `f* = (p·b − q) / b` (p = model probability, q = 1−p, b = net odds from market price), then applies the `kelly_cap` gate: `f_deployed = f* × 0.25 (quarter-Kelly) × confidence²`, hard-clamped to the catalog max of 10% of bankroll per position. The cap triggers a logged warning whenever it binds. Stakes can never exceed the cap under randomized fuzzing.
5. **Wallet isolation** — `PolyclawWalletAdapter` is the ONLY live execution path. On every call it fail-closed verifies the signing key is the Polyclaw wallet key and not the treasury key; any treasury-key usage attempt is blocked and logged. Wallet state is isolated from strategy logic (strategy proposes, adapter disposes). Red-team test: attempts to route through treasury keys must all fail.
6. **Order lifecycle** — `OrderLifecycleManager` opens positions via the adapter (GTC maker orders preferred — Polymarket charges 0% on most markets and maker entry avoids taker cost; FOK for size-certain exits), tracks to resolution, handles redemption/payouts, and reconciles expected vs realized PnL to the cent. Near resolution, new entries are blocked and open positions are force-reviewed (oracle settlement risk guard).
7. **Risk limits** — per-market loss limit 3% of bankroll; daily loss circuit breaker at 5% of bankroll halts all new positions (resets only via the documented manual procedure — never auto-reset); portfolio cap 60% of bankroll deployed; no single category > 40% of deployed capital; max 90 days to resolution (avoids illiquid long-duration markets).
8. **Fee routing** — 15 bps (0.15%) of *settled* PnL (not notional) routes to Treasury `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`, recorded in a fee ledger with per-trade attribution. The ledger must reconcile to the treasury-bound total to the cent.
9. **Resolution** — positions resolve via the market's oracle (UMA optimistic oracle dispute window respected — no early redemption claims); winnings redeem 1 USDC per winning share per the CTF invariant.

Numeric parameters (locked):

| Parameter | Value |
|---|---|
| Protocol fee to Treasury | 15 bps (0.15%) of settled PnL |
| Target APR | 16% (edge-driven; lumpy, not smooth) |
| Kelly fraction | 0.25 (quarter-Kelly), × confidence² |
| Max allocation per position | 10% of bankroll (catalog cap) |
| Min edge to trade | 2% probability gap vs market |
| Min expected value | 3% |
| Min signal confidence | 60% |
| Min capital | $25 |
| Per-market loss limit | 3% of bankroll |
| Daily loss circuit breaker | 5% of bankroll, manual reset only |
| Max portfolio deployed | 60% of bankroll |
| Max category concentration | 40% of deployed capital |
| Max days to resolution | 90 |
| Min market liquidity | $10k 24h volume |
| Data staleness cutoff | 60s — stale data unusable for sizing |
| Forecast calibration bar | Brier score beats naive baseline on scripted corpus |
| Order preference | GTC maker (0% fee markets); FOK for exits |

Actors: market data feed (off-chain), forecast engine (Python), Kelly sizer (Python), Polyclaw wallet adapter (signing boundary), order lifecycle manager (Python), Polymarket CLOB (external execution venue), UMA/Chainlink oracles (external resolution), Treasury (fee sink), founder signer (breaker reset + live capital approval).

Key modules: `src/sincor2/agents/p14_prediction/` — `api.py` (IMarketDataFeed, IForecastEngine, IKellySizer, IPolyclawWalletAdapter), `forecast.py`, `sizer.py`, `wallet.py`, `lifecycle.py`, `feed.py`; `config/agents/p14-prediction.yaml` (dry_run=true default, polyclaw_wallet_only=true); `scripts/deploy_p14.py`, `scripts/sim_p14_backtest.py`.

## 2. Why

Who pays and why: the agent earns from probability mispricing — markets where the model probability diverges from the CLOB price by ≥ 2%. Documented edges in the wild: oracle-lag arbitrage on short-duration markets (Chainlink settlement price updating 10–45s behind CEX), weather markets priced off stale model runs vs fresh GFS ensembles, and mean-reversion after flash moves. The agent is a maker-first liquidity provider capturing these gaps.

Revenue path to Treasury: 15 bps on settled PnL. At 16% target APR on a $100k bankroll = $16k annual PnL → $24/yr to Treasury. Small per dollar by design; the catalog's volume-over-vanity directive applies — P14's value compounds across bankrolls and its forecast signal is reusable by other swarm modules.

What breaks without this: without the Polyclaw-only gate, a strategy bug can sign with treasury keys — the catastrophic failure mode this project's architecture exists to prevent. Without quarter-Kelly + confidence², noisy probability estimates produce full-Kelly overbets and ruin. Without the daily breaker, a miscalibrated regime (e.g., oracle dispute, ambiguous resolution criteria) compounds losses intraday.

## 3. Build Stack

- **Python 3.11**, `mypy`-clean typed interfaces. HTTP/WebSocket client for the Polymarket CLOB (use the official `py-clob-client` — pinned; the repo's `requirements.lock` was missing it as of 2026-09-25, so pin explicitly). `eth_account` for EIP-712 order signing through the Polyclaw key only. `numpy`/`scipy` for calibration math; `pydantic` for feed schema validation.
- **Reused from repo (verified on disk):**
  - `~/workspace/sincor2/verticals/trading/polymarket_agent.py` (existing Polymarket agent — ingest patterns, market metadata handling)
  - `~/workspace/sincor2/verticals/trading/polyclaw/` (Polyclaw execution surface — the wallet adapter builds on this)
  - `~/workspace/sincor2/verticals/trading/openclaw_agent.py` (OpenClaw integration surface referenced in the project definition)
  - `~/workspace/sincor2/src/sincor2/defi/catalog.py` (TREASURY constant, ProtocolSpec enforcement)
  - `~/workspace/sincor2/src/sincor2/forecasting_engine.py` (TOA forecast hook for the forecast engine)
  - `~/workspace/sincor2/src/sincor2/treasury_settlement.py` (fee ledger pattern)
- **Built new:** the calibrated forecast engine with Brier tracking, quarter-Kelly sizer with confidence² penalty, fail-closed Polyclaw wallet adapter with treasury-key red-team tests, order lifecycle manager with to-the-cent PnL reconciliation, the daily circuit breaker with manual reset, the fee ledger with per-trade attribution.
- **Not reused:** the agent does not use the onchain hook system (no Solidity in P14 — execution is off-chain CLOB signing; settlement is Polymarket's contracts).

## 4. Tip

- **Resolution risk is the silent killer.** Ambiguous resolution criteria, UMA disputes, and invalid-market outcomes (50/50 split payouts) destroy edge models. Hard veto: never trade a market whose resolution criteria you cannot quote back.
- **Maker, not taker.** Polymarket charges 0% on most markets but taker flow still pays spread; the lifecycle manager defaults to GTC maker orders and only uses FOK market orders for exits. Paper-trade (dry_run=true) until the backtest shows Brier improvement AND positive net PnL after the 15 bps fee.
- **Calibration > accuracy.** A model that is right 55% of the time but miscalibrated will overbet via Kelly and ruin the bankroll. Track Brier score weekly per category; any category with Brier > 0.25 (worse than random) triggers estimator recalibration, not more capital.
- **The wallet adapter is a security boundary.** Treat `wallet.py` like a firewall: minimal code, maximal tests, no strategy logic inside. The red-team test (attempt treasury-key usage, expect block + log) must run in CI on every commit.
- **Monitoring signals:** Brier score per category (warn > 0.22, halt > 0.25), daily PnL vs 5% breaker, fee ledger reconciliation delta (must be 0), data staleness rate, UMA dispute watchlist, maker fill rate.
- **Status feed rule:** report real values or `unknown`, never fabricated `healthy`.

## 5. Acceptance criteria

1. Every spec flow (ingest, forecast, size, execute, settle) maps to a typed interface function; `mypy` passes on the module.
2. Forecast engine beats the naive baseline Brier score on the scripted historical corpus and persists calibration history.
3. Kelly sizer: stakes never exceed `f* × 0.25 × confidence²` and never exceed 10% of bankroll across randomized fuzzing; cap binding triggers a logged warning.
4. Polyclaw adapter: red-team test attempting treasury-key usage — 100% of attempts blocked and logged; dry-run orders execute end-to-end; strategy logic cannot reach signing keys.
5. Order lifecycle: scripted market (open → resolve → redeem) completes in dry-run with PnL reconciled to the cent; near-resolution entry blocking works.
6. Risk limits: breaching per-market (3%), daily (5%), or allocation (10%) limits halts new orders with a named alert; the breaker resets only via the documented manual procedure.
7. Fee routing: 15 bps on settled PnL routes to `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`; fee math unit-checked against scripted settlements; ledger reconciles to the treasury-bound total.
8. Backtest: `scripts/sim_p14_backtest.py` runs green with realized PnL, max drawdown, Brier score, and fee totals logged per market.
9. Live path constraint: no code path can sign with or transfer from the treasury key — proven by test, not by convention.

## 6. Risk gates

- **Catalog:** `live_blocked: false` — P14 is one of the few projects allowed a live path. **But the live path is the Polyclaw wallet ONLY, never the treasury** (`polyclaw_wallet_only` gate). Any live execution attempt that is not signed by the Polyclaw key is blocked fail-closed.
- **Risk budget:** risk_score 0.60 → max_alloc_pct 0.10 per position; quarter-Kelly (0.25) with confidence² penalty as the sizing governor; min $25 capital.
- **Stays dry-run:** `dry_run=true` is the YAML default; live mode requires explicit config change + founder approval of live capital limits + wallet-isolation re-verification. The backtest must be green before any live discussion.
- **Auditor + founder-signer path:** audit-prep package must formally evidence the Polyclaw-only live path (wallet-isolation proof); the mainnet checklist requires audit sign-off, backtest green, key-management plan, and a rollback procedure. Daily breaker reset is a manual founder procedure.
- **Kill conditions:** Brier > 0.25 in any traded category halts new positions in that category; daily loss ≥ 5% halts all new positions; any UMA dispute on a held market freezes new entries in that market until resolution finalizes.

## 7. Phase definition of done

- **Spec (3 tasks):** `docs/architecture/P14_PREDICTION.md` (with data-flow diagram and the wallet-isolation boundary), the Python interfaces in `api.py`, and the economics model with the Kelly fraction table and fee-accrual formula exist; a reviewer can state both gate semantics and validate the 16% APR derivation — no implementation required yet.
- **Scaffold (2 tasks):** `src/sincor2/agents/p14_prediction/`, config, test stubs, and `scripts/deploy_p14.py` stub exist; the agent imports cleanly, boots in dry-run from the YAML, and CI collects the new test files with zero errors.
- **Core (7 tasks):** forecast engine, Polyclaw adapter, Kelly sizer, market-data integration, order lifecycle, risk limits, and fee routing are implemented; a scripted market lifecycle runs end-to-end in dry-run with to-the-cent PnL reconciliation, the red-team treasury-key test passes, and stakes are provably Kelly-capped under fuzzing.
- **Testing (4 tasks):** unit, integration, property/fuzz (zero violations), and historical backtest simulation all pass; the backtest logs PnL, max drawdown, Brier score, and fee totals per market.
- **Audit (3 tasks):** the audit-prep package is complete with threat model (oracle/resolution manipulation, wallet key compromise, Kelly overbet from miscalibration) and wallet-isolation proof; all findings remediated with linked regression tests; the performance report shows measurable decision-loop latency reduction with identical outputs on the regression corpus.
- **Docs (3 tasks):** technical docs fully specify the sizing formulas and wallet-isolation design; the agent YAML boots the agent in dry-run and the runbook's breaker procedure matches the implemented circuit breaker; dashboard hooks emit sample payloads validating against the feed schema.
- **Deploy (2 tasks):** `scripts/deploy_p14.py` deploys to staging idempotently in a container with the wallet credentials path never in the repo; the mainnet readiness checklist exists with every item marked pass/blocked and an owner — no live capital without audit sign-off and founder approval.

---

## P16 — Best-Execution DEX Aggregator: Deep Spec

> Project: P16 Best-Execution DEX Aggregator | Category: execution | fee_bps: 6 | risk_score: 0.26
> target_apr: 0.02 | min_capital_usd: 10 | max_alloc_pct: 0.50 | live_blocked: true
> Gates: `min_out`, `venue_whitelist`
> Reference: ~/workspace/sincor2/src/sincor2/defi/catalog.py (ProtocolSpec 16, P16_DEX_AGG)

## 1. What/How

P16 is a DEX aggregator for Base: an off-chain Python **route optimizer** that splits orders across allowlisted venues to maximize realized output, and an on-chain **RouterGateway** that executes the committed route with `min_out` protection. The design follows the 1inch Pathfinder shape — graph over liquidity sources, multi-split routing — but scoped to 2–3 Base venues, with TOA slippage forecasts reweighting route selection each cycle.

Step-by-step flow:

1. **Quote** — the Python agent takes a `QuoteRequest(token_in, token_out, amount_in, slippage_bps)` and queries each allowlisted venue adapter's `quote()` view: Uniswap V3 (exact-output math per fee tier: 5/30/100 bps), Uniswap V4 (via the hook-aware quoter), Aerodrome (volatile `x·y=k` and stable-curve pools). Adapter quotes must match fork snapshots of real pools to 1-wei tolerance.
2. **Optimize** — the split solver maximizes the aggregator objective: `maximize Σ out(routeᵢ) − gas_cost − fees  subject to Σ in = amount_in`, with gas-cost penalization per additional split (each extra venue leg costs execution gas; a split is only added when its marginal output gain exceeds its marginal gas). Constraints: allocations sum to 100%, at most 3 splits per swap, each leg ≥ $10 (dust threshold — smaller legs are merged or dropped), and the committed `minOut = quoted_out × (1 − slippage_bps/10000)` with default slippage 50 bps.
3. **TOA reweight** — before finalizing, the agent consumes the TOA slippage/forecast feed per venue pair (wired via `src/sincor2/forecasting_engine.py` hook + agent YAML). If the forecast shows venue degradation (e.g., predicted slippage spike on Aerodrome volatile pools), route weights shift measurably — proven by an integration test with a degraded-forecast JSON.
4. **Commit & execute** — the agent submits the route to `RouterGateway.execute(route, minOut)` with the 6 bps protocol fee taken in the input token and swept to Treasury `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` in the same transaction. The gateway calls each venue adapter's `swap()` inside non-bricking try/catch: one venue reverting never bricks the route — the failed leg's input is returned to the caller and the remaining legs settle.
5. **min_out enforcement** — the whole swap reverts if realized output < `minOut`. The quote is committed at execution time (fresh `quote()` in the same transaction or a 1-block-fresh signed quote), so a stale off-chain quote cannot be exploited: manipulated-quote test reverts, honest-quote test passes.
6. **Venue allowlist** — `VenueRegistry` holds the allowlist; RouterGateway reverts on any non-allowlisted adapter. Add/remove is admin + 24h timelock; removal takes effect only after the timelock elapses (tested: a swap through a removed venue reverts post-timelock).
7. **WETH path** — native ETH is wrapped to WETH at the gateway entry and unwrapped at exit when the user requests ETH out; the wrap/unwrap is inside the atomic transaction.

Numeric parameters (locked):

| Parameter | Value |
|---|---|
| Protocol fee to Treasury | 6 bps (0.06%) of input token, every executed swap |
| Target APR | 2% (execution savings + fee capture; this is infrastructure, not a yield product) |
| Default slippage tolerance | 50 bps (minOut = quoted × 0.995) |
| Max splits per swap | 3 venues |
| Dust threshold per leg | $10 minimum |
| Execution gas target | < 300k gas for a routed swap on the reference fork |
| Min capital | $10 |
| Max allocation per tick | 50% of router-managed capital |
| Venue add/remove timelock | 24h |
| Quote freshness | committed at execution (same-tx or 1-block-fresh) |
| Adapter quote tolerance | 1 wei vs fork snapshot |
| Allowlisted venues (genesis) | Uniswap V3, Uniswap V4, Aerodrome on Base |

Actors: swapping user/agent (any caller), Python routing agent (off-chain optimizer), venue adapters (per-DEX Solidity), RouterGateway (execution + fee sweep), VenueRegistry admin (timelocked), TOA forecast feed (off-chain input), Treasury (fee sink).

Key contracts/modules: `RouterGateway.sol` (ReentrancyGuard, SafeERC20, try/catch per venue, minOut check, 6 bps sweep, fee-ledger event), `IVenueAdapter.sol` (`quote()` view + `swap()` with caller-supplied minOut), `UniswapV3Adapter.sol`, `AerodromeAdapter.sol`, `VenueRegistry.sol` (timelocked allowlist), Python `src/sincor2/defi/p16_dex_aggregator/` (`types.py` QuoteRequest/RouteQuote dataclasses, `optimizer.py` split solver, `agent.py` with TOA hook), `agents/p16-dex-aggregator.yaml`.

## 2. Why

Who pays and why: anyone swapping on Base through SINCOR pays the 6 bps execution fee because the router delivers better net execution than single-venue swaps — 1inch's public testing shows 1–3% better rates on large swaps via splitting, and the gas-aware optimizer guarantees the improvement is net of costs. Internal consumers (the P10 arb scanner's execution leg, treasury rebalancing, agent portfolio swaps) are the first users; external agents via the A2A marketplace are the scale story.

Revenue path to Treasury: 6 bps on input, every swap, swept on-chain with a fee-ledger event. At $1M daily routed volume = $60/day = ~$21.9k/yr. This is a volume game — the catalog's volume-over-vanity directive is the strategy. The fee is deliberately tiny (1inch charges 0% and monetizes elsewhere; P16 charges 6 bps because the Treasury is the product).

What breaks without this: without `min_out`, stale quotes become theft vectors (adversarial quoter drains the difference). Without the venue allowlist, a malicious adapter can be routed user funds. Without gas-aware splitting, the optimizer "improves" quotes by adding legs whose gas cost exceeds the price gain — the classic aggregator failure mode where gross output rises and net value falls.

## 3. Build Stack

- **Solidity 0.8.24.** Libraries: OpenZeppelin v5 (`ReentrancyGuard`, `SafeERC20`); Uniswap V3 quoter math reimplemented in the adapter as a view (no external quoter dependency at execution — the adapter computes from pool state); Aerodrome `Router` interface for volatile/stable pools. No proxies: gateway, adapters, and registry are immutable; the registry's *entries* change via timelock, not the code.
- **Python 3.11**: the split solver is constrained optimization with gas penalization — implement with `scipy.optimize` (SLSQP) with a pure-Python fallback (grid + local search) so the agent has no hard scipy dependency in minimal installs. `web3.py` for quote/commit, `eth_account` public API for signing. Reused from repo: `src/sincor2/defi/catalog.py` (TREASURY constant, ProtocolSpec caps), `src/sincor2/defi/yield_aggregator.py` (EXECUTE_LIVE env pattern — the router defaults to dry-run quoting, never executing, until explicitly enabled), `src/sincor2/forecasting_engine.py` (TOA slippage forecast hook), `src/sincor2/treasury_settlement.py` (fee sweep accounting pattern), `marketplace/settlement.py` (settlement event patterns), `onchain/src/hooks/MoebiusMEVHook.sol` (document the router's interaction with MEV flow — the router should prefer private submission where available).
- **Exact integration points (verified on disk):**
  - `~/workspace/sincor2/src/sincor2/defi/p16_dex_aggregator/` (new module)
  - `~/workspace/sincor2/onchain/src/p16-dex-aggregator/` (new contracts: `interfaces/`, `adapters/`, `RouterGateway.sol`)
  - `~/workspace/sincor2/agents/p16-dex-aggregator.yaml` (agent config: budgets, heartbeat, TOA hook)
  - TOA feed via `src/sincor2/forecasting_engine.py`
- **Reused vs built new:** reused — catalog/Treasury/EXECUTE_LIVE conventions, TOA hook, settlement event patterns, MEV-hook awareness. Built new — the gas-penalized split solver, the three venue adapters with 1-wei quote fidelity, the timelocked venue registry, the try/catch non-bricking multi-leg executor, the committed-quote minOut check.

## 4. Tip

- **Net, not gross.** The optimizer's objective must be `output − gas − fees`, never raw output. Test: on 100 synthetic quote sets the optimizer must beat best-single-venue routing on *net* realized output — if it only wins gross, the gas penalization is broken.
- **Stale quotes are the attack surface.** The route is computed off-chain on a snapshot; if state moves before inclusion, realized fill drifts. The committed-quote check (same-tx re-quote or 1-block-fresh signed quote) plus `minOut` revert is the defense. Never let the Python agent's quote be trusted by the gateway without re-validation.
- **One bad venue must not brick the route.** The try/catch isolation is load-bearing: a venue that starts reverting (paused pool, hacked router) degrades to fewer legs, not a stuck swap. Test the reentrancy attempt against the callback path explicitly.
- **Fee-on-transfer tokens** need a measured-input rule: the gateway measures actual received balance (balance-before/after), not the nominal input amount, before computing the 6 bps fee and the split allocations.
- **Monitoring signals:** route fill rate, realized vs forecast slippage delta, per-venue revert rate, Treasury fee inflow per hour, optimizer net-vs-gross win rate, quote-freshness violations.
- **Status feed rule:** report real values or `unknown`, never fabricated `healthy` (per STATUS_FEED_SCHEMA).

## 5. Acceptance criteria

1. Adapter `quote()` matches fork snapshots of real Uniswap V3 and Aerodrome pools to 1-wei tolerance.
2. Split optimizer on 100 synthetic quote sets: allocations always sum to 100%, never violate min_out, max 3 splits, dust legs (< $10) never emitted, average *net* realized output beats best-single-venue routing.
3. TOA forecast integration: a degraded-venue forecast JSON measurably shifts recommended route weights in an integration test.
4. `min_out`: whole swap reverts if realized output < committed minOut (manipulated-quote test reverts, honest-quote test passes).
5. Fee: fork test asserts the treasury balance delta equals exactly 6 bps of input on a real routed swap; fee-ledger event emitted per swap.
6. Allowlist: RouterGateway reverts on any non-allowlisted adapter; removal takes effect only after the 24h timelock elapses.
7. Non-bricking: one venue reverting does not brick the route (failed leg's input returned); reentrancy attempt against the callback path reverts.
8. Gas: routed-swap execution under 300k gas on the reference fork (documented gas report).
9. Fuzz: 10k runs, zero violations of (realized ≥ minOut) ∧ (fee == exactly 6 bps) ∧ (only allowlisted venues execute).
10. Base mainnet-fork simulation: ≥ 5 real-pool swaps settle with realized output ≥ minOut and correct treasury fee deltas.
11. solc 0.8.24 compiles with zero errors and zero warnings.

## 6. Risk gates

- **Catalog:** `live_blocked: true` — the router defaults to dry-run quoting; live execution requires the audit + founder-signer path. Gates `min_out` and `venue_whitelist` are enforced on-chain in the gateway, not in the Python agent.
- **Risk budget:** risk_score 0.26 → max_alloc_pct 0.50 per tick; min $10 capital. The router never holds user funds between transactions (pass-through); the residual risk is a bad route, bounded by minOut.
- **Stays dry-run:** quoting, optimization, and fork simulation. Live swaps only after the audit-prep package passes and the mainnet checklist is signed off.
- **Auditor + founder-signer path:** threat model must cover malicious venue adapter, stale TOA forecast, and admin key compromise; venue add/remove is 24h-timelocked; the mainnet checklist confirms timelock active, monitoring live, treasury address verified, and dry-run default honored.
- **Kill conditions:** per-venue revert rate > 5% over 1h auto-excludes the venue from routing (Python-side) pending review; realized-vs-forecast slippage delta > 100 bps sustained over 1h halts new route commits.

## 7. Phase definition of done

- **Spec (3 tasks):** `docs/architecture/P16_DEX_AGG_ARCHITECTURE.md`, the router/adapter interfaces plus Python dataclasses, and the 6 bps fee model exist; a reviewer can onboard a venue, state the allowlist update procedure, and compute the exact treasury take for any routed swap from the spec alone — no implementation required yet.
- **Scaffold (2 tasks):** `onchain/src/p16-dex-aggregator/`, `src/sincor2/defi/p16_dex_aggregator/`, the test file, and the agent YAML exist; the contract skeleton compiles under solc 0.8.24 and the Python agent imports and answers a health ping.
- **Core (7 tasks):** venue adapters, split optimizer, TOA feed, min_out enforcement, fee sweep, allowlist registry, and edge cases are implemented; adapter quotes match fork snapshots to 1 wei, the optimizer beats single-venue routing net of gas on 100 synthetic sets, and a manipulated quote provably reverts.
- **Testing (4 tasks):** unit, integration, fuzz (10k runs, zero invariant violations), and Base mainnet-fork simulation (≥ 5 real-pool swaps) all pass; the fork report records quotes, splits, execution, and fee deltas.
- **Audit (3 tasks):** the audit-prep package passes the repo's audit-readiness checklist with zero missing artifacts; all findings remediated with linked regression tests and no new warnings; the gas report documents routed-swap execution under 300k gas.
- **Docs (3 tasks):** technical docs let a new builder add a third Base venue adapter from docs alone; the agent YAML validates against the repo agent schema and the runbook covers the top 5 failure modes; monitoring hooks emit structured metrics with `unknown`-on-stale semantics.
- **Deploy (2 tasks):** `scripts/defi/p16/deploy_testnet.py` deploys gateway, adapters, and registry to Base Sepolia with seeded allowlist entries and a test swap executes end-to-end; the mainnet readiness checklist is signed off item-by-item with evidence links — nothing deploys to mainnet until all items pass.

---

## P17 — On-Chain Options Protocol (Deep Spec)

Catalog: `P17_OPTIONS`, category derivatives · fee_bps **15** · risk_score **0.52** · target_apr **0.10** ·
min_capital_usd **200** · max_alloc_pct **0.12** · live_blocked **true** · gates (`covered_only`, `expiry_band`).
Treasury: `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`.

## 1. What/How

A covered-only European options protocol on Base. No AMM market-making, no naked shorts — every option
token is minted 1:1 against locked collateral, so uncovered exposure is unrepresentable in the
contract state, not merely disallowed by policy. Two writer vaults:

- **Covered calls.** Writer deposits 1 unit of underlying (e.g. WETH) per option minted. Buyer pays
  premium in USDC. At expiry, settlement price `P_exp` (Chainlink spot, staleness-guarded):
  if `P_exp > K`, buyer may exercise during the 24h exercise window — paying `K` USDC and receiving
  1 underlying; writer keeps `K` + premium. If `P_exp <= K`, options expire worthless; writer
  withdraws underlying + premium.
- **Covered puts.** Writer deposits `K` USDC per option minted (locked stable). If `P_exp < K`,
  buyer exercises: delivers 1 underlying, receives `K` USDC; writer keeps premium + delivered
  underlying. If `P_exp >= K`, writer withdraws `K` USDC + premium.

**Step-by-step flow (one series):**
1. Series manager (timelocked role) lists a series: underlying, strike `K`, expiry `T`, call/put.
   `T` must satisfy the expiry_band gate: **7 days ≤ T ≤ 90 days**, and `T` must be one of the
   fixed tenors {7, 14, 30, 60, 90} days to prevent liquidity fragmentation.
2. Writer calls `write(n)`: deposits `n` collateral units; vault mints exactly `n` OptionToken
   (ERC-20, immutable strike/expiry/isCall metadata, mintable only by the vault).
3. Buyer calls `buy(n)` on the agent-quoted premium: premium priced off-chain by the agent
   (Black-Scholes) and verified on-chain by the pricer (see below). **15 bps of premium → Treasury.**
4. At expiry: permissionless `settle(seriesId)` snapshots `P_exp`. OTM options burn to zero;
   ITM options become exercisable for 24h. After the window, `sweep(seriesId)` releases all
   writer collateral and burns remaining tokens.
5. Writer calls `withdraw()` only when no live options reference their collateral share.

**On-chain premium pricer (bounds, not discovery):** the agent quotes BS premium using spot from
Chainlink, volatility from a realized-vol feed (30-day default; Chainlink-style vol feeds span
24h/7d/30d lookbacks), risk-free rate **4% annualized**. The on-chain pricer recomputes BS and
accepts the trade only if `|quoted − model| ≤ 2%` and premium sits inside hard rails:
**floor 0.5% of notional, cap 50% of notional**. Volatility input is clamped to **[10%, 300%]**
annualized to bound oracle-spike damage.

**Numeric parameters (normative):**
- fee_bps = 15 on every premium payment, routed to Treasury (no burn).
- covered_only: `totalMinted(series) ≤ lockedCollateral(series)` enforced as a `require` on mint;
  withdrawal blocked while `writerLocked > 0`.
- expiry_band: 7d ≤ T ≤ 90d; fixed tenors {7,14,30,60,90}; exercise window 24h post-expiry.
- Series open-interest cap: **10,000 options**; per-writer cap: **20% of series OI**.
- Writer collateralization: **100%** (1:1 physical cover — no overcollateralization needed).
- max_alloc_pct: **0.12** of a single DeFi-OS tick's capital may flow to P17 venues.
- Dust guard: minimum premium **$1.00** equivalent; minimum write **1 option**.
- Oracle staleness: spot feed older than **2h** reverts pricing/exercise/settlement.

## 2. Why

- **Who pays:** option buyers (hedgers, speculators) pay premium; writers (yield seekers) lock
  capital. Both sides currently pay CEX/Deribit spreads and custody risk to trade these payoffs.
- **Why they pay:** on-chain covered calls turn idle ETH into yield (target_apr 0.10 on deployed
  writer capital); buyers get permissionless downside/upside exposure with self-custodied
  settlement — no account, no withdrawal limits.
- **Revenue path to Treasury:** 15 bps on every premium payment. At $1M monthly premium volume
  that is $1,500/month protocol revenue, scaling linearly with OI — the DeFi-OS ranks P17 by
  exactly this inflow (P26 `rank_by_fee`).
- **What breaks without this:** SINCOR's idle treasury/agents' ETH earns 0% and options flow
  stays off-chain; the ecosystem has no native volatility product, so hedging demand leaks to
  Deribit/Lyra-style venues and their fees never touch the Treasury.

## 3. Build Stack

- **Solidity 0.8.24**, OpenZeppelin v5: `ERC20`, `ReentrancyGuard`, `AccessControl`,
  `TimelockController` (48h delay on series listing + parameter changes), `SafeERC20`.
- **Oracles:** Chainlink spot feeds (Base) + realized-vol feed; adapter pattern so a Pyth-style
  feed can be swapped without touching the vault.
- **Python agent:** `src/sincor2/defi/p17_options/` — BS pricer (off-chain quoting), series
  manager, settlement keeper (permissionless `settle`/`sweep` cron), TOA hook for revenue
  forecasting. Reuses repo conventions: agent YAML in `agents/`, `STATUS_FEED_SCHEMA` reporting
  (report `unknown`, never `healthy`, on stale data).
- **Tests:** eth-tester + py-evm (matches repo onchain suites), solc 0.8.24; fork sims on Base
  fork with real Chainlink answers.
- **Integration points (verified to exist):** `onchain/src/` for the new `p17-options/`
  contracts (sibling to `ComplianceGuard.sol`); `src/sincor2/defi/` for the Python package
  (sibling to `catalog.py`, `yield_aggregator.py`); `agents/` for the YAML config;
  SINCOR dashboard for monitoring hooks.
- **Reused vs new:** new = OptionToken, CoveredVault, on-chain BS pricer, oracle adapters.
  Reused = OZ libraries, repo test/agent/dashboard conventions, treasury fee-routing pattern
  (fee → `0x09E2…9612Ac`).

## 4. Tip — operational gotchas, failure modes, monitoring

- **Oracle staleness is the #1 killer.** A stale Chainlink answer at expiry mis-settles the
  whole series. Monitor feed `updatedAt` age; alert if >30 min, hard-revert paths at >2h.
- **Near-expiry miscalibration.** Realized-vol pricing systematically misprices the final
  hours (too much IV priced in near expiry — documented in prediction-market oracle research).
  The 7-day minimum tenor exists for this reason; never list 0DTE-style series.
- **Writer griefing via dust.** Minimum premium $1 and per-series OI cap 10,000 bound this.
- **Exercise-window congestion.** If gas spikes during the 24h window, ITM holders may miss
  exercise — the auto-settle-at-expiry fallback (ITM-but-unexercised settles at `P_exp`)
  removes the hostage dynamic.
- **Volatility feed manipulation.** Vol input clamped [10%, 300%]; premium rails
  [0.5%, 50%] of notional bound the damage of any single bad print.
- **Monitor in production:** aggregate coverage ratio (must read exactly ≥1.0), open interest
  per series, premium volume, treasury fee inflow per day, oracle staleness seconds,
  fraction of series settled permissionlessly vs via keeper.

## 5. Acceptance criteria

1. **Covered-only invariant:** for every series, `minted ≤ lockedCollateral` holds across
   10,000 randomized fuzz runs; any naked-short mint attempt reverts (tested explicitly).
2. **Pricing fidelity:** on-chain pricer matches the spec's worked BS examples within **1%**;
   out-of-band expiries (T < 7d or > 90d) revert.
3. **Settlement correctness:** on a Base fork with 20+ real price paths, ITM exercise pays
   exactly `max(0, P_exp − K)` (calls) / `max(0, K − P_exp)` (puts) to the wei; no series ends
   undercollateralized.
4. **Fee routing:** Treasury receives exactly 15 bps of every premium; fee math matches worked
   examples to the wei.
5. **Gas:** `exercise` completes under **250k gas** on the reference fork.
6. **Access control:** unauthorized series listing / parameter change reverts; pause halts
   writes but never traps withdrawable collateral.

## 6. Risk gates

- `live_blocked = true`: the Python agent emits dry-run intents only; no mainnet series until
  the auditor sign-off + founder-signer deploy path completes.
- Catalog gates enforced on-chain: `covered_only` (mint-time require), `expiry_band`
  (listing-time require).
- Money-path rule: any change touching premium math, collateral accounting, or settlement
  requires external audit findings closed + 48h timelock + founder-signer execution.
- Dry-run default stays on in every environment until the mainnet-readiness checklist is
  signed with evidence links.

## 7. Phase definition of done

- **Spec:** `docs/architecture/P17_OPTIONS_ARCHITECTURE.md` reviewed; every catalog gate maps
  to a named on-chain enforcement point; worked BS examples recompute by hand.
- **Scaffold:** full repo tree exists (`onchain/src/p17-options/`, `src/sincor2/defi/p17_options/`,
  `tests/pytest/test_p17_options.py`, `agents/p17-options.yaml`); contracts compile under
  solc 0.8.24; agent imports and answers a health ping.
- **Core:** all seven core contracts/tasks complete; covered-only invariant holds in tests;
  pricing within 1% of spec; exercise/settlement/expiry paths green including edge cases.
- **Testing:** unit + integration + 10k-run fuzz + 20-path fork simulation all green with the
  numeric bars in §5 met.
- **Audit:** audit-prep package passes the repo checklist with zero missing artifacts; all
  findings remediated with linked regression tests; gas report shows exercise < 250k.
- **Docs:** integrator can list and settle a series from docs alone; runbook covers oracle
  outage + expiry-crush; monitoring hooks emit per STATUS_FEED_SCHEMA.
- **Deploy:** Base Sepolia deployment verified with a full write→expiry lifecycle executed;
  mainnet-readiness checklist fully signed before any mainnet transaction.

---

## P19 — Decentralized Credit Underwriting (Deep Spec)

Catalog: `P19_CREDIT`, category credit · fee_bps **20** · risk_score **0.45** · target_apr **0.12** ·
min_capital_usd **100** · max_alloc_pct **0.15** · live_blocked **true** · gates (`score_floor`,
`concentration_cap`). Treasury: `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`.

Design lineage: Spectral/Cred-style behavioral scoring (MACRO 300–850 feature families) feeding
differentiated LTV ceilings (high-scoring wallets earn +3–5pp LTV boosts in live pilots), plus the
coordination-doc SUC-LOOPS pattern — ERC-6551 token-bound vaults so the underwriting agent can run
yield strategies inside the borrower's escrow without taking custody.

## 1. What/How

An on-chain credit pipeline: **score → max LTV → collateralized line → health-monitored position.**
There is no unsecured book — the score buys *better terms* (higher LTV), never *zero collateral*.

**Step-by-step flow:**
1. **Feature extraction (off-chain agent).** The scoring agent reads the borrower's wallet
   history on Base/Ethereum via RPC: wallet age (blocks since first tx), count of repaid vs
   liquidated positions on Aave/Compound/Morpho, average utilization, tx count, protocol
   diversity (distinct protocols touched), stablecoin vs volatile mix, and negative signals —
   interactions with known rug contracts and mixers. Mixer interaction clamps the score to 300.
2. **Attestation (on-chain).** An allowlisted attestor signs an EIP-712 `ScoreAttestation`
   {borrower, score, issuedAt, nonce}; the ScoreRegistry verifies the signature, checks the
   attestor is allowlisted, enforces replay protection (nonce per borrower), and stores
   {score, issuedAt}. Scores expire after **72h** (staleness policy); borrows with missing or
   stale scores revert. Attestor set is k-of-n multisig controlled (**k=2, n=3** initially).
3. **Score → max LTV (on-chain table, timelocked updates):**

   | Score band | Max LTV |
   |---|---|
   | 750–850 | 75% |
   | 650–749 | 65% |
   | 550–649 | 55% |
   | 450–549 | 45% |
   | 350–449 | 35% |
   | < 350 | ineligible (score_floor = 350) |

   Monotonicity is enforced: any table update that is not non-decreasing in score reverts.
   (This mirrors the Spectral pilot economics: +3–5pp LTV per tier above the 50% base.)
4. **Vault (ERC-6551).** Each borrower gets a token-bound account: the CreditVaultFactory calls
   the ERC-6551 registry `createAccount` (ERC-1167 minimal-proxy pattern) bound to a
   non-transferable borrower credit NFT. Collateral is locked *in the borrower's own TBA*;
   the underwriting agent can execute approved yield strategies inside the vault (strategy
   allowlist) without custody — the SUC-LOOPS pattern.
5. **Line issuance.** `draw(amount)`: requires `amount ≤ collateralValue × maxLTV(score)`.
   Zero-collateral or over-cap draws revert — this is the code-level no-unsecured-book rule.
   Collateral priced via Chainlink; staleness > 2h reverts draws.
6. **Health monitoring + circuit breaker.** Health factor HF = collateral×LTVmax / debt.
   If HF < **1.15**, the agent routes up to **25%** of vault collateral into the approved
   delta-hedge (perp short on the allowlisted venue) to arrest deterioration. If HF < **1.05**,
   permissionless liquidation via Dutch auction (starting at 5% discount, decaying over 6h).
7. **Concentration caps (on-chain):** single borrower ≤ **10%** of total originated book;
   single score band ≤ **40%**; originations breaching either revert.

**Fees:** 20 bps on originated principal + 20 bps on accrued interest, both to Treasury.
Example: $10,000 line at 8% APR for 1 year → $20 origination fee + $16 interest fee = $36 to Treasury.

## 2. Why

- **Who pays:** borrowers pay origination + interest spread; the spread is priced by score —
  thin-file wallets that prove repayment history get 75% LTV instead of the 50% base,
  i.e. 33% less capital locked for the same loan.
- **Why they pay:** overcollateralized DeFi excludes anyone who can't post 150%+; score-based
  LTV is the only on-chain mechanism that prices *behavioral* risk instead of raw collateral.
- **Revenue path to Treasury:** 20 bps on origination and on every interest accrual — the
  highest fee_bps in the swarm catalog, because credit is the highest-margin primitive.
  Ranked by P26 `rank_by_fee`, P19 is a top-3 treasury contributor at scale.
- **What breaks without this:** SINCOR lending stays one-size-fits-all; good borrowers
  subsidize bad ones through flat LTVs, and the protocol has no risk-priced credit product
  to offer the RWA (P08) and agent-portfolio (P25) pipelines.

## 3. Build Stack

- **Solidity 0.8.24**, OpenZeppelin v5 (`AccessControl`, `ReentrancyGuard`,
  `TimelockController` 48h for the LTV table and risk params, `SafeERC20`).
- **ERC-6551:** reference registry + account implementation; per-borrower TBA as ERC-1167
  minimal proxy; borrower identity = non-transferable ERC-721 credit NFT (SBT).
- **Python scoring agent:** `src/sincor2/defi/p19_credit/` — feature pipeline over Base/Eth
  RPC (or Etherscan-family API), logistic-regression score model (interpretable, auditable;
  no black-box ML on the money path), EIP-712 attestation signer, health-monitor keeper,
  TOA hook.
- **Integration points (verified):** `dae/identity.py` for attestation/identity plumbing;
  `verticals/compliance/` (`regulatory_agent.py`) — every origination is sanctions-screened
  before the line opens; `src/sincor2/defi/` for the package (sibling to `catalog.py`);
  `onchain/src/` for the new `p19-credit/` contracts.
- **Reused vs new:** new = ScoreRegistry, LTV table, CreditUnderwriter, ERC-6551 vault
  factory, scoring model. Reused = ERC-6551 reference contracts, OZ libs, Chainlink feeds,
  repo agent/test conventions, P20 gatekeeper for the sanctions screen.

## 4. Tip — operational gotchas, failure modes, monitoring

- **Wallet abandonment is the honest weakness.** Scores are address-bound; a borrower can
  walk away from a bad score with a fresh address. Mitigations, not denial: the credit SBT
  binds score history to a non-transferable identity, and the *collateral* (not the score)
  is what protects lenders — the score only tunes LTV within the 35–75% band, so a
  fresh-address attacker gains nothing without posting collateral first.
- **Attester compromise = the oracle attack.** k-of-n (2-of-3) signing, key rotation
  procedure in the runbook, and an on-chain attestor-revocation path that freezes new
  originations within one block of a compromise report.
- **Score staleness during volatility.** 72h expiry is the compromise: shorter would
  DoS borrowers with re-attestation gas; longer lets a deteriorating wallet borrow stale.
  Monitor median score age; alert if >48h.
- **ERC-6551 callback surface.** The TBA executes agent-approved strategies — the strategy
  allowlist is the security boundary; any new strategy needs the same audit bar as a
  contract upgrade.
- **Monitor:** originated credit by score band, utilization vs concentration caps, HF
  distribution (alert if >5% of book below 1.15), circuit-breaker fire count, attestation
  freshness, treasury fee inflow.

## 5. Acceptance criteria

1. **No unsecured book:** a draw with zero collateral reverts; a draw above
   `collateral × maxLTV(live score)` reverts — proven across 10,000 fuzz runs with zero
   `debt > collateral × maxLTV` violations and zero zero-collateral lines.
2. **Score integrity:** borrow with missing, stale (>72h), below-floor (<350), forged, or
   replayed attestation reverts; LTV table lookups match the spec table exactly and any
   non-monotonic table update reverts.
3. **Concentration:** an origination breaching the 10% per-borrower or 40% per-band cap
   reverts, tested at the boundary.
4. **Circuit breaker:** on a fork simulation with 20+ shock paths, no line breaches its LTV
   cap and the hedge route fires before any liquidation in every path.
5. **Fee routing:** 20 bps origination + 20 bps on accruals to Treasury, matching worked
   examples to the wei.
6. **Gas:** `drawdown` under **300k gas** on the reference fork.

## 6. Risk gates

- `live_blocked = true`: dry-run scoring and simulated originations only; no mainnet lines.
- Catalog gates on-chain: `score_floor` (350, enforced in `draw`), `concentration_cap`
  (10%/40%, enforced in `originate`).
- Money-path rule: LTV table, attestor set, strategy allowlist, and liquidation params are
  timelock-48h + auditor-signed + founder-signer executed. No exceptions.
- The scoring model itself is versioned; a model upgrade is treated as a parameter change
  (timelock + re-audit of the feature pipeline).

## 7. Phase definition of done

- **Spec:** `docs/architecture/P19_CREDIT_ARCHITECTURE.md` reviewed; the score→LTV table is
  fully specified with every score mapping to exactly one max LTV; the doc states precisely
  which check blocks an unsecured loan.
- **Scaffold:** tree exists (`onchain/src/p19-credit/`, `src/sincor2/defi/p19_credit/`,
  `tests/pytest/test_p19_credit.py`, `agents/p19-credit-underwriting.yaml`); compiles under
  solc 0.8.24; agent imports and answers a health ping.
- **Core:** registry, LTV table, ERC-6551 vaults, issuance, health monitor, concentration
  caps, and access control all complete; unsecured draws revert in tests.
- **Testing:** unit + integration + 10k fuzz + 20-path fork sim green against the §5 bars.
- **Audit:** package passes the repo audit-readiness checklist; threat model covers attester
  compromise, score-oracle manipulation, ERC-6551 callbacks; fairness/bias review note filed;
  all findings remediated with regression tests; gas report shows drawdown < 300k.
- **Docs:** a risk reviewer can validate every enforcement point from docs alone; runbook
  covers attester outage + mass health-drop; monitoring hooks emit per STATUS_FEED_SCHEMA.
- **Deploy:** Base Sepolia deployment verified; a scored borrow executes end-to-end;
  mainnet-readiness checklist signed with evidence links before any mainnet line.

---

## P20 — DeFi Compliance Automation (Deep Spec)

Catalog: `P20_COMPLIANCE`, category compliance · fee_bps **0** · risk_score **0.15** · target_apr **0.00** ·
min_capital_usd **0** · max_alloc_pct **0.00** · live_blocked **true** · gates (`kyc_aml`, `geo_block`).
This protocol produces **pass/fail decisions, not yield** — it is infrastructure the other 25
protocols depend on.

Design lineage: Chainlink ACE (pause → off-chain sanctions check → immutable on-chain pass/fail
before settlement), Chainalysis-style on-chain sanctions oracles (`isSanctioned(address)`),
OWASP fail-closed enforcement for agent payments, and the repo's existing
`onchain/src/ComplianceGuard.sol` — which this project **extends and hardens** (see §1 note on
its fail-open default).

## 1. What/How

A gatekeeper decision engine that protocols call before accepting value. One call, one verdict,
one immutable evidence record.

**Step-by-step flow:**
1. A protocol (vault, lending pool, RWA sleeve) calls `Gatekeeper.check(account)` before
   `deposit`/`mint`/`borrow`. The integration hook is a modifier-style call: on FAIL the
   protocol transaction reverts with a queryable rejection code.
2. The decision engine fans out to three plugin types and combines results:
   - **KYC plugin:** verifies an EIP-712 signed identity attestation (issuer allowlisted,
     90-day validity, nonce replay protection). Returns PASS/FAIL/STALE.
   - **AML plugin:** the off-chain screening agent checks the address against OFAC SDN, EU
     consolidated, and UN sanctions lists plus taint propagation — bounded **3-hop lookback**
     over fund flows (mixer/darknet/scam interaction = automatic FAIL). The agent commits
     `keccak256(attestation)` on-chain; the plugin verifies the commitment, never the PII.
   - **Geo plugin:** registry of ISO-3166 region codes with allow/block status. An address
     resolving to a blocked jurisdiction FAILs. Updates are timelocked 48h.
3. **Combination rule (fail-closed):** verdict = FAIL if *any* plugin returns FAIL, STALE, or
   is unreachable/erroring. There is no "unknown → pass" path. A third verdict, REFER,
   routes to manual review and **blocks** until a reviewer resolves it on-chain.
4. Every decision is appended to an **immutable audit trail**: event log with
   {account, verdict, evidenceHash, pluginVersions, timestamp}. No update/delete path exists.
   Off-chain, the agent additionally writes Ed25519-signed, JCS-canonicalized, hash-chained
   receipts (OWASP pattern) so an external auditor can verify the chain without repo access.
5. **Emergency override:** a guardian role can force-PASS a single address with an on-chain
   reason; the override **auto-expires after 24h** and cannot be renewed without a new reason.
   During an override of list X, a FAIL from list Y still blocks (overrides are per-list).
6. **Caching:** PASS decisions are cached for **24h** (screening is expensive); any list
   update or new sanction designation invalidates the cache immediately.

**Important integration note — hardening the existing guard.** The repo's
`onchain/src/ComplianceGuard.sol` is currently *fail-open when no oracle is set*
("fail-open only when no oracle is set … blocklist-only until oracleEnabled"). P20 keeps its
`ISanctionsOracle` interface but changes the default: unset-oracle ⇒ FAIL (deny), with an
explicit, time-boxed `LEGACY_ALLOWLIST` escape hatch for pre-existing integrations, each
entry carrying an expiry. The contract's own docs already name ZK-attestation screening as
the upgrade path — P20 implements that path.

**Numeric parameters (normative):**
- fee_bps = **0**. This protocol charges nothing; its value is enabling the other 25.
- Sanctions lists: OFAC SDN + EU consolidated + UN; list-version freshness: alert if >**24h**
  stale, FAIL-closed if >**48h**.
- Attestation validity: KYC **90 days**; screening commitment **24h** cache; score staleness
  ⇒ FAIL.
- Taint lookback: **3 hops**; mixer interaction ⇒ automatic FAIL (no appeal except via REFER).
- Emergency override TTL: **24h**, single-address, per-list, reason on-chain.
- Geo registry: ISO-3166 codes; updates timelocked **48h**.
- Retention: sanctions-screen logs **5 years** (evidence store, off-chain); decision events
  permanent on-chain.

## 2. Why

- **Who pays:** nobody directly — and that is the point. P20 is a cost center that protects
  every revenue center. The "customers" are P08 (RWA vaults), P19 (credit), and any
  institutional-facing venue that cannot legally accept unscreened deposits.
- **Why it matters:** one sanctioned flow through a SINCOR protocol is a regulatory event
  that can freeze the whole ecosystem's banking and exchange access. Compliance is the gate
  every institutional dollar passes through — without it, the RWA and credit revenue paths
  in the catalog cannot exist.
- **Revenue path to Treasury:** indirect but load-bearing — P20 unlocks the institutional
  TVL that pays P08's 15 bps and P19's 20 bps. Its own fee_bps is 0 by design; P26 ranks it
  by *losses prevented*, not fees.
- **What breaks without this:** sanctioned/tainted funds enter SINCOR vaults; the DAO has no
  auditable evidence of screening; institutional integrations (RWA, credit) are undeployable.

## 3. Build Stack

- **Solidity 0.8.24**, OpenZeppelin v5 (`AccessControl`, `TimelockController` 48h for list/geo
  updates, `ReentrancyGuard` on the hook path). Extends `onchain/src/ComplianceGuard.sol`
  (reuses `ISanctionsOracle`/`IComplianceGuard` interfaces; hardens the default to fail-closed).
- **Python screening agent:** `src/sincor2/defi/p20_compliance/` — sanctions-list ingestion
  (OFAC SDN digital-currency addresses, EU, UN), 3-hop taint walk over Base/Eth RPC,
  EIP-712/Ed25519 receipt signing, TOA hook, STATUS_FEED_SCHEMA reporting.
- **Integration points (verified):** `verticals/compliance/` (`regulatory_agent.py`,
  `schemas.py`, `agent_card.json`) — P20 is the on-chain enforcement arm of that vertical;
  `onchain/src/` for the hardened gatekeeper; `src/sincor2/defi/` for the agent package.
- **Reused vs new:** reused = ComplianceGuard interfaces + guardian pattern, OZ libs, the
  compliance vertical's schemas. New = decision engine, plugin architecture, geo registry,
  immutable audit trail, integration hook, receipt chain.

## 4. Tip — operational gotchas, failure modes, monitoring

- **Fail-closed is a DoS surface.** If the screening provider is down, *everything* halts —
  which is correct, but an attacker can manufacture the outage. Mitigation: redundant
  providers (primary + fallback), circuit-breaker alerting when failure rate exceeds **5%**
  of checks in 10 minutes, and a drilled manual-review (REFER) procedure.
- **List staleness kills you quietly.** OFAC updates land frequently; a 3-day-old list is a
  false sense of security. Monitor list-version age as a first-class metric; page at >24h.
- **Never put PII on-chain.** Only hashes and verdicts go on-chain; attestations live in the
  evidence store with 5-year retention. A leaked passport hash is still a leak — use
  salted commitments.
- **Geo is address-level, not IP-level.** VPNs make IP geo-gating theater; the registry keys
  on attested jurisdiction from the KYC plugin, not on the RPC endpoint's IP.
- **Override abuse.** Every emergency override is on-chain with a reason and a 24h fuse;
  monitor override count — more than 3 in 7 days triggers a governance review.
- **Monitor:** decision latency p50/p99 (target p99 < **2s** off-chain + 1 block on-chain),
  pass/fail/refer rates, blocklist hit counts, provider staleness seconds, override count,
  cache-hit rate.

## 5. Acceptance criteria

1. **Fail-closed fuzz:** across 10,000 randomized runs, *any* unreachable, erroring, or
   stale provider yields FAIL — zero passes on incomplete signal, zero violations.
2. **Deposit gating:** an integration test proves a KYC-failing address cannot deposit into
   a hooked vault while a passing address can; the rejection code is queryable on-chain.
3. **Audit trail immutability:** the decision log has no update/delete path; a full history
   replays from events; off-chain receipt hash-chain verifies end-to-end.
4. **Real-world screening:** on a mainnet fork, 20+ real addresses screened — every
   sanctioned-source tainted flow FAILs (zero false passes), clean flows PASS.
5. **Override discipline:** emergency overrides auto-expire at 24h in tests; a KYC FAIL
   still blocks during an unrelated list's override.
6. **Performance:** gatekeeper `check` under **150k gas** on the reference fork; screening
   decision p99 under 2s off-chain.

## 6. Risk gates

- `live_blocked = true`: the gatekeeper runs in dry-run (log-only) mode until audit sign-off;
  protocols integrate against the dry-run hook first.
- Catalog gates enforced: `kyc_aml` (plugin quorum + fail-closed), `geo_block` (registry
  consulted on every check).
- List/geo/attestor-allowlist changes: timelock 48h + compliance-officer co-sign +
  founder-signer execution. No unilateral list edits — a compromised list admin is a
  protocol-wide allow/block weapon.
- Mainnet activation of *enforcing* mode requires the audit sign-off, a drilled outage
  runbook, and redundant providers live.

## 7. Phase definition of done

- **Spec:** `docs/architecture/P20_COMPLIANCE_ARCHITECTURE.md` reviewed; the decision model
  is deterministic — a reviewer can derive the exact verdict for any documented input;
  fail-closed is stated as a blocking requirement, not an aspiration.
- **Scaffold:** tree exists (`onchain/src/p20-compliance/`, `src/sincor2/defi/p20_compliance/`,
  `tests/pytest/test_p20_compliance.py`, `agents/p20-compliance-automation.yaml`); interfaces
  compile under solc 0.8.24; mock plugin passes a smoke test.
- **Core:** all seven core tasks complete; every signal combination maps to the spec's
  expected verdict; provider-outage cases fail closed in tests.
- **Testing:** unit + integration + 10k fuzz + 20-address fork sim green against the §5 bars.
- **Audit:** package passes the repo audit-readiness checklist; threat model covers
  list-admin compromise, evidence tampering, and screened-data privacy; data-privacy review
  filed; all findings remediated with regression tests; gas report shows check < 150k.
- **Docs:** a protocol team can integrate the deposit hook from docs alone; runbook covers
  provider outage, emergency override, and appeals; monitoring hooks emit per
  STATUS_FEED_SCHEMA.
- **Deploy:** Base Sepolia deployment verified; a hooked test vault gates deposits on
  Sepolia; mainnet-readiness checklist signed with evidence links before enforcing mode on
  mainnet.

---

## P21 — DAO Treasury Management (Deep Spec)

Catalog: `P21_TREASURY_DAO`, category treasury · fee_bps **8** · risk_score **0.20** · target_apr **0.04** ·
min_capital_usd **1** · max_alloc_pct **0.30** · live_blocked **false** · gates (`hold_file`,
`execute_live_env`). Treasury: `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`.

Design lineage: Aera-style autonomous vaults (daily rebalancing to a target, non-custodial,
Guardian + arbitrageur model, Spearbit-audited), Karpatkey-style transparent reporting, and the
industry-standard Investment Policy Statement (IPS) discipline — allocation bands, per-venue
exposure caps, rebalancing triggers, runway reporting.

**Critical invariant: the swarm computes allocation reports and NEVER broadcasts transactions.**
`live_blocked=false` does not weaken this — it only means the reporting pipeline may run
against live data. Execution, if ever enabled, goes through `execute_live_env` to an approved
external executor; the swarm itself holds no signing capability.

## 1. What/How

A read-only allocation swarm for the canonical SINCOR treasury. It watches, computes, reports —
and stops there.

**Step-by-step flow (one tick, default cadence 24h):**
1. **Inventory.** The allocator reads treasury balances (on-chain reads only) across the
   venue set: Morpho Gauntlet USDC (Base), Aave v3, liquid staking, stablecoin reserves,
   and the native SINC/AXM position.
2. **Target computation.** Within the risk budget (risk_score 0.20), the allocator computes
   target weights across four bands:

   | Band | Target | Hard band |
   |---|---|---|
   | Stablecoin reserves (USDC/USDT) | 50% | 40–60% |
   | Blue-chip (ETH/BTC, incl. LST) | 27% | 20–35% |
   | Yield venues (Morpho, Aave) | 15% | 10–25% |
   | Native SINC/AXM | 8% | 5–15% |

   Per-venue cap: **max_alloc_pct = 0.30** — no single venue ever exceeds 30% of the tick's
   capital. Venue allowlist only (audited venues; stable-only for the stable band).
3. **Rebalance trigger.** A reallocation is proposed only if: any band deviates > **500 bps
   (5%)** from target, or **24h** has elapsed since the last report, or a venue's risk
   signal trips (utilization > 90%, depeg > 50 bps, audit flag). Otherwise the tick emits a
   "no action" report — boring is the default.
4. **Hold file (gate).** Every decision — including "no action" — is written to a
   content-addressed hold file (`JSON`, filename = `sha256` of canonical contents) awaiting
   human review. No downstream step proceeds without a *reviewed* hold file. Unreviewed
   holds block publishing; holds older than **72h** unreviewed raise a backlog alert.
5. **No-broadcast guard.** The process holds no private keys and no signing libraries are
   importable in the allocator path; any code path attempting a broadcast raises
   `BroadcastForbidden` and logs a security event. Tested at every agent entrypoint.
6. **Dashboard publisher.** Reviewed reports are published to the SINCOR dashboard feed on
   the status-feed schema: per-venue allocations, risk metrics, fee projection at 8 bps,
   IPS-limit checks, runway projection. Reports `unknown` on failure, never `healthy`.
7. **execute_live_env (gate).** Live execution of an approved allocation happens ONLY when
   the `EXECUTE_LIVE_ENV` env flag is set AND a reviewed hold file exists, and even then
   execution is *delegated* to an approved external executor path — the swarm still never
   broadcasts. Every dispatch is audit-logged. Default in all deploys: **off**.

**Fees:** 8 bps on *realized* treasury yield, computed per tick and routed to the canonical
treasury. Worked example: $1M deployed stables at 4% APR → $40,000/year yield → $32/year
protocol fee at 8 bps. Small per dollar — the value is the allocation alpha, not the fee.

## 2. Why

- **Who pays:** the DAO itself — 8 bps comes out of realized yield, so the fee is only paid
  when the treasury actually earns.
- **Why it exists:** an idle treasury earns 0% and a manually-managed one drifts — the
  documented failure mode of DAO treasuries is not blowups but *silent erosion*: creeping
  concentration, yield-chasing, discretionary spends. A boring, banded, reported process
  beats vibes.
- **Revenue path to Treasury:** twofold — (a) 8 bps on realized yield flows straight back
  to the treasury; (b) the allocation alpha itself (moving idle stables from 0% to ~4%)
  is treasury revenue. At the catalog's target_apr of 0.04 on deployed capital, the swarm
  pays for itself many times over.
- **What breaks without this:** treasury allocation becomes ad-hoc multisig discretion with
  no IPS, no bands, no reporting — exactly the setup that produces the 72.5%-in-native-token
  concentration the industry data shows in protocol DAOs.

## 3. Build Stack

- **Python 3.11**, `src/sincor2/defi/p21/`: `allocator.py`, `gates.py`, `report.py`,
  `publisher.py`, `fees.py`. Pure off-chain — no new Solidity required (fee routing reuses
  the treasury fee pattern; an optional executor adapter is the only on-chain surface).
- **Data:** on-chain reads via web3.py (Base RPC); venue APYs from Morpho/Aave subgraphs or
  official APIs; TOA `ingest_feedback` for revenue forecasting per tick.
- **Hold files:** JSON canonicalization (JCS), sha256 content addressing, stored under a
  dedicated `hold/` directory with a 72h-review SLA.
- **Integration points (verified):** `src/sincor2/defi/` — the package lives next to
  `catalog.py` and `yield_aggregator.py` (P21 consumes the aggregator's venue data);
  `agents/` for `p21-treasury-dao.yaml`; SINCOR dashboard feed for the publisher.
- **Reused vs new:** new = allocator logic, hold-file gate, report schema, no-broadcast
  guard, dashboard publisher. Reused = DeFi-OS tick engine (`engine.py`), yield aggregator
  venue adapters, agent YAML conventions, status-feed schema.

## 4. Tip — operational gotchas, failure modes, monitoring

- **The env flag is the whole security model.** `EXECUTE_LIVE_ENV` defaults to off in every
  deploy script, Dockerfile, and Railway config. Any PR that flips a default to on fails
  review automatically — encode this as a CI check (`grep` the flag default).
- **Hold-file backlog is the early warning.** Unreviewed holds piling up means either the
  reviewer is asleep or the allocator is thrashing. Alert at >3 unreviewed or any hold
  older than 72h.
- **Report freshness.** A dashboard showing yesterday's allocation as "current" is worse
  than no dashboard. Freshness alert if no reviewed report in 30h.
- **No-broadcast is tested, not trusted.** Fuzz the allocator with adversarial inputs;
  assert zero broadcast attempts. Any new dependency that can sign (eth-account, etc.)
  must never be importable from the allocator module — enforce with an import-lint test.
- **Venue risk signals need teeth.** Utilization >90% or depeg >50 bps must *force* a
  reallocation proposal out of the venue, not merely note it.
- **Monitor:** allocation drift per band (bps), hold-file backlog count/age, report
  freshness, no-broadcast-attempt alarm (must stay at zero — any firing is a SEV-1),
  realized yield vs target_apr 0.04, fee routed per tick.

## 5. Acceptance criteria

1. **Allocation math:** every report's allocations sum to exactly 100%; no venue exceeds
   max_alloc_pct **0.30**; all bands respected — verified over 1,000+ randomized allocator
   inputs.
2. **No-broadcast invariant:** no fuzzed input across 1,000 cases ever produces a broadcast
   attempt; direct broadcast attempts through every agent entrypoint raise
   `BroadcastForbidden` and log a security event.
3. **Hold-file gate:** unreviewed holds block publishing in tests; reviewed holds unlock it;
   holds are content-addressed (tampering changes the address and fails verification).
4. **Live-env discipline:** without `EXECUTE_LIVE_ENV`, the pipeline is report-only even
   against live data; with the flag, only reviewed holds dispatch, and every dispatch is
   audit-logged. Fork simulation emits **zero** on-chain transactions.
5. **Fee math:** 8 bps on realized yield matches the worked example in P21_ECONOMICS.md to
   the wei.
6. **Coverage:** `src/sincor2/defi/p21/` line coverage above **85%**.

## 6. Risk gates

- `live_blocked = false` — but this authorizes *live-data reporting*, not live execution.
  The `hold_file` + `execute_live_env` gates are the actual control plane.
- Execution path (if ever enabled): auditor sign-off on the executor adapter + founder-signer
  approval of the env-flag policy + timelocked executor allowlist. The swarm's no-broadcast
  invariant is not relaxable by any flag.
- Dry-run default: every environment ships with `EXECUTE_LIVE_ENV` off; enabling it is a
  deliberate, logged, reversible operator action with a printed compliance checkpoint
  pre-flight.
- Treasury address `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` is a constant verified
  against the catalog at import time; a mismatch fails closed.

## 7. Phase definition of done

- **Spec:** `docs/spec/P21_ARCHITECTURE.md` + `P21_ECONOMICS.md` reviewed; the no-broadcast
  invariant is a blocking requirement; the rebalance-trigger table is complete.
- **Scaffold:** `src/sincor2/defi/p21/` package imports cleanly; stub report validates
  against the report schema; `configs/agents/p21-treasury-dao.yaml` parses.
- **Core:** allocator, hold-file gate, report generator, no-broadcast guard, dashboard
  publisher, fee routing, and live-env controller all complete; invalid inputs raise
  instead of silently defaulting.
- **Testing:** unit (85%+ coverage) + integration + 1,000-case invariant fuzz + fork
  simulation green against the §5 bars, with zero transactions emitted on the fork.
- **Audit:** audit-prep package is a single reviewable directory with the commit hash
  recorded; threat model covers accidental broadcast, hold-file tampering, fee diversion;
  zero open medium-or-higher findings; gas-optimized hot paths with a before/after report.
- **Docs:** component reference complete with every public function documented; runbook
  covers hold-file review, dashboard checks, and live-env enablement; monitoring hooks
  emit on the feed schema with alerts firing on simulated staleness.
- **Deploy:** testnet scripts run end-to-end on Base Sepolia with `EXECUTE_LIVE_ENV` off
  and agent check-ins visible; mainnet-readiness checklist reviewed and signed with
  go/no-go recorded.

---

## P22 — Stablecoin Yield Maximizer (USDC/USDT on Base)

**Catalog binding:** `ProtocolSpec(22, "P22_STABLE_YIELD", ..., risk_score=0.22, target_apr=0.058, min_capital_usd=10.0, max_alloc_pct=0.50, fee_bps=10, live_blocked=False, gates=("stable_only","morpho_preferred"))`
**Treasury:** `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` (from `src/sincor2/defi/catalog.py::TREASURY`)
**Chain:** Base mainnet (chain id 8453). Assets: USDC and USDT only (both 6 decimals on Base).
**Auction file:** `~/workspace/sincor2-auction-tasks/p22-stablecoin-yield.json`

---

## 1. What/How

P22 is an offchain allocation swarm that deploys user USDC/USDT into the highest net-yield venue on Base, capped per venue, with a 10 bps protocol cut on realized yield routed to the canonical treasury. It is the conservative leg of the yield stack (risk_score 0.22, the lowest of the 26 except P20/P21) — no leverage, no LP positions, no volatile collateral exposure, only lending-market supply positions.

### Step-by-step flow

1. **Scan (every 300 s).** `scanner.py` polls each allowlisted venue adapter for a `PoolQuote{venue, asset, net_apr, depth_usd, ts}`. Net APR = venue-reported supply APR minus any curator performance fee (e.g. Steakhouse USDC 25%, Moonwell Flagship 15%, Gauntlet 0%) minus an estimated P22 share of the 10 bps protocol fee. Quotes older than 600 s are discarded as stale; a venue with no fresh quote is skipped, never guessed.
2. **Gate.** `gates.py::stable_only` rejects any quote whose `asset` ∉ {USDC, USDT} or `chain_id != 8453` **before** it enters the optimizer. Rejection logs a security event with asset address + venue. DAI, ETH, cbBTC, and unknown tokens are rejected at scan, adapter, and allocation entrypoints.
3. **Optimize.** `optimizer.py` solves: maximize Σ w_i × net_apr_i subject to Σ w_i = 1.0, 0 ≤ w_i ≤ 0.50 (max_alloc_pct), w_i = 0 for stale/capped venues. Ties (within 5 bps) break to Morpho venues on Base (`morpho_preferred`). Depth check: w_i × total_capital ≤ 0.10 × depth_usd — never more than 10% of a venue's available liquidity.
4. **Rebalance check.** Compare weighted net APR of current allocation vs proposed allocation. Rotate only if ΔAPR ≥ 50 bps **and** estimated gas cost of the move < 25% of the expected annualized yield gain **and** ≥ 4 h since the last rotation (churn cooldown). Otherwise emit `NOOP`.
5. **Execute.** Adapters build supply/withdraw calldata (ERC-4626 `deposit`/`redeem` for Morpho vaults; Aave V3 Pool `supply`/`withdraw`). Every external call is wrapped in try/catch that degrades to `venue_skipped`, never an uncaught exception. Multi-venue moves are executed venue-by-venue in descending Δ weight order; a failed leg leaves prior legs in place and records partial state.
6. **Fee accounting.** `fees.py` accrues 10 bps on **realized** yield only (delta of vault share value in USDC terms, measured per 300 s tick). Fees accumulate in a per-user ledger and settle to the canonical treasury in batches ≥ $10 to avoid dust transfers.

### Key contracts/modules

| Module | Location | Role |
|---|---|---|
| `scanner.py` | `src/sincor2/defi/p22/` | 300 s poll loop, quote normalization, TOA forecast overlay hooks |
| `adapters/morpho.py` | `src/sincor2/defi/p22/adapters/` | Morpho Blue/Vault discovery, supply/withdraw tx building, APR reads |
| `adapters/aave_v3.py` | `src/sincor2/defi/p22/adapters/` | Secondary venue (Aave V3 Base USDC/USDT), same `StableVenueAdapter` interface |
| `optimizer.py` | `src/sincor2/defi/p22/` | Constrained allocation solver, tie-breaks, depth caps |
| `fees.py` | `src/sincor2/defi/p22/` | 10 bps realized-yield fee ledger, treasury settlement |
| `gates.py` | `src/sincor2/defi/p22/` | `stable_only` gate, venue allowlist enforcement |
| Agent config | `configs/agents/p22-stablecoin-yield.yaml` | budgets, memory, TOA hooks, 5-min check-in cadence |

### Venue allowlist (Base, chain id 8453)

| Venue | Adapter | Asset | Reference APY band (net, 2026) | Note |
|---|---|---|---|---|
| Morpho Gauntlet USDC Prime | `adapters/morpho.py` | USDC | ~3.7–4.4% | 0% perf fee; preferred on ties |
| Morpho Steakhouse USDC | `adapters/morpho.py` | USDC | ~2.8–3.5% | 25% perf fee priced into net APR |
| Morpho Moonwell Flagship USDC | `adapters/morpho.py` | USDC | ~4.3–4.5% | 15% perf fee; +WELL rewards tracked separately, not in net APR |
| Aave V3 Base USDC | `adapters/aave_v3.py` | USDC | ~3–6% (utilization-driven) | Secondary fallback |
| Aave V3 Base USDT | `adapters/aave_v3.py` | USDT | ~3–6% | Secondary fallback |

Venue additions require a spec amendment + audit re-signoff; the adapter set is frozen at deploy.

### Actors

- **Yield agent** (Python, `configs/agents/p22-stablecoin-yield.yaml`): runs scan/optimize/rebalance loop, TOA check-ins every 5 min.
- **TOA orchestrator**: feeds revenue forecasts via `ingest_feedback`; can flip venue preference within one rebalance cycle (subject to cooldown).
- **Treasury forwarder**: pulls batched 10 bps fees to `0x09E289…9612Ac`; per standing treasury policy converts USDC→(USDC/WETH) — no-op here since fees are already USDC.

### Numeric parameters

- fee_bps = 10 (0.10% of realized yield, not AUM)
- target_apr = 0.058 (5.8% gross)
- risk_score bound = 0.22 (venue mix must keep blended venue risk ≤ 0.22; Morpho curated vaults and Aave V3 both qualify)
- max_alloc_pct = 0.50 per venue
- min_capital_usd = 10.0 (positions below $10 are not opened — gas would exceed a year of yield)
- depth_cap = 0.10 (≤10% of venue available liquidity per allocation)
- scan_cadence_s = 300; quote_staleness_s = 600
- rebalance_delta_bps = 50; rebalance_cooldown_h = 4; gas_vs_gain_ratio = 0.25
- tie_break_bps = 5 (prefer Morpho when |ΔAPR| ≤ 5 bps)

---

## 2. Why

**Who pays:** users with idle USDC/USDT on Base who want automated best-rate lending without managing positions themselves. **Why they pay:** rate dispersion across Base lending venues is 150–400 bps at any time (Morpho vaults 2.8–4.5% vs Aave V3 spikes to 6%+ at high utilization; Coinbase/Morpho retail products hit 7.4% with incentives). A user parked in one venue leaves money on the table; manual rotation costs gas and attention.

**Revenue path to Treasury:** 10 bps on realized yield. Worked example: $100,000 deployed at 5.8% gross → $5,800/yr yield → $58/yr to treasury, $5,742 to user (net APR 5.7942%). At $10M deployed across the swarm, treasury inflow ≈ $5,800/yr before compounding. Fees are realized-yield-based, so treasury only earns when users earn — aligned, and it compounds with the deposit base.

**What breaks without this:** capital sits in a single venue; when that venue's utilization collapses (e.g. a large borrower repays on Aave), the user's APR drops to ~1% and no one notices. P22 is also the **on-ramp primitive** for P25 (agent-managed portfolios use P22 as the cash-like sleeve) — without it, P25 has no stable leg.

---

## 3. Build Stack

- **Python 3.11**, stdlib-first. `eth-utils`/`web3` only in adapter layer for calldata construction; no new chain client (reuse repo's existing web3 patterns).
- **ERC-4626 standard** for Morpho vault interaction (`deposit`, `redeem`, `convertToAssets`); **Aave V3 `IPool`** (`supply`, `withdraw`, `getReserveData`) for the secondary venue. Both behind the single `StableVenueAdapter` protocol in `interfaces.py` — new venues are ~120 lines each.
- **TOA hooks**: `ingest_feedback` (forecast overlay) and 5-min check-in emission, same pattern as `src/sincor2/defi/yield_aggregator.py` — P22 reuses its fee-ledger and check-in serialization, builds new the scanner/optimizer.
- **Repo integration points (verified to exist):**
  - `src/sincor2/defi/` — new package `p22/` alongside existing `catalog.py`, `yield_aggregator.py`
  - `marketplace/settlement.py` — AXM settlement patterns for any future fee-denominated flows
  - `verticals/trading/` — reference for agent/venue conventions (P22 is offchain-only, no `onchain/src/hooks` contract needed)
  - `configs/agents/` — new `p22-stablecoin-yield.yaml`
  - `docs/ops/` — new `P22_RUNBOOK.md`
- **Tests:** `tests/pytest/test_p22_*.py`, ≥85% coverage on `src/sincor2/defi/p22/`, Base mainnet fork runs for adapter tx-building.

---

## 4. Tip — gotchas, failure modes, monitoring

- **Utilization spikes are the enemy.** Aave V3 USDC supply APR can read 12% at 95% utilization and collapse to 2% the next block when a whale repays. The optimizer must use a **4-hour TWAP of quoted APR**, not the spot quote, or it will churn into spikes and pay gas for nothing. Scanner stores a rolling TWAP per venue; optimizer consumes TWAP only.
- **Morpho vault allocations shift.** Curators (Gauntlet/Steakhouse/Moonwell) reallocate underlying Morpho Blue markets; the adapter must re-read `totalAssets`/`convertToAssets` per scan, never cache share prices across ticks.
- **Withdraw liquidity.** ERC-4626 `redeem` can revert under high utilization. Rotation plan must stage withdrawals with a 2-epoch liquidity check: if `maxRedeem(vault, us) < needed`, split the rotation across ticks rather than forcing it.
- **Fee-on-realized-yield needs a clean baseline.** Set `principal_baseline` at each deposit/rebalance; yield = `convertToAssets(shares) − baseline`. Never accrue fees on unrealized TWAP marks.
- **Monitoring signals (SINCOR dashboard):** `net_apr_vs_target` (alert if blended net APR < 4.5% for > 24 h), `venue_stale` (no fresh quote in 600 s), `stable_only_violation` (P0 alarm — any non-USDC/USDT asset touches the allocator), `rotation_gas_ratio` (alert if a rotation's gas exceeded 25% of its projected gain — indicates TWAP failure).

---

## 5. Acceptance criteria

Every P22 task is judged against:

1. **stable_only is absolute.** No code path in `src/sincor2/defi/p22/` can hold, quote, or allocate a non-USDC/USDT asset. Fuzzed asset addresses (1000+) are 100% rejected at all three entrypoints.
2. **Optimizer constraints hold exactly.** Σw = 1.0 (within 1e-9), w_i ≤ 0.50, w_i × capital ≤ 0.10 × venue depth. Violations are impossible by construction, proven by 1000+ fuzzed quote sets.
3. **Fee math is wei-exact.** `fee = floor(realized_yield_wei × 10 / 10000)`; matches the worked example in `docs/spec/P22_ECONOMICS.md` to the wei; fees settle only to `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`.
4. **Rebalance discipline.** No rotation fires with ΔAPR < 50 bps, gas > 25% of projected gain, or < 4 h since last rotation. TOA-forced rotations respect the same gates.
5. **Graceful degradation.** Any venue failure (revert, stale quote, RPC error) → venue skipped, position unchanged, alert emitted. Zero uncaught exceptions in fork runs.
6. **Coverage and fork proof.** ≥85% Python coverage; a Base mainnet-fork run produces a valid allocation plan from live Morpho/Aave quotes with zero unexpected reverts.

---

## 6. Risk gates

- **live_blocked = False** — P22 may emit live intents, but only under `dry_run_default`: every deploy ships with dry-run ON; live execution requires an explicit operator flag flip + the printed compliance checkpoint pre-flight (standing 2026-09-26 directive).
- **Risk budget:** blended venue risk ≤ 0.22. Adding a venue with venue-risk > 0.22 is a spec-level change requiring audit re-signoff.
- **Auditor + founder-signer path required for:** (a) any change to the venue allowlist, (b) any change to fee_bps, max_alloc_pct, or the treasury address, (c) the dry-run→live flip. Auditor signoff = external review recorded in the audit-prep package; founder-signer = the deploy ceremony runbook path (see PR #263 precedent).
- **What stays dry-run:** live capital rotation on Base mainnet stays dry-run until audit remediation is fully closed AND the mainnet readiness checklist is signed.

---

## 7. Phase definition of done

- **spec:** `docs/spec/P22_ARCHITECTURE.md` + `P22_ECONOMICS.md` + `interfaces.py` reviewed and frozen: venue allowlist, adapter interface, optimizer constraints (50 bps/4 h/25% gas rule), fee math with worked example, and the stable_only gate semantics are all written as implementable numbers, not prose.
- **scaffold:** `src/sincor2/defi/p22/` package imports cleanly; scanner/optimizer/fee stubs with TOA hooks compile; `configs/agents/p22-stablecoin-yield.yaml` validates; mock adapter returns schema-valid `PoolQuote`s.
- **core:** gates reject DAI/ETH/unknown at every entrypoint; Morpho + Aave V3 adapters quote and build txs against a Base fork; optimizer produces valid plans (Σ=1, caps respected, Morpho tie-break); 10 bps fees accrue wei-exact; TOA rotation flips venue within one cycle under cooldown.
- **testing:** unit (gate/fee/quote/optimizer, ≥85% coverage), integration (scan→optimize→allocate→fee with Morpho-outage fallback), 1000+ fuzz cases on invariants, and a Base mainnet-fork run with live quotes — all green.
- **audit:** frozen snapshot + threat model (venue spoofing, stale-quote exploitation, fee diversion) in a reviewable package; zero open medium+ findings; hot-path gas reduced ≥20% with before/after report.
- **docs:** component reference with every public function documented (builds warning-free), agent YAML + `P22_RUNBOOK.md` executable by an operator, dashboard hooks emitting on the feed schema.
- **deploy:** Base Sepolia end-to-end with agent check-ins in logs, dry-run default; mainnet readiness checklist signed (audit signoff, allowlist freeze, treasury address verified, rollback plan, compliance checkpoint printed) with go/no-go recorded.

---

## P23 — NFT-Fi Liquidity Pools (Fractionalized NFT Yield)

**Catalog binding:** `ProtocolSpec(23, "P23_NFTFI", ..., risk_score=0.65, target_apr=0.15, min_capital_usd=400.0, max_alloc_pct=0.08, fee_bps=20, live_blocked=True, gates=("collection_whitelist","utilization_cap"))`
**Treasury:** `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` (from `src/sincor2/defi/catalog.py::TREASURY`)
**Posture: LIVE-BLOCKED.** The module never emits live intents; any live-intent path raises `LiveBlocked`. Everything below runs on testnets, forks, and local chains only.
**Auction file:** `~/workspace/sincor2-auction-tasks/p23-nftfi-pools.json`

---

## 1. What/How

P23 is a fractional-NFT yield system: whitelisted NFT collections are deposited into per-collection vaults, vaults issue ERC-20 fractional shares, and share value accrues from lending-market activity — borrowers post whitelisted NFTs as collateral and pay interest, which flows to fractional holders. The design internalizes the BendDAO 2022 lesson set: cross-sourced floor oracles (never one source), conservative LTV, hard utilization caps, and a liquidation path that does not depend on finding an auction bidder mid-crash.

### Step-by-step flow

1. **Whitelist.** `CollectionRegistry` holds the allowlist of ERC-721 collection addresses. Adds/removals go through a **48-hour timelocked admin**; every change emits an event mirrored by the Python monitor. A collection removed from the whitelist freezes new deposits immediately; existing positions keep full redemption rights.
2. **Deposit & fractionalize.** User deposits an NFT from a whitelisted collection into `FractionalVault`. The vault locks the ERC-721 and mints ERC-20 fractional shares: `shares = floor_price_twap × (1 − deposit_fee_bps/10000) / share_price`, where `share_price` is the current NAV per share. First deposit of a vault mints a permanent `MINIMUM_SHARES = 1e3` to the burn address (ERC-4626 inflation-attack guard).
3. **Price.** `NftPricingOracle` reports per-collection floor as a **7-day TWAP** blended from ≥2 independent marketplace sources. Staleness breaker: no fresh feed update within 24 h → deposits freeze, withdrawals stay open, manager agent pages. Per-epoch price-move clamp: a single oracle update cannot move the floor more than 25% (blunts manipulation spikes like the Loopscale pattern).
4. **Yield generation.** The vault routes a bounded sleeve of its stablecoin-denominated liquidity into NFT-collateralized lending: borrowers borrow against whitelisted NFTs at **max LTV 60%**, liquidation threshold **75%** of floor TWAP, with a **24-hour borrower repayment grace window** before collateral auction. Interest paid by borrowers accrues to share NAV. The lending sleeve is capped by the utilization cap (below).
5. **Utilization cap.** `utilization = deployed_lending_value / total_pool_value ≤ 0.75`. Deposit calls that would breach 0.75 revert; withdrawals are **never** blocked by the cap. The cap is readable onchain (`utilizationCap()`, `currentUtilization()`).
6. **Redeem.** Share holders burn ERC-20 shares for pro-rata underlying value (USDC-denominated accounting; NFT-specific redemption via buyout auction is out of scope for v1 — redemptions are fungible-value, not specific-token).
7. **Fee.** 20 bps on realized yield (NAV appreciation per epoch, measured per collection pool), routed to the canonical treasury with per-pool accounting.

### Key contracts/modules

| Contract/Module | Location | Role |
|---|---|---|
| `CollectionRegistry.sol` | `onchain/src/p23/` | Whitelist, 48 h timelock admin, change events |
| `FractionalVault.sol` | `onchain/src/p23/` | ERC-721 custody, ERC-20 share mint/burn, NAV accounting, ReentrancyGuard |
| `NftPricingOracle.sol` | `onchain/src/p23/` | Floor TWAP, 24 h staleness breaker, ±25% per-update clamp, last-good-price fallback |
| `UtilizationCap.sol` (library/enforcer) | `onchain/src/p23/` | 0.75 cap enforcement on deposits, always-open withdrawals |
| `interfaces/` | `onchain/src/p23/interfaces/` | `IFractionalVault`, `ICollectionRegistry`, `INftPricingOracle` |
| `manager.py` | `src/sincor2/defi/p23/` | Python liquidity manager: utilization monitoring, cross-collection rebalance within caps, TOA feedback, dry-run only |
| Agent config | `configs/agents/p23-nftfi-pools.yaml` | budgets, memory, TOA hooks, check-in cadence |

### Actors

- **Fractional holder**: deposits NFTs or USDC, holds ERC-20 shares, earns borrower-interest accrual.
- **Borrower**: posts whitelisted NFT collateral, borrows at ≤60% LTV, pays interest; 24 h grace before liquidation auction.
- **Curator/admin (timelocked)**: manages the collection whitelist; cannot touch user funds, cannot change fee_bps or treasury.
- **Liquidity manager agent** (Python, dry-run only): rebalances the manager's *advisory* allocation across whitelisted collections within the 0.08 per-collection cap; emits TOA feedback. Never signs.
- **Liquidation backstop**: if a collateral auction finds no bidder, the vault's reserve sleeve (5% of pool value, untouchable by the lending cap) absorbs the NFT at the 75% threshold price rather than letting bad debt compound — the explicit BendDAO anti-death-spiral.

### Numeric parameters

- fee_bps = 20 (0.20% of realized yield per pool per epoch)
- target_apr = 0.15 (15%; assumes borrower interest 18–24% APR on deployed sleeve × 0.75 max utilization × 0.90 after reserve drag)
- risk_score bound = 0.65 (highest in the 26 except P10; concentration and oracle risk priced in)
- max_alloc_pct = 0.08 per collection (no single collection > 8% of managed capital)
- min_capital_usd = 400.0 (below $400, per-NFT gas and oracle-update amortization destroy the return)
- utilization_cap = 0.75 (lending sleeve / pool value)
- max_ltv = 0.60; liquidation_threshold = 0.75; borrower_grace_h = 24
- oracle_twap_days = 7; oracle_staleness_h = 24; oracle_max_move_pct = 0.25 per update; min_sources = 2
- whitelist_timelock_h = 48; reserve_sleeve_pct = 0.05
- deposit_fee_bps = 50 (0.5%, anti-spam on fractionalize/redeem churn); no withdrawal fee
- epoch_s = 604800 (7-day epochs for accrual snapshots)
- live_blocked = True — `LiveBlocked` raised on any live-intent path; verified by tests hitting every entrypoint

---

## 2. Why

**Who pays:** NFT holders who want yield on otherwise-idle blue-chip NFTs, and borrowers who want liquidity without selling. **Why they pay:** the BendDAO post-mortem is the pitch — 2022 proved demand (15,000 ETH lent at peak) *and* proved the failure modes (single floor oracle + shared pool + 90%→70% liquidation-threshold panic + no-bidder auctions = reflexive run). A fractional system with cross-sourced TWAP oracles, hard 75% utilization caps, and a reserve-sleeve backstop is the product BendDAO's depositors actually wanted.

**Revenue path to Treasury:** 20 bps on realized per-pool yield. Worked example: a $1M pool at 15% APR generates $150,000/yr gross yield → $300/yr to treasury at 20 bps, $149,700 to share holders (net 14.97%). Per-pool accounting means the treasury ledger can attribute inflow per collection. Ten pools at $1M = $3,000/yr treasury inflow at target.

**What breaks without this:** NFT capital stays dead (0% on a JPEG) or flows into the same under-collateralized single-oracle designs that blew up in 2022. P23 is also the catalog's designated high-risk sleeve (risk_score 0.65) — without it the swarm has no instrument for NFT-native capital.

---

## 3. Build Stack

- **Solidity 0.8.24** (matches repo's pinned solc — see `~/.solcx/solc-v0.8.24` precedent), OpenZeppelin `ReentrancyGuard`, `SafeERC20`, ERC-4626 math patterns for share accounting (adapted: NAV is oracle-derived, not asset-counted).
- **Non-bricking oracle pattern:** every oracle read wrapped in try/catch; on failure the vault falls back to last-good-price and freezes deposits — never reverts the whole transaction (repo standing lesson from ExecutionEscrowManager's push/pull payout fix).
- **Python 3.11** for `manager.py` (dry-run advisory only), sharing the TOA check-in serialization from `src/sincor2/defi/yield_aggregator.py`.
- **Repo integration points (verified to exist):**
  - `onchain/src/p23/` — new Solidity tree (vault, registry, oracle, interfaces); sits alongside existing `onchain/src/hooks/` (`AutoCapitalizeMonetizeHook.sol`, `MoebiusMEVHook.sol`, `PhantomCreditToken.sol`) following the same non-brick conventions
  - `src/sincor2/defi/` — new package `p23/` next to `catalog.py`
  - `marketplace/` — settlement patterns if share redemptions ever need AXM-denominated paths (currently out of scope)
  - `configs/agents/p23-nftfi-pools.yaml`, `docs/ops/P23_RUNBOOK.md`
  - **Not used:** `sinax/` does not exist in this repo — no SINAX integration is cited; proofs/attestations are out of scope for v1
- **Tests:** forge (Solidity) + pytest (Python); ≥85% Python coverage; fork tests against real NFT collection data for oracle behavior; 1000+ fuzzed deposit/redeem/price sequences on invariants.

---

## 4. Tip — gotchas, failure modes, monitoring

- **The oracle is the entire attack surface.** Floor prices are gameable (wash trades, thin floors). The 7-day TWAP + 2-source minimum + 25% per-update clamp is the defense; the failure mode is a *slow* manipulation over weeks. Monitor `floor_divergence` (source A vs source B spread > 15% → alert; > 30% → auto-freeze deposits).
- **BendDAO's real killer was duration mismatch**, not just the oracle: lender funds were instantly withdrawable while loans were locked in illiquid NFTs. The 0.75 utilization cap + 5% reserve sleeve + always-open withdrawals is the structural answer — but under a true collection collapse (floor −80%), the reserve sleeve will be consumed. Size it honestly in the risk docs: the sleeve covers idiosyncratic defaults, not systemic collection death.
- **Share-math rounding** at small pool sizes can dilute early depositors. The 1e3 minimum-shares burn + $400 min capital floor jointly bound this; fuzz it explicitly.
- **Whitelist governance capture** is the centralization vector: whoever controls the 48 h timelock decides which collections get yield. Timelock events must be dashboard-visible with the full 48 h delay honored — no emergency bypass.
- **Monitoring signals:** `utilization_vs_cap` (alert at 0.70), `oracle_staleness` (P0 at 24 h), `whitelist_change` (every timelock queue/execute), `reserve_sleeve_drawdown` (any absorption event = P1 incident review), `live_intent_attempt` (any `LiveBlocked` raise = P0 — someone tried to go live).

---

## 5. Acceptance criteria

Every P23 task is judged against:

1. **Whitelist is airtight.** Deposits referencing non-whitelisted collections revert in 100% of fuzzed cases; timelock removals freeze new deposits within one block while redemptions keep working.
2. **Utilization cap holds.** `currentUtilization() ≤ 0.75` across 1000+ fuzzed deposit/borrow/repay/price sequences; cap-breach deposits revert; withdrawals never revert due to the cap.
3. **Oracle degrades safely.** Stale (>24 h) or single-source feeds freeze deposits and fall back to last-good-price without bricking the vault; manipulated feeds (>25% jump) are clamped and alert.
4. **Share math is wei-exact.** Deposit→accrue→redeem cycles match the spec's NAV formula to the wei; `totalSupply × share_price` reconciles to pool NAV within oracle tolerance.
5. **Fee math is wei-exact.** 20 bps on realized per-epoch yield per pool; settles only to `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`.
6. **Live-blocked is provable.** Every entrypoint attempted as a live intent raises `LiveBlocked`; zero live intents emitted in any test, fork run, or fuzz campaign. Dry-run paths unaffected.

---

## 6. Risk gates

- **live_blocked = True (hard).** The module raises `LiveBlocked` on any live-intent code path. Going live is not a checklist item — it requires a new catalog entry, a full re-audit, and founder signoff. The mainnet readiness checklist for P23 records **go/no-go = NO-GO by design** until that re-audit exists.
- **Risk budget:** risk_score 0.65 is the ceiling for the sleeve; per-collection cap 0.08 enforced onchain where possible, offchain in the manager.
- **Auditor + founder-signer path required for:** (a) any whitelist addition (each collection is a new risk surface), (b) any change to utilization_cap, max_ltv, liquidation_threshold, oracle params, or fee_bps, (c) any change to the timelock admin. No exceptions — these are money-path parameters.
- **What stays dry-run:** everything. The Python manager is advisory-only and never signs; the Solidity suite deploys to testnets/forks only.

---

## 7. Phase definition of done

- **spec:** `docs/spec/P23_ARCHITECTURE.md` + `P23_ECONOMICS.md` + Solidity/Python interfaces frozen: fractionalization model, whitelist governance (48 h timelock), utilization-cap formula (0.75), oracle spec (7-day TWAP, 2 sources, 24 h breaker, 25% clamp), fee math with worked per-pool example, and the live-blocked posture stated as a hard invariant.
- **scaffold:** `onchain/src/p23/` compiles under solc 0.8.24; `src/sincor2/defi/p23/` imports cleanly; vault/registry/oracle stubs + mock pricing oracle round-trip a mock deposit/redeem; agent YAML validates.
- **core:** whitelist registry enforces add/remove with timelock; utilization enforcer reverts cap-breach deposits and never blocks withdrawals; fractional vault completes deposit-accrue-redeem with wei-exact share math; oracle adapter survives manipulated/stale feeds; 20 bps fees route per-pool; liquidity manager rebalances within caps; every live-intent path raises `LiveBlocked`.
- **testing:** unit (registry/caps/share-math/fees/live-block, forge + pytest ≥85%), integration (whitelist→deposit→accrue→redeem with oracle moves), 1000+ fuzz sequences on invariants, mainnet-fork run with real collection data and zero live intents — all green.
- **audit:** frozen snapshot + threat model (oracle manipulation, whitelist capture, share-math rounding) reviewable in one directory; zero open medium+ findings; vault/registry hot paths gas-reduced ≥20% with before/after report.
- **docs:** component reference (Solidity + Python) warning-free with every public function documented; agent YAML + `P23_RUNBOOK.md` (whitelist changes, cap monitoring, breaker resets) executable; dashboard hooks on the feed schema.
- **deploy:** Base Sepolia end-to-end with live emission disabled by default and agent check-ins in logs; mainnet readiness checklist produced and **signed as NO-GO** (audit signoff, whitelist freeze policy, oracle verification, treasury address verified, rollback plan, compliance checkpoint printed) — go-live requires re-audit.

---

## P24 — SocialFi Revenue Share (Creator Tokens + DeFi Primitives)

**Catalog binding:** `ProtocolSpec(24, "P24_SOCIALFI", ..., risk_score=0.33, target_apr=0.06, min_capital_usd=50.0, max_alloc_pct=0.10, fee_bps=12, live_blocked=True, gates=("creator_split","no_price_talk"))`
**Treasury:** `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` (from `src/sincor2/defi/catalog.py::TREASURY`)
**Posture: LIVE-BLOCKED.** The module never emits live intents; any live-intent path raises `LiveBlocked`. Testnets, forks, local chains only. Content policy (`no_price_talk`) is intact and enforced onchain-adjacent.
**Auction file:** `~/workspace/sincor2-auction-tasks/p24-socialfi-revenue.json`

---

## 1. What/How

P24 turns creators into markets with a durable revenue split: each verified creator gets a bonding-curve token, trades pay a fixed fee that splits three ways (creator / platform / SINCOR treasury), accrued revenue is claimable per epoch, and creator tokens plug into two DeFi primitives (staking for yield, collateral in allowlisted lending venues). The design takes the friend.tech bonding-curve mechanic (`price = supply² / 16000`), replaces its 5%/5% extractive split with a conservation-exact three-way split, and adds the content-policy guard the catalog mandates.

### Step-by-step flow

1. **Onboarding.** `onboarding.py` registers a creator: identity check, metadata submission, content-policy screen. Metadata containing price talk (token price predictions, "moon"/"100x"-class promises, guaranteed-return language) is rejected before any onchain action; rejections log the violated rule, not the full text.
2. **Issuance.** `CreatorTokenFactory` deploys the creator's token: fixed supply **1,000,000,000** (1e9) tokens, **50% to the open market bonding curve**, **50% to the creator vesting linearly over 5 years** (1,825 days; unvested tokens cannot be sold or transferred). Supply cap is immutable at issuance; the factory is the only minter (verified by `onlyFactory` on the token).
3. **Bonding-curve trading.** Early price discovery runs on-curve: `buy_price(supply) = supply² / 16000` in wei-equivalent quote asset (USDC on Base), `sell_price = (supply − 1)² / 16000`. Curve inventory caps at **$69,000 market cap**; at graduation the curve closes and inventory migrates to a standard AMM pool (migration is a one-way, audited step — no re-entry to the curve).
4. **Fee split (conservation-exact).** Every trade pays **1000 bps (10%)** total fee, split per trade as integers that always sum to the fee:
   - creator: **4940 bps of the fee** (49.4% → 4.94% of trade value), pull-claimable
   - platform/operator: **4940 bps of the fee** (49.4%)
   - SINCOR treasury: **120 bps of the fee** = **12 bps of trade value** (the catalog's fee_bps=12), routed to `0x09E289…9612Ac`
   - Each leg computed as `floor(fee × share / 10000)`; residual dust goes to the treasury leg (most conservative destination). Invariant: `creator + platform + treasury == fee` exactly, for all inputs — fuzz-proven.
   - The `creator_split` gate: creator share of the fee can never be set below **4000 bps (40%)**; any parameter update violating this reverts.
5. **Revenue accrual.** `RevenueAccrual` tracks per-token revenue in **7-day epochs** with snapshotting: a holder's claimable amount is fixed by their balance at epoch snapshot, so late joiners cannot claim past epochs. Claims are **pull-based** (user calls `claim`); individual payout failures never block other claimants (non-bricking try/catch per payout, the repo's standing pattern).
6. **DeFi primitives.** Two adapters, both capped:
   - **Staking:** stake creator tokens in `CreatorStaking` for a share of a staking-rewards pool funded by 10% of platform-leg revenue; target 6% APR on staked value; unbonding period **72 h** (prevents flash stake/dump around epoch snapshots).
   - **Collateral:** creator tokens usable as collateral in allowlisted lending venues at **max LTV 50%**, per-token exposure cap **max_alloc_pct = 0.10** of the venue sleeve. Venue allowlist frozen at deploy.
7. **Content-policy guard.** `ContentPolicyGuard` screens token name/symbol/description/creator bio at issuance and on metadata updates against the `no_price_talk` rule set (regex + keyword deny-list, versioned). Violations block issuance or flag the token for review; the guard's rule-set version is emitted in events for auditability.

### Key contracts/modules

| Contract/Module | Location | Role |
|---|---|---|
| `CreatorTokenFactory.sol` | `onchain/src/p24/` | Permissioned issuance, 1e9 fixed supply, 50/50 market/creator-vesting split, 5-yr linear vesting |
| `BondingCurve.sol` | `onchain/src/p24/` | `supply²/16000` pricing, $69k graduation cap, one-way AMM migration |
| `FeeSplitDistributor.sol` | `onchain/src/p24/` | 4940/4940/120 split, pull claims, dust-to-treasury, non-bricking payouts |
| `RevenueAccrual.sol` | `onchain/src/p24/` | 7-day epochs, snapshot accounting, per-creator ledgers |
| `CreatorStaking.sol` | `onchain/src/p24/` | Staking primitive, 72 h unbonding, rewards from 10% of platform leg |
| `CollateralAdapter.sol` | `onchain/src/p24/` | Lending-venue adapter, 50% max LTV, 0.10 per-token cap |
| `ContentPolicyGuard.sol` | `onchain/src/p24/` | `no_price_talk` screening at issuance + metadata updates |
| `interfaces/` | `onchain/src/p24/interfaces/` | `ICreatorTokenFactory`, `IFeeSplitDistributor`, `IRevenueAccrual` |
| `onboarding.py` | `src/sincor2/defi/p24/` | Creator registration, metadata validation, TOA feedback |
| Agent config | `configs/agents/p24-socialfi-revenue.yaml` | budgets, memory, TOA hooks, check-in cadence |

### Actors

- **Creator**: registers, gets token + 5-yr vesting position, earns 49.4% of every trade fee in their token, pull-claims.
- **Trader/holder**: buys/sells on curve then AMM; pays 10% trade fee; can stake for the 6% target APR.
- **Platform operator**: receives 49.4% of trade fees; funds the staking rewards pool from 10% of its leg.
- **SINCOR treasury**: receives exactly **120 bps of each trade fee** (12 bps of trade value) plus the dust remainder.
- **Onboarding agent** (Python): guides registration, enforces the content-policy guard offchain before onchain issuance, emits TOA feedback. Never signs; live-blocked.

### Numeric parameters

- fee_bps = 12 (treasury cut = 12 bps of trade value = 120 bps of the 10% trade fee)
- trade_fee_bps = 1000 (10% per buy/sell); creator 4940 / platform 4940 / treasury 120 (bps of fee); creator floor 4000 bps of fee
- bonding curve: `price = supply² / 16000` (quote asset wei); graduation at $69,000 market cap
- token supply = 1e9 fixed; 50% curve / 50% creator vesting over 1,825 days
- target_apr = 0.06 (6% on the staking primitive)
- risk_score bound = 0.33; max_alloc_pct = 0.10 per creator token in the collateral sleeve
- min_capital_usd = 50.0 (below $50, the 10% trade fee + gas makes participation irrational — stated in onboarding UX)
- epoch_s = 604800 (7-day revenue epochs); staking unbonding_h = 72; max LTV = 0.50
- staking rewards source = 10% of platform fee leg
- live_blocked = True — `LiveBlocked` on any live-intent path

---

## 2. Why

**Who pays:** traders speculating on creator attention, and creators monetizing it. **Why they pay:** the 2023–2026 SocialFi experiments proved both halves — friend.tech showed bonding curves + fee splits generate real creator income ($12M in creator fees in its first month; 5% to app + 5% to creator per trade), and Zora's 2026 creator-coin model showed every profile becoming a market with 1% creator fees on every trade. P24 takes the proven mechanic and fixes the two things that killed the predecessors: (a) the split is conservation-exact and creator-floored (≥40%) instead of platform-extractive, and (b) revenue accrues in auditable epochs with pull claims instead of opaque points programs.

**Revenue path to Treasury:** 12 bps of every trade value, routed per trade to the canonical treasury with per-creator accounting. Worked example: a creator token doing $500,000/yr in trade volume → $50,000 in trade fees → creator $24,700, platform $24,700, **treasury $600/yr** (12 bps × $500k). At 100 active creator tokens averaging that volume: $60,000/yr treasury inflow. The staking primitive adds a second path: unclaimed platform-leg dust and the 10%-of-platform-leg staking pool overflow sweep to treasury quarterly.

**What breaks without this:** creators monetize through Web2 ad-revenue shares (YouTube keeps ~42%) or not at all; SINCOR captures zero of the attention economy its agents participate in. P24 is also the catalog's only `social`-category protocol — without it the swarm has no creator-economy instrument.

---

## 3. Build Stack

- **Solidity 0.8.24**, OpenZeppelin `ReentrancyGuard`, `SafeERC20`; bonding-curve math in fixed-point with overflow-checked `supply²` (supply ≤ 5e8 on-curve, so `supply²` ≤ 2.5e17 — fits uint256 with 3 orders of magnitude headroom; still checked).
- **Pull-payment pattern** throughout the distributor (no push loops over holder sets — the repo's standing push/pull lesson).
- **Python 3.11** for `onboarding.py`; content-policy screening reuses a versioned rule-set module shared with `verticals/compliance/` conventions.
- **Repo integration points (verified to exist):**
  - `onchain/src/p24/` — new Solidity tree next to existing `onchain/src/hooks/`
  - `src/sincor2/defi/` — new package `p24/` next to `catalog.py`
  - `verticals/compliance/` — content-policy rule-set conventions (P24's `no_price_talk` list lives here as the versioned source)
  - `marketplace/` — creator discovery/listing patterns if creator tokens are ever surfaced in agent cards (read-only for v1)
  - `configs/agents/p24-socialfi-revenue.yaml`, `docs/ops/P24_RUNBOOK.md`
  - **Not used:** `sinax/` does not exist in this repo — no attestation integration cited
- **Tests:** forge + pytest; ≥85% Python coverage; fuzz on split conservation (exact equality for all inputs), epoch snapshot correctness, and policy-guard true/false positive rates.

---

## 4. Tip — gotchas, failure modes, monitoring

- **Bonding curves are reflexive on the way down.** `supply²/16000` means the last sellers get almost nothing; combined with a 10% sell fee, late holders are trapped. The $69k graduation cap bounds the damage, and the vesting schedule (5-yr linear, no transfer of unvested) prevents creator dumps — but document the reflexivity plainly in user-facing copy. No price talk in our own materials either (`no_price_talk` applies to SINCOR's promotion of creator tokens).
- **Split conservation must be exact, not approximate.** A 1-wei leak per trade × millions of trades = real money and an auditor finding. The `floor()` + dust-to-treasury rule makes conservation structural, not remembered — fuzz it with adversarial fee values (1 wei through 1e24).
- **Epoch snapshot gaming.** Without the 72 h staking unbonding, a holder could stake 1 block before snapshot and unstake after. The unbonding period + snapshot-at-epoch-start (not end) closes this; test both.
- **Content-policy guard false positives** will block legitimate creators ("price" appears in "priceless work"). The deny-list is phrase-based, not substring-based, and every rejection cites the rule version + matched phrase; keep a human-appeal path in the runbook.
- **Monitoring signals:** `split_drift` (any trade where legs ≠ fee → P0, should be impossible), `creator_payout_lag` (unclaimed > 30 days → nudge), `policy_violation_rate` (spike → rule-set review), `curve_graduation` (every $69k graduation → treasury reconciliation), `live_intent_attempt` (any `LiveBlocked` raise → P0).

---

## 5. Acceptance criteria

Every P24 task is judged against:

1. **Split conservation is exact.** `creator + platform + treasury == fee` for every trade across fuzzed inputs from 1 wei to 1e24; dust always lands in the treasury leg; creator leg never below 4000 bps of the fee.
2. **Bonding-curve math matches spec.** `buy_price = supply²/16000`, `sell_price = (supply−1)²/16000`; graduation fires at exactly $69,000 market cap; post-graduation curve is permanently closed.
3. **Accrual snapshots are ungameable.** Late joiners cannot claim past epochs; 72 h unbonding + epoch-start snapshots proven against stake/unstake timing attacks in tests.
4. **Content policy holds.** The `no_price_talk` deny-list blocks price-talk metadata at issuance in 100% of adversarial test cases; legitimate creator metadata passes; every rejection cites rule version + phrase.
5. **Fee math is wei-exact.** Treasury leg = `floor(fee × 120 / 10000)` + dust; settles only to `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`; matches the worked creator-payout example in `docs/spec/P24_ECONOMICS.md` to the wei.
6. **Live-blocked is provable.** Every entrypoint attempted as a live intent raises `LiveBlocked`; zero live intents in any test, fork, or fuzz run.

---

## 6. Risk gates

- **live_blocked = True (hard).** `LiveBlocked` on any live-intent path. Mainnet readiness checklist records **NO-GO by design** until a full re-audit + founder signoff creates a new catalog posture.
- **Risk budget:** risk_score 0.33; the collateral-sleeve per-token cap (0.10) and 50% max LTV are money-path parameters — changes need auditor + founder-signer.
- **Auditor + founder-signer path required for:** (a) any change to split bps, creator floor, trade fee, or treasury address, (b) bonding-curve parameters or graduation cap, (c) content-policy rule-set major versions, (d) venue allowlist for the collateral adapter.
- **Compliance gate:** `no_price_talk` is a catalog gate, not a nice-to-have. Any task that weakens it fails acceptance regardless of other criteria. SINCOR's own promotion of P24 must also avoid price talk (standing token-positioning line: SINC is platform access, not an investment — creator tokens are likewise never marketed on price).

---

## 7. Phase definition of done

- **spec:** `docs/spec/P24_ARCHITECTURE.md` + `P24_ECONOMICS.md` + Solidity/Python interfaces frozen: token lifecycle (1e9 supply, 50/50, 5-yr vesting), bonding-curve formula + $69k graduation, exact 4940/4940/120 split with conservation proof obligation, epoch/snapshot accrual, staking (6% target, 72 h unbond) + collateral (50% LTV, 0.10 cap) primitives, and the `no_price_talk` rule-set as versioned requirements.
- **scaffold:** `onchain/src/p24/` compiles under solc 0.8.24; `src/sincor2/defi/p24/` imports cleanly; factory/distributor stubs + mock revenue source round-trip a mock issue→split→claim; onboarding stub screens metadata; agent YAML validates.
- **core:** factory issues only through itself with caps + attribution events; distributor splits to exact bps with pull claims and non-bricking payouts; accrual snapshots survive epoch boundaries; content-policy guard blocks price-talk metadata; staking + collateral adapters work against mocked venues within caps; onboarding agent completes a valid registration end-to-end; 12 bps treasury routing is wei-exact; every live-intent path raises `LiveBlocked`.
- **testing:** unit (factory/split/accrual/guard/fees, forge + pytest ≥85%), integration (onboard→issue→accrue→split→claim plus staking flows), 1000+ fuzz sequences on split conservation + snapshot correctness, mainnet-fork run with exact accounting — all green.
- **audit:** frozen snapshot + threat model (split manipulation, accrual gaming, factory permission bypass, curve reflexivity) reviewable in one directory; zero open medium+ findings; factory/distributor hot paths gas-reduced ≥20% with before/after report.
- **docs:** component reference warning-free with every public function documented; agent YAML + `P24_RUNBOOK.md` (onboarding, split monitoring, policy escalations + human appeal path) executable; dashboard hooks on the feed schema.
- **deploy:** Base Sepolia end-to-end, dry-run default, agent check-ins in logs; mainnet readiness checklist produced and **signed as NO-GO** (audit signoff, split-parameter freeze, treasury address verified, rollback plan, compliance checkpoint printed).

---

## P25 — Agent-Managed Portfolio (Swarm-Driven User Portfolios, Fees in AXM/SINC)

**Catalog binding:** `ProtocolSpec(25, "P25_PORTFOLIO", ..., risk_score=0.28, target_apr=0.07, min_capital_usd=25.0, max_alloc_pct=0.40, fee_bps=10, live_blocked=False, gates=("risk_budget","cash_floor"))`
**Treasury:** `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` (from `src/sincor2/defi/catalog.py::TREASURY`)
**Posture:** live_blocked = False, but P25 never moves user funds onchain by itself — it computes target allocations and drift-correction trades; execution stays behind `dry_run_default` and the operator's execution path.
**Auction file:** `~/workspace/sincor2-auction-tasks/p25-agent-portfolio.json`

---

## 1. What/How

P25 is the portfolio layer that turns the other 25 swarms' outputs into per-user portfolios. A weight ingestor consumes TOA-ranked protocol signals, a risk-budget engine converts the 0.28 risk budget into per-position limits, an allocator produces target weights, a rebalancer corrects drift, a cash-floor guard keeps 5% in USDC, and a fee module accrues 10 bps per annum denominated in AXM/SINC to the canonical treasury. The architecture follows the Yearn V3 Debt Allocator pattern (offchain optimizer → onchain applicator → keeper execution), with Beefy-style harvest fee accounting.

### Step-by-step flow

1. **Ingest (every 3600 s).** `ingestor.py` pulls swarm outputs: per-protocol TOA scores for P01–P25 (P26 excluded — it is the meta ranker, not an allocation target). Each protocol contributes `signal = toa_score × (1 − risk_score)`; weights normalize to Σ = 1.0. Feeds carry a timestamp; any feed older than **3600 s** is stale → the ingestor emits `HOLD` (no rebalance, reads keep serving last-good weights).
2. **Risk budget.** `risk.py` converts risk_score 0.28 into hard limits: for each candidate position i with protocol risk r_i and weight w_i, require `w_i × r_i ≤ 0.28 × w_i` trivially — the binding constraints are: (a) single-position cap w_i ≤ 0.40, (b) blended portfolio risk `Σ w_i × r_i ≤ 0.28`, (c) any protocol with r_i > 0.60 is ineligible regardless of weight (excludes P06 0.62, P10 0.70, P23 0.65 — the high-risk sleeves cannot enter conservative user portfolios). Violations return itemized rejections, never silent clamping.
3. **Allocate.** `allocator.py` combines normalized weights with the risk budget: scale down the highest-risk positions first until `Σ w_i × r_i ≤ 0.28`, then renormalize; enforce w_i ≤ 0.40; reserve the cash floor. Output is a `TargetPortfolio{weights, cash_pct, as_of, feed_hash}`.
4. **Cash floor.** `guards.py` enforces **cash_pct ≥ 0.05** in USDC on every allocation and every rebalance plan. Any plan breaching the floor is rejected before trade computation.
5. **Rebalance (daily cadence, 86400 s).** `rebalancer.py` computes drift = |current_w − target_w| per position. If any position's drift ≥ **0.025 absolute (2.5%)**, compute delta trades; batch them; drop any trade < **$25** (min_capital_usd) as dust; re-check cash floor and risk budget on the post-trade portfolio. If all drifts < 2.5%, emit `NOOP`.
6. **Fees.** `fees.py` accrues **10 bps per annum on AUM**, computed per tick: `fee_tick = aum_usd × 0.0010 × (tick_s / 31557600)`. Denominated in **AXM** (default) or **SINC** (user's choice at onboarding); conversion uses the same price source as the fee-conversion executor path. Per-user ledger; settlement to treasury in batches ≥ $10 equivalent.
7. **Serve.** `api.py` exposes read-only portfolio state: current allocation, target weights, drift per position, fee history, rebalance status. Responses are sealed-safe: a user can only ever read their own `user_id`-scoped portfolio (tested with cross-user read attempts).

### Key contracts/modules

| Module | Location | Role |
|---|---|---|
| `ingestor.py` | `src/sincor2/defi/p25/` | Swarm-output ingestion, TOA signal → weights, 3600 s staleness → HOLD |
| `risk.py` | `src/sincor2/defi/p25/` | Risk-budget engine: 0.28 blended cap, 0.40 position cap, r_i > 0.60 exclusion |
| `allocator.py` | `src/sincor2/defi/p25/` | Target portfolio computation, renormalization, feed-hash provenance |
| `rebalancer.py` | `src/sincor2/defi/p25/` | Drift detection (2.5%), delta-trade batching, $25 dust filter |
| `fees.py` | `src/sincor2/defi/p25/` | 10 bps p.a. AUM fee in AXM/SINC, per-user ledger, treasury settlement |
| `guards.py` | `src/sincor2/defi/p25/` | 5% USDC cash-floor enforcement on every plan |
| `api.py` | `src/sincor2/defi/p25/` | Sealed-safe read API (allocation, drift, fees, rebalance status) |
| Agent config | `configs/agents/p25-agent-portfolio.yaml` | budgets, memory, TOA hooks, check-in cadence |

P25 is **offchain-only** (no new Solidity). If onchain fee settlement is later needed, it goes through the existing fee-conversion executor pattern (Permit2/Universal Router path from PR #259), not a new contract.

### Actors

- **User**: onboards, picks fee denomination (AXM/SINC), reads portfolio via API. Never touches other users' data.
- **Swarm signals** (P01–P25, via TOA): the raw material — protocol-level scores, APRs, risk assessments.
- **TOA orchestrator**: supplies `ingest_feedback`; its revenue forecasts can shift weights within one ingest cycle.
- **Rebalance executor (operator-controlled)**: consumes P25's delta-trade plans; P25 itself never signs. Live execution requires the operator's explicit path + compliance checkpoint.
- **Treasury**: receives batched AXM/SINC fee settlements.

### Numeric parameters

- fee_bps = 10 → **per annum on AUM** (0.10%/yr), not per-trade; tick accrual `aum × 0.0010 × tick_s/31557600`
- target_apr = 0.07 (7%; assumes blended sleeve yields net of the 10 bps fee)
- risk_score bound = 0.28 blended (`Σ w_i × r_i ≤ 0.28`); ineligible protocols: r_i > 0.60
- max_alloc_pct = 0.40 per position
- min_capital_usd = 25.0 (positions and delta trades below $25 are not opened — dust filter)
- cash_floor_pct = 0.05 (USDC, enforced on every plan)
- ingest_cadence_s = 3600; feed_staleness_s = 3600 (stale → HOLD, reads unaffected)
- rebalance_cadence_s = 86400; drift_trigger = 0.025 absolute
- fee denomination: AXM (default) | SINC (user choice); settlement batch floor $10 equivalent
- live_blocked = False, but execution stays behind `dry_run_default` + operator path

---

## 2. Why

**Who pays:** users who want a managed DeFi portfolio without picking protocols themselves — the "set and forget" segment Beefy proved exists (hundreds of millions in TVL on exactly this promise: deposit once, keepers rebalance, fees compound). **Why they pay:** the 26-swarm system produces 25 protocol signals no human can track; P25 is the only component that converts swarm intelligence into *their* allocation. Without it, swarm outputs are dashboard trivia.

**Revenue path to Treasury:** 10 bps p.a. on AUM, denominated in AXM/SINC — which means every fee payment is also AXM/SINC buy pressure routed to treasury. Worked example: $1M AUM → $1,000/yr in fees; at AXM = $0.50, that's 2,000 AXM/yr to `0x09E289…9612Ac`. At $50M AUM: $50,000/yr (100,000 AXM at $0.50). The fee is on AUM rather than yield so treasury earns even in flat markets — the tradeoff is disclosed at onboarding, and the rate (0.10%) sits an order of magnitude below Yearn's historical 2%/20% and below Beefy's 9.5% performance fee, which is the competitive argument.

**What breaks without this:** P01–P25 are 25 disconnected strategies with no user-facing portfolio product; the SINCOR A2A economy has no managed-money primitive, and the AXM/SINC fee flywheel (users pay fees in AXM/SINC → treasury accumulates → conversion policy) has no demand driver. P25 uses P22 as its cash-like sleeve — the two are designed as a pair.

---

## 3. Build Stack

- **Python 3.11**, stdlib-first; `pydantic`-style validation via the repo's existing schema helpers if present, else dataclasses with explicit validators (no new heavy deps).
- **Yearn V3 DOA pattern** (offchain optimizer → applicator → keeper) adapted: P25's optimizer is `allocator.py`, the "applicator" is the delta-trade plan consumed by the operator's execution path, and the "keeper" role is the daily rebalance cron. **Beefy harvest pattern** for fee accounting: fees accrue on a per-tick basis against a recorded baseline, never on marks.
- **TOA integration**: `ingest_feedback` for weight shifts; 5-min check-ins reuse `src/sincor2/defi/yield_aggregator.py` serialization.
- **Repo integration points (verified to exist):**
  - `src/sincor2/defi/` — new package `p25/` next to `catalog.py` (reads `PROTOCOL_BY_ID` as the signal universe)
  - `src/sincor2/defi/p22/` — P25 consumes P22's allocation output as its stable sleeve (cross-project dependency, versioned interface)
  - `marketplace/settlement.py` — AXM settlement conventions for fee denomination
  - `verticals/trading/` — execution-path conventions for the operator's rebalance consumer
  - `configs/agents/p25-agent-portfolio.yaml`, `docs/ops/P25_RUNBOOK.md`
  - **Not used:** `sinax/` does not exist in this repo — no attestation integration cited
- **Tests:** `tests/pytest/test_p25_*.py`; ≥85% coverage; fork runs feed real protocol data through the ingest→allocate→rebalance pipeline.

---

## 4. Tip — gotchas, failure modes, monitoring

- **Stale feeds are the #1 failure mode.** A TOA feed that stops updating while markets move produces a portfolio optimized for last week. The 3600 s staleness → HOLD rule is load-bearing: rebalance freezes, reads keep serving, alert fires. Never "use last weights and keep trading" — that is how stale-signal losses happen.
- **Risk-budget renormalization can concentrate.** Scaling down high-risk positions and renormalizing pushes weight into the remaining low-risk ones, which can breach the 0.40 cap or over-concentrate in P22's stable sleeve. The allocator must iterate: scale → cap → renormalize → re-check blended risk, max 10 iterations, then reject with itemized violations if unconverged.
- **The r_i > 0.60 exclusion list must be computed from the catalog, not hardcoded.** If a protocol's risk_score changes in `catalog.py`, P25's eligibility must follow automatically — read `PROTOCOL_BY_ID` at ingest time.
- **Fee denomination choice has tax/accounting implications for users** (paying in AXM vs SINC). Disclose at onboarding; keep the choice reversible quarterly, not per-tick (prevents gaming the denomination around price moves).
- **Monitoring signals:** `drift_vs_target` (alert if any position > 5% drift for > 48 h without rebalance — indicates executor failure), `stale_weight_feed` (P1 at 3600 s), `fee_accrual` (treasury-reconcilable per-user ledger), `cash_floor_breach_attempt` (any rejected plan → audit log), `unconverged_allocation` (allocator iteration exhaustion → P1).

---

## 5. Acceptance criteria

Every P25 task is judged against:

1. **Weights are always valid.** Σw = 1.0 (within 1e-9), w_i ≤ 0.40, `Σ w_i × r_i ≤ 0.28`, cash ≥ 5% USDC — across 1000+ fuzzed signal sets; violations are rejected with itemized causes, never silently clamped.
2. **High-risk exclusion is automatic.** Any protocol with catalog risk_score > 0.60 is ineligible; eligibility is derived from `PROTOCOL_BY_ID` at ingest time, not a hardcoded list (proven by mutating a fixture catalog).
3. **Staleness freezes action, not reads.** Feeds older than 3600 s → `HOLD`: no rebalance plan emitted, API keeps serving last-good weights, alert fires. Tested with frozen-clock fixtures.
4. **Rebalance discipline.** No plan emits unless some position drifts ≥ 2.5%; delta trades < $25 are dropped; post-trade portfolio re-passes cash floor + risk budget.
5. **Fee math is wei-exact and correctly denominated.** `fee_tick = aum × 10/10000 × tick_s/31557600`, denominated in the user's chosen AXM/SINC at the recorded price; matches the worked AXM example in `docs/spec/P25_ECONOMICS.md` to the wei; settles only to `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`.
6. **API is sealed-safe.** Cross-user read attempts return 403/empty in 100% of tests; responses validate against the published schema.

---

## 6. Risk gates

- **live_blocked = False**, but P25 is **advisory + accounting only**: it computes plans and accrues fees; it never holds user keys and never broadcasts. Live *execution* of rebalance plans goes through the operator's execution path under `dry_run_default`, with the printed compliance checkpoint pre-flight (standing 2026-09-26 directive).
- **Risk budget:** blended 0.28 is the portfolio-level invariant. Any change to the budget, the 0.40 cap, the 0.60 exclusion threshold, or the 5% cash floor is a spec-level change requiring audit re-signoff.
- **Auditor + founder-signer path required for:** (a) changes to fee_bps, fee denomination options, or treasury address, (b) changes to risk-budget/cash-floor parameters, (c) enabling any automated (non-operator) execution of rebalance plans — this would change P25's advisory posture and needs a full re-audit.
- **What stays dry-run:** any onchain movement of user funds stays dry-run until audit remediation is closed AND the mainnet readiness checklist is signed. The API and fee ledger can go live earlier (read + accounting paths).

---

## 7. Phase definition of done

- **spec:** `docs/spec/P25_ARCHITECTURE.md` + `P25_ECONOMICS.md` + `interfaces.py` frozen: weight pipeline (TOA signal → normalized weights), risk-budget semantics (0.28 blended, 0.40 cap, 0.60 exclusion from catalog), cash floor (5% USDC), rebalance rules (daily, 2.5% drift, $25 dust filter), and the AXM/SINC fee flow with worked example — all as implementable numbers.
- **scaffold:** `src/sincor2/defi/p25/` (ingestor, risk, allocator, rebalancer, fees, guards, api) imports cleanly; ingestor/TOA stubs and a mock swarm feed produce a valid draft portfolio; agent YAML validates.
- **core:** risk engine rejects over-budget weight sets with itemized violations; ingestor normalizes fresh feeds and HOLDs on stale ones; allocator honors caps + floor and raises on invalid input; cash-floor guard blocks breaching plans; fee module accrues 10 bps p.a. in AXM/SINC wei-exact; rebalancer converges a drifted mock portfolio in one pass; API is sealed-safe and schema-valid.
- **testing:** unit (risk/ingestor/allocator/floor/fees, ≥85% coverage), integration (weights→allocation→rebalance→fees→API with stale-feed hold), 1000+ fuzz cases on invariants, mainnet-fork run with real protocol data through the pipeline — all green.
- **audit:** frozen snapshot + threat model (weight manipulation, stale-feed exploitation, fee diversion, cross-user reads) reviewable in one directory; zero open medium+ findings; any onchain settlement paths gas-reduced ≥20% with before/after report.
- **docs:** component reference warning-free with every public function documented; agent YAML + `P25_RUNBOOK.md` (weight-feed ops, rebalance review, fee reconciliation) executable; dashboard hooks on the feed schema.
- **deploy:** Base Sepolia end-to-end with dry-run default and agent check-ins in logs; mainnet readiness checklist signed (audit signoff, weight-feed source verification, treasury address verified, rollback plan, compliance checkpoint printed) with go/no-go recorded — advisory/accounting paths may go live before automated execution.
