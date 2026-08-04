# SINCOR2 CEO Full Execution Plan — 2026-08-04

**Directive**: Parallel execution on all fronts. Product first. Outside agents in the A2A ecosystem. Hooks live + loops on testnet. SINC whitelisted everywhere possible. Polygon/BNB SINC deferred until product is worth buying.

**Primary metric**: Treasury inflow to `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`.

> **🌕 Harvest Moon Activation** — Sep 26, 2026 16:48 UTC  
> All Harvest Moon build tracks execute in parallel with the tracks below.  
> **Gate**: No public Harvest push until product conversion gate is cleared.  
> See: [`docs/launch/HARVEST_GATE.md`](launch/HARVEST_GATE.md) | [`docs/launch/CLAIM_SPEC.md`](launch/CLAIM_SPEC.md) | [`docs/launch/ASSET_INVENTORY_2026-08.md`](launch/ASSET_INVENTORY_2026-08.md)

---

## 1. A2A — Outside Agents in the Ecosystem (HIGHEST PRIORITY)

**Goal**: Any external A2A v1.0.1-compliant agent (Claude, OpenAI, Hermes, CrewAI, LangGraph, custom) can discover SINCOR agents, quote, pay in AXM/SINC, and execute tasks without custom integration.

### Current State
- `src/sincor2/a2a_integration.py` (57k) implements discovery + JSON-RPC.
- Platform Agent Card at `/.well-known/agent-card.json` and legacy `/.well-known/agent.json`.
- `scripts/register_agent.py` + `examples/external_agent_registration.sh` exist.
- Adapters: `adapters/crewai_adapter.py`, `langgraph_adapter.py`, `beeai_adapter.py`, `generic_adapter.py`.
- Marketplace registry + reputation + settlement in `marketplace/`.
- Examples: `examples/a2a_loop_demo.py`, `examples/crewai_onboard.py`, `examples/workflows/cross_framework_workflow.py`.

### Builder Actions (Immediate)
1. **Harden public Agent Card**  
   - Ensure live Railway deployment serves complete v1.0.1 Agent Card with full skills array (43+), supportedInterfaces (JSONRPC preferred), securitySchemes, capabilities.streaming=false|true as implemented.  
   - Add signed Agent Card support (v1.0.1 JWS) if not present.
2. **External registration path**  
   - Make `POST /api/marketplace/register` (or existing registry endpoint) accept external Agent Cards with rate limits + reputation bootstrap.  
   - Document one-command onboarding: `curl ... | ./examples/external_agent_registration.sh`.
3. **Quote + settlement for outsiders**  
   - `/api/a2a/quote` must return AXM amount + treasury fee split.  
   - Settlement must auto-route fee portion to Treasury; burn portion of AXM as designed.
4. **Cross-framework demos live**  
   - Run and publish results of `examples/crewai_onboard.py` and `cross_framework_workflow.py` against production endpoint.  
   - Add one more adapter if missing (e.g., OpenAI Assistants or Anthropic tool-use wrapper).
5. **Operator UI**  
   - `templates/operator_dashboard.html` already discovers cards and submits tasks — ensure it works against live endpoint and shows external agents.

**Success criteria**: An external agent can discover → quote → pay AXM → execute a skill → receive artifact, with fee visible on Basescan to Treasury.

---

## 2. Hooks Live + Hook Loops on Testnet

**Goal**: SharedLiquidityHook, LiquidityAmplifierHook, MoebiusMEVHook, SincLimitOrderHook, AutoCapitalizeMonetizeHook, and lending-loop containers deployable and looping on Base Sepolia (or Base testnet equivalent). Fee routing verified.

### Current State
- Production-ready Solidity in `onchain/src/`: SharedLiquidityHook.sol, LiquidityAmplifierHook.sol, SincLimitOrderHook.sol, MoebiusMEVHook.sol, AutoCapitalizeMonetizeHook.sol, SINCLending.sol, SharedLiquidityVault.sol, etc.
- Foundry scripts: `onchain/script/04_MineHookAddress.s.sol`, `05_DeployHook.s.sol`, Deploy*.s.sol.
- Tests: SharedLiquidityHook.t.sol, MoebiusMEVHook.t.sol + fork tests, SINCLending.t.sol, etc.
- Proposals under `onchain/proposals/hooks/`.

