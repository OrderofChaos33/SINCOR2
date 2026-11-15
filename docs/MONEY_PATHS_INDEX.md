# Money Paths Index — the 52 SINCOR revenue paths

Human-readable view of `config/money_paths_registry.json` (machine-readable source of truth).
Verify any time with: `python3 scripts/verify_money_paths.py` (full) or `--only <id>` (one path).

- **Generated:** 2026-11-14 · **Branch:** `feat/sincor-a-plus-master-overhaul` · **Base:** `4fd2c248` (corrected post-integration — the first generation used the pre-rebase side-branch tree at `45f34309`; see the notes on paths 1, 10, and 49)
- **Source of truth:** ~/workspace/goals/make-money/files/52-PATH-STATUS-TABLE.md (verified 2026-11-14) + 52-PATH-REVENUE-MANIFEST.md (names/ids only; manifest claims are scope, not proof)
- **Outside revenue to date:** $0 on every path.

> Verified table rollup: LIVE 3 / SEND-READY 3 / WIRED-NEEDS-TRIGGER 5 / NOT FOUND 5, remainder IN-QUEUE. Per-entry counts here: LIVE 2 (paths 1, 17), SEND-READY 2 (paths 10, 52), WIRED-NEEDS-TRIGGER 5, NOT FOUND 5, IN-QUEUE 38. The third LIVE (bounty sweeps) and third SEND-READY (audit packets) in the table's rollup are Pillar-4 motions spanning paths 21-46 rather than single numbered paths; they are documented in each P01-P26 entry's notes instead of inflating one entry.