### Builder Actions
1. **Testnet deploy script**  
   - Extend `onchain/script/deploy-base.sh` or create `deploy-base-sepolia.sh` with correct PoolManager address for Base Sepolia.  
   - Mine CREATE2 salts for required hook flags.  
   - Deploy SharedLiquidityHook + LiquidityAmplifierHook first (highest revenue path).
2. **Hook loop verification**  
   - After deploy: initialize pool, add liquidity, execute swaps that trigger before/afterSwap and liquidity hooks.  
   - Confirm fee accrual and transfer to Treasury address.  
   - Run existing Foundry tests against the deployed addresses (fork or live).
3. **Agent wiring**  
   - Link `agents/defi_yield_aggregator_agent.yaml` and TOA to call the testnet hooks via SincSwapRouter / vault client.  
   - Scheduler check-in must report hook TVL / fee metrics.
4. **No mainnet until green**  
   - Product (agent marketplace + paid verticals) must show real usage before mainnet hook graduation.

**Success criteria**: At least two hooks live on Base Sepolia with measurable fee events to a test Treasury, callable by an agent.

---

## 3. SINC Whitelisted Everywhere

**Goal**: Maximize discoverability and usability of Base SINC (`0x9C8cd8d3961F445D653713dE65C6578bE11668e7`).

### Current Assets
- `scripts/whitelist_token.py` (30k) — primary tool.  
- `scripts/register_blockscout_token.py`, `scripts/certify_token.py`.  
- Tokenlist: `static/tokenlists/sincor.tokenlist.json` + `tokenlists/`.  
- Blockscout submit notes in `tokenlists/blockscout/`.

### Builder / Ops Actions
1. Run `python scripts/whitelist_token.py` end-to-end; log every success/failure.  
2. Submit/verify on: Basescan, Blockscout, CoinGecko request, CoinMarketCap request, DexScreener, GeckoTerminal, Uniswap Token Lists, Coinbase Wallet list (PR if open), MetaMask token detection, 1inch, Kyber, Paraswap token lists where applicable.  
3. Keep official buy path only via bonding curve + USDC hook ($1.50 floor). Document rogue V2 pool warning on every surface.  
4. Update `docs/SINC_INTEGRATION.md` and `docs/TOKEN_ADOPTION_PLAN.md` with current status table.

**Success criteria**: SINC appears in at least 5 major indexes/explorers with correct metadata and official curve link.

---

## 4. Product Worth Buying (Foundation)

**Goal**: Paid plans and verticals convert. Without this, multi-chain and advanced DeFi are premature.

### Parallel Workstreams
1. **WebBuilder Swarm** — production vertical already wired; close first 3 paying customers this week.  
2. **Starter/Pro/Enterprise checkout** — SINC wallet billing on Base must be frictionless; Stripe/PayPal fallbacks only if already live.  
3. **Marketplace UX** — Operator dashboard + agent selector must let a human or external agent buy a skill in < 3 minutes.  
4. **Content + outreach** — Sales/Negotiator agents push demos; measure by paid conversions, not vanity metrics.

Polygon SINC + BNB audited contracts: **parked**. Integrate only after product revenue is proven.

---

## 5. DeFi Swarm Continuity (from prior brief)

- Keep 5-min `scripts/defi_swarm_checkin_scheduler.py` running.  
- TOA revenue ranking still primary.  
- Top-3 builds (Yield Aggregator, OL TWAMMI, Neutral Yield / JIT) continue in parallel with A2A and testnet hooks.

---

## Execution Order (Parallel Tracks)

| Track | Owner Archetype | First 24h Deliverable |
|-------|-----------------|-----------------------|
| A2A external agents | Builder + Director | Live Agent Card verified + one external registration success |
| Testnet hooks | Builder + Auditor | SharedLiquidityHook + LiquidityAmplifierHook on Base Sepolia |
| Whitelist | Scout + Ops | whitelist_token.py run + status table updated |
| Product conversion | Negotiator + Caretaker | 1 paying customer or 3 qualified demos closed |
| DeFi swarm | TOA + Builder | Scheduler green + 1 production commit on top-ranked project |

All tracks report status into the 5-min check-in loop and Treasury dashboard.

---

## Non-Negotiables
- Only fully tested code.  
- Fee routing to Treasury on every new surface.  
- No half-measures.  
- Outside agents must be first-class citizens of the A2A marketplace.  
- Product revenue before multi-chain expansion.

**CEO**: Plans issued. Branch `ceo/2026-08-04-full-execution`. Execute now. Scale infinite.