| # | Path | Category | Status | Payment rail | Entrypoint |
|---|---|---|---|---|---|
| 1 | CNP Task Auction Take-Rate | A2A JSON-RPC skill | LIVE | AXM (Base) | `src/sincor2/a2a_integration.py` |
| 2 | AXM Native Settlement Spread | on-chain settlement | NOT FOUND | none — mechanism not built | — (none; see notes) |
| 3 | Vickrey Auction Surplus Routing | DeFi/liquidity route | IN-QUEUE | AXM (Base) | `marketplace/contract_net/vickrey.py` |
| 4 | Dual-Pool Re-Auction Fees | DeFi/liquidity route | IN-QUEUE | AXM (Base) | `contracts/ExecutionEscrowManager.sol` |
| 5 | Hybrid Slashing & Bond Forfeiture Cut | on-chain settlement | IN-QUEUE | AXM (Base) | `contracts/escrow/SincorTaskEscrow.sol` |
| 6 | TimeLock Early-Exit Penalties | on-chain settlement | NOT FOUND | none — mechanism not built | — (none; see notes) |
| 7 | sincor-x402 Micro-Tariffs | x402 API gate | WIRED-NEEDS-TRIGGER | x402 (Base, USDC) | `src/sincor2/x402_payments.py` |
| 8 | ERC-7579 Session Key Validation Fees | on-chain module | NOT FOUND | none — no toll implemented | — (none; see notes) |
| 9 | SEAL Underwriting Premiums | on-chain module | IN-QUEUE | AXM (Base) | `src/sincor2/underwriting/engine.py` |
| 10 | SIGIL Attestation Tolls | on-chain module | SEND-READY | none — toll not implemented | — (none; see notes) |
| 11 | TOA Priority Fees | A2A JSON-RPC skill | NOT FOUND | none — no toll implemented | — (none; see notes) |
| 12 | KYA Identity & Reputation Staking | on-chain module | WIRED-NEEDS-TRIGGER | AXM (Base) | `src/sincor2/kya/registry.py` |
| 13 | Adjudicator Dispute Fees | A2A JSON-RPC skill | IN-QUEUE | AXM (Base) | `src/sincor2/a2a_inbound_market.py` |
| 14 | SINAX Licensing | licensing | IN-QUEUE | none — no billing surface | `src/sincor2/sinax/axiom_solver.py` |
| 15 | BribeLoop Hook MEV | DeFi/liquidity route | IN-QUEUE | ETH (Base) | `contracts/hooks/BribeLoopHook.sol` |
| 16 | Dynamic LP Fee-Switch Hook | DeFi/liquidity route | WIRED-NEEDS-TRIGGER | USDC/WETH (Base) | `onchain/src/SharedLiquidityHook.sol` |
| 17 | MoebiusX | DeFi/liquidity route | LIVE | USDC/WETH (Base) | — (none; see notes) |
| 18 | Agentic Liquidity Vault Management Fees | DeFi/liquidity route | IN-QUEUE | USDC (Base) | `onchain/src/sincor/Sinc4626Vault.sol` |
| 19 | A2A Escrow Float Yield | on-chain settlement | NOT FOUND | none — mechanism not built | — (none; see notes) |
| 20 | x402 Paymaster Gas Sponsorship Markup | x402 API gate | IN-QUEUE | x402 (Base, USDC) | `contracts/paymaster/SincorPaymaster.sol` |
| 21 | P01 Yield Aggregator Vault | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/yield_aggregator.py` |
| 22 | P02 Concentrated Liquidity Manager | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/clmm_manager.py` |
| 23 | P03 Intent Solver & Dark Pool | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/intent_dark_pool.py` |
| 24 | P04 MEV Protection & Capture | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/mev_capture.py` |
| 25 | P05 DeFi Risk Mutual | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/insurance_mutual.py` |
| 26 | P06 Perp DEX Hedging Swarm | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/perp_hedge_swarm.py` |
| 27 | P07 Cross-Chain Bridge Optimizer | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/bridge_optimizer.py` |
| 28 | P08 RWA Tokenization Vaults | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/rwa_vaults.py` |
| 29 | P09 DAO Governance Optimizer | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/dao_governance.py` |
| 30 | P10 Flash Loan Arbitrage Engine | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/flash_arbitrage.py` |
| 31 | P11 Delta-Neutral Yield | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/delta_neutral.py` |
| 32 | P12 TWAMM Large-Order Engine | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/twamm.py` |
| 33 | P13 AVS Tranching & Restaking | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/avs_tranching.py` |
| 34 | P14 Prediction Market Automation | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/prediction_markets.py` |
| 35 | P15 Lending Protocol Optimizer | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/lending_optimizer.py` |
| 36 | P16 Best-Execution DEX Aggregator | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/dex_aggregator.py` |
| 37 | P17 On-Chain Options Protocol | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/options_protocol.py` |
| 38 | P18 Structured Product Vaults | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/structured_products.py` |
| 39 | P19 Decentralized Credit Underwriting | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/credit_underwriting.py` |
| 40 | P20 DeFi Compliance Automation | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/compliance_automation.py` |
| 41 | P21 DAO Treasury Management | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/treasury_dao.py` |
| 42 | P22 Stablecoin Yield Maximizer | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/p22/__init__.py` |
| 43 | P23 NFT-Fi Liquidity Pools | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/p23/__init__.py` |
| 44 | P24 SocialFi Revenue Share | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/p24/__init__.py` |
| 45 | P25 Agent-Managed Portfolio | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/p25/__init__.py` |
| 46 | P26 Self-Improving DeFi OS | DeFi/liquidity route | IN-QUEUE | USDC/ETH (Base) | `src/sincor2/defi/p26/__init__.py` |
| 47 | IFD Intelligence Feeds | enterprise outbound | WIRED-NEEDS-TRIGGER | Stripe fiat / USDC fallback | `src/sincor2/instant_business_intelligence.py` |
| 48 | Governance & Treasury Risk Briefings | enterprise outbound | IN-QUEUE | Stripe fiat / USDC fallback | `src/sincor2/defi/treasury_dao.py` |
| 49 | Sybil & Campaign Verification-as-a-Service | A2A JSON-RPC skill | IN-QUEUE | none — not productized | `src/sincor2/axm_antigaming.py` |
| 50 | B2B Agent Fleet Subscriptions | $297 checkout lane | WIRED-NEEDS-TRIGGER | Stripe fiat (off by default) / USDC fallback | `src/sincor2/platform_payments.py` |
| 51 | Vertical Swarms | vertical services | IN-QUEUE | varies by vertical | `verticals/loader.py` |
| 52 | Enterprise MCP Server & SDK Licensing | enterprise outbound | SEND-READY | Stripe fiat / licensing terms | `src/sincor2/mcp_server.py` |

## Per-path notes

**1. CNP Task Auction Take-Rate** — LIVE · healthcheck: `python3 scripts/verify_money_paths.py --only 1`
  LIVE internal-only. 5% canon: A2A_PLATFORM_FEE_BPS default 500 at a2a_integration.py:104; treasury 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac. Zero realized fees to date. NOTE (corrected post-integration, 2026-11-14): activity_engine.py cited by the status table IS present on this tree at src/sincor2/activity_engine.py — the earlier absence claim was measured on the pre-rebase side-branch tree (45f34309) and does not hold on main 4fd2c248.

**2. AXM Native Settlement Spread** — NOT FOUND · healthcheck: `python3 scripts/verify_money_paths.py --only 2`
  No distinct spread code exists anywhere in the repo (verified). Settlement is 1:1 at the 5% fee; no bid/ask or FX spread is taken.

**3. Vickrey Auction Surplus Routing** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 3`
  Sealed-bid Vickrey engine exists; surplus currently accrues to the poster, not the treasury. Routing the surplus to treasury is the unbuilt step.

**4. Dual-Pool Re-Auction Fees** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 4`
  posterReAuctionBalances at ExecutionEscrowManager.sol:69; no fee is taken on re-auction and the contract is not deployed.

**5. Hybrid Slashing & Bond Forfeiture Cut** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 5`
  Slash/forfeiture logic at SincorTaskEscrow.sol:95-98, treasury constructor-set (:12, :33-36); also contracts/StakeSlashManager.sol and onchain/src/sincor/AgentStakeSlash.sol. None deployed.

**6. TimeLock Early-Exit Penalties** — NOT FOUND · healthcheck: `python3 scripts/verify_money_paths.py --only 6`
  No vesting/timelock contract exists in the repo (verified). The manifest's TimeLockRewardEscrow.sol does not exist.

**7. sincor-x402 Micro-Tariffs** — WIRED-NEEDS-TRIGGER · healthcheck: `python3 scripts/verify_money_paths.py --only 7`
  PyPI sincor-x402 v0.1.1 published; /x402-live returns 200. Blocked on: Railway CDP facilitator vars + redeploy, W-155 settle-response-loss fix, Lane 3 fee-split decision. Lane 3 books no fee split today.

**8. ERC-7579 Session Key Validation Fees** — NOT FOUND · healthcheck: `python3 scripts/verify_money_paths.py --only 8`
  Fee NOT FOUND. Session-key validators exist (onchain/src/sincor/EpochSessionKeyValidator.sol) but charge no validation fee; no toll code exists.

**9. SEAL Underwriting Premiums** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 9`
  The SEAL module itself (marketplace/contract_net/sigil_seal.py) lives on unmerged branch xioix/cnp-01-sigil-seal and is absent on this branch; only the underwriting viewer/policy surface exists here. No premiums collected.

**10. SIGIL Attestation Tolls** — SEND-READY · healthcheck: `python3 scripts/verify_money_paths.py --only 10`
  Module SEND-READY: contracts/modules/SigilPolicyModule.sol IS present on this tree (main 4fd2c248), with its test suite at test/SigilPolicyModule.t.sol — the earlier 'absent on this branch' claim was measured on the pre-rebase side-branch tree (45f34309) and does not hold here (corrected post-integration, 2026-11-14). The attestation TOLL itself is NOT FOUND. ERC-8004 promotion merged via PR #654.

**11. TOA Priority Fees** — NOT FOUND · healthcheck: `python3 scripts/verify_money_paths.py --only 11`
  Fee NOT FOUND. TOA forecasting infrastructure exists but charges no priority fee.

**12. KYA Identity & Reputation Staking** — WIRED-NEEDS-TRIGGER · healthcheck: `python3 scripts/verify_money_paths.py --only 12`
  KYA package + contracts/kya/KYARegistry.sol; genesis SBT live. Outside registrations: 0. Needs external trigger/adoption.

**13. Adjudicator Dispute Fees** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 13`
  Dispute/adjudication flow exists in the inbound market; no dispute fee is charged today.

**14. SINAX Licensing** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 14`
  SINAX is real code (src/sincor2/sinax/, 26 modules) but has no licensing or billing surface; licensing is unbuilt.

**15. BribeLoop Hook MEV** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 15`
  Hook code exists with mock addresses only; never deployed, capturing nothing. The backrun lane it fed was KILLED by measurement (median gaps 0.7-0.8 bps vs 3-94 bps fee floors).

**16. Dynamic LP Fee-Switch Hook** — WIRED-NEEDS-TRIGGER · healthcheck: `python3 scripts/verify_money_paths.py --only 16`
  SharedLiquidityVault LIVE @ 0xeA90a257e5Dae20a0472C4812775F28614459bb6; hook staging LIVE @ 0x5A20BfEc6Caa3A94246eCCCb36F27F4980152dC0. Production pool attachment pending.

**17. MoebiusX** — LIVE · healthcheck: `python3 scripts/verify_money_paths.py --only 17`
  LIVE but external to this repo: the v3.1 trader kit lives at ~/workspace/ops/moebiusx/v3.1-kit/ (deliberate isolation of session-key material). 107 all-time trades, all SUCCESS, through 2026-11-14; pool ~$54.90. In-repo surface is only the v4 hook (onchain/src/hooks/MoebiusMEVHook.sol) and notes.

**18. Agentic Liquidity Vault Management Fees** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 18`
  ERC-4626 vault code exists; no management/performance fee is charged and no external depositors exist.

**19. A2A Escrow Float Yield** — NOT FOUND · healthcheck: `python3 scripts/verify_money_paths.py --only 19`
  No float-yield mechanism exists (verified). Escrowed funds earn nothing today.

**20. x402 Paymaster Gas Sponsorship Markup** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 20`
  Paymaster contract exists with no markup logic; not deployed.

**21. P01 Yield Aggregator Vault** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 21`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**22. P02 Concentrated Liquidity Manager** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 22`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**23. P03 Intent Solver & Dark Pool** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 23`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**24. P04 MEV Protection & Capture** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 24`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**25. P05 DeFi Risk Mutual** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 25`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**26. P06 Perp DEX Hedging Swarm** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 26`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**27. P07 Cross-Chain Bridge Optimizer** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 27`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**28. P08 RWA Tokenization Vaults** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 28`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**29. P09 DAO Governance Optimizer** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 29`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**30. P10 Flash Loan Arbitrage Engine** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 30`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**31. P11 Delta-Neutral Yield** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 31`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**32. P12 TWAMM Large-Order Engine** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 32`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**33. P13 AVS Tranching & Restaking** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 33`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**34. P14 Prediction Market Automation** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 34`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**35. P15 Lending Protocol Optimizer** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 35`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**36. P16 Best-Execution DEX Aggregator** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 36`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**37. P17 On-Chain Options Protocol** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 37`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**38. P18 Structured Product Vaults** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 38`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**39. P19 Decentralized Credit Underwriting** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 39`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**40. P20 DeFi Compliance Automation** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 40`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**41. P21 DAO Treasury Management** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 41`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**42. P22 Stablecoin Yield Maximizer** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 42`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**43. P23 NFT-Fi Liquidity Pools** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 43`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**44. P24 SocialFi Revenue Share** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 44`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**45. P25 Agent-Managed Portfolio** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 45`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**46. P26 Self-Improving DeFi OS** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 46`
  One of the 26 in-platform DeFi product modules (src/sincor2/defi/catalog.py). Not a separately deployed or separately sold product today. Pillar-4 revenue motions span all 26: audit packets SEND-READY, bounty sweeps LIVE (internal scanning), direct integrations and micro-products IN-QUEUE (verified status table, 2026-11-14).

**47. IFD Intelligence Feeds** — WIRED-NEEDS-TRIGGER · healthcheck: `python3 scripts/verify_money_paths.py --only 47`
  Feed engine exists; priced in platform_payments.py PLATFORM_PLANS (BI Report $49, Monthly Intelligence $149/mo). Subscribers: 0.

**48. Governance & Treasury Risk Briefings** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 48`
  Briefing capability rides on the P21 treasury module; no standalone briefing product or subscriber exists.

**49. Sybil & Campaign Verification-as-a-Service** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 49`
  The anti-gaming guard code IS present on main (corrected post-integration, 2026-11-14): src/sincor2/axm_antigaming.py exists on this tree, check_self_bid at axm_antigaming.py:310, enforced via check_bid_eligibility in src/sincor2/a2a_inbound_market.py (close_auction :793, place_bid :1174, commit_bid :1249, reveal_bid :1336). The earlier 'exists only on unmerged branches / ABSENT on this branch' claim was measured on the pre-rebase side-branch tree (45f34309) and is false here. What remains IN-QUEUE is the VaaS product surface — packaging, pricing, and an external API for selling the verification as a service — which is not productized.

**50. B2B Agent Fleet Subscriptions** — WIRED-NEEDS-TRIGGER · healthcheck: `python3 scripts/verify_money_paths.py --only 50`
  PLATFORM_PLANS: starter $297/mo, professional $997/mo, enterprise $2,997/mo, intel $149/mo; USDC 1:1 fallback when AXM unpriced. Live /pricing 200, /checkout 404 (no page), subscribers 0, no payment ever completed.

**51. Vertical Swarms** — IN-QUEUE · healthcheck: `python3 scripts/verify_money_paths.py --only 51`
  verticals/ packages exist (healthcare, dental, compliance, lead_gen, trading, auto_detailing). The only operating vertical is Clinton Auto Detailing — a separate local business, not SINCOR revenue.

**52. Enterprise MCP Server & SDK Licensing** — SEND-READY · healthcheck: `python3 scripts/verify_money_paths.py --only 52`
  MCP server exists; Glama + mcp.so listing kits paste-ready. Smithery needs a deployed public MCP URL; Official MCP Registry blocked on PyPI publish of the server package.

## Reading this index honestly

- **LIVE** means the mechanism runs today (paths 1 and 17 run internally / externally as noted); it does **not** mean outside revenue — realized outside revenue is $0 on all 52 paths.
- **NOT FOUND** means the mechanism does not exist in the code (paths 2, 6, 19 outright; the fee/toll parts of 8 and 11). No handler was fabricated for these; the gap is the record.
- A null entrypoint with a non-NOT-FOUND status (10, 17) means the *revenue mechanism* has no single entrypoint file: for 10 the SIGIL module itself is present on this tree (`contracts/modules/SigilPolicyModule.sol`) but the attestation toll is not implemented; for 17 the trader kit lives outside this repo by design. Each note says exactly where things stand.
- Statuses come from the verified status table (2026-11-14), not from the founder manifest — the manifest is scope, not proof. Of its three named core contracts, `SigilVerifier.sol` and `TimeLockRewardEscrow.sol` do not exist anywhere in the repo; a file named `SincorsettlementEngine.sol` DOES exist at `src/sincor2/SincorsettlementEngine.sol` on main, but it is a 79-line standalone prototype outside both Foundry `src` roots, not the manifest's claimed core settlement contract — the production settlement source is `contracts/ExecutionEscrowManager.sol` and the 5% fee canon lives in Python (`A2A_PLATFORM_FEE_BPS`, `src/sincor2/a2a_integration.py:104`). (Corrected post-integration, 2026-11-14: the earlier blanket "do not exist" was measured on the pre-rebase side-branch tree.)
