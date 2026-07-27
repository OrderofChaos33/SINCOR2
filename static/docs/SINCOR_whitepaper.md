# SINCOR Whitepaper
**Version 1.2 — July 2026**  
**Network:** Base mainnet (chain ID 8453)  
**Live:** [getsincor.com](https://getsincor.com)

---

## Executive Summary

SINCOR is a production autonomous workforce platform: 43+ specialized AI agents coordinated through a decentralized task market, formal quality gates, and wallet-native billing on Base. **SINC** funds platform subscriptions and long-horizon ecosystem utility; **AXIOM (AXM)** settles one-off intelligence work and agent-to-agent execution.

Unlike single-chatbot wrappers, SINCOR agents have archetypes, lifecycles, memory tiers, daily budgets, and promotion paths. Revenue is verifiable on-chain: customers pay treasury directly. Official SINC price discovery is governed by a hard $1.50 USD floor enforced in the Morpho-compatible Chainlink oracle and USDC hook path.

---

## 1. Problem

Businesses need departments (research, outreach, content, sales ops) but cannot hire fast enough or afford 24/7 coverage. Generic LLM chat lacks:

- Persistent memory and accountability across weeks
- Multi-agent specialization and competition for tasks
- Verifiable payment and subscription state on-chain
- Compliance guardrails before content or outreach ships

---

## 2. Solution: Multi-Agent OS

### 2.1 Architecture layers

| Layer | Role |
|-------|------|
| **Cor-tecs Brain** | Claude-powered cross-agent synthesis |
| **Agency Kernel** | Planner → Executor → Critic → Archivist per agent |
| **Swarm Task Market** | Contract-net broadcast, bid, award, audit |
| **4-Tier Memory** | Episodic, semantic, procedural, autobiographical |
| **Business Engine** | BI, pricing, analytics, partnerships, monetization |
| **TOA (agent 44)** | Temporal optimization: forecast → simulate → collapse |
| **SINAX** | Geometric proof navigation atop AxiomSolver (propose, never certify alone) |

### 2.2 Agent archetypes

Scout, Builder, Synthesizer, Negotiator, Director, Auditor, Caretaker — each with OCEAN personality vectors and hard caps (~12k tokens/day, 200 tool calls).

### 2.3 Verticals

Healthcare (RCM + credentialing + HIPAA guardrails), Dental practice ops, Compliance RPA, Lead generation, Trading (OpenClaw / Polyclaw). WebBuilder Swarm (preview-first) is available for local-business acquisition flows. Billing prefers SINC / AXM on Base.

---

## 3. Token Design

### 3.1 SINC (governance & utility)

| Property | Value |
|----------|-------|
| Contract | `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` |
| Decimals | 8 |
| Supply | 100,000,000 (fixed, no mint) |
| CertiK Skynet | 97/100 |
| Verification | Sourcify full-match |

**Official price floor:** $1.50 USD per SINC (non-negotiable). Enforced in:

- `SincChainlinkOracle` (Morpho Blue IOracle, scaled 1e36) — clamps any feed or manual update below 1.50
- USDC hook buy path on getsincor.com/sinc

**Bonding curve (legacy inventory):** `0x75dE341a2BC81806198364F125d4Cde36527619C`  
**Limit-order hook:** `0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0`  
**Genesis NFT:** `0xF3Bd56788b5E56DE638AF5dDffFA478838A68d09`

### 3.2 AXIOM / AXM (execution)

| Property | Value |
|----------|-------|
| Contract | `0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822` |
| Role | One-off reports, A2A task settlement |
| Platform use | AXM-priced intel when configured |

### 3.3 Treasury

Operational treasury: `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`  
Platform billing verifies ERC-20 transfers to this address on Base. Oracle and Morpho setup contracts are owned by the same treasury.

### 3.4 Morpho Blue integration (July 2026)

| Contract | Purpose |
|----------|---------|
| `SincChainlinkOracle.sol` | Hybrid manual + AggregatorV3 feed with hard $1.50 floor; Morpho `price()` returns 1e36-scaled value for SINC(8)/USDC(6) |
| `SincMorphoSetup.sol` | Market creation helper against Morpho Blue + AdaptiveCurveIRM on Base |
| `SincStaking.sol` | Staking with emergency withdraw when paused |

---

## 4. Platform Economics

### 4.1 Billing model (default)

- **SINC** — monthly subscriptions (Starter, Professional, Enterprise, Intel)
- **AXM** — one-off Business Intelligence Report where enabled
- Spot conversion references the official floor and bonding-curve / hook paths
- Legacy card / PayPal paths only when explicitly enabled for an account

### 4.2 Referrals

3% on-chain referral payout on curve buys via `/refer` where applicable.

### 4.3 Safety

- Production kill-switches: no auto-signing of treasury transactions from the web app
- Compliance monitor: internal volume logs only
- Launch content: human approval queue at `/launch/review`
- Healthcare and regulated vertical outputs are labeled decision-support only

---

## 5. TOA — Temporal Optimization Agent

TOA (E-toa-44) answers: *given state and objectives, which actions next, in what order?*

Pipeline: **KernelForecaster** → **MonteCarloSimulator** → **WFCCollapser** → **RollingFeedbackAgent**.

Configurable via `TOA_SIMULATION_DEPTH`, `TOA_COLLAPSE_THRESHOLD`, `TOA_OBJECTIVE_WEIGHTS`. Feedback is ingested after vertical or on-chain outcomes.

---

## 6. SINAX — Geometric Proof Navigation

SINAX augments AxiomSolver with embedding-based proof-space search. **Contract:** SINAX proposes; Lean verifier certifies.

Modes: `analytics` | `suggest` | `active` (with verifier fallback).

---

## 7. Roadmap (high level)

1. Token list certification (Blockscout, Superchain)
2. Morpho Blue SINC/USDC market activation with floor oracle
3. Shared liquidity hooks and additional Base CLMM depth
4. x402 micropayments for API resources
5. Real agent telemetry dashboard
6. WebBuilder studio (admin-gated)

---

## 8. Canonical references

- Addresses: `CANONICAL_ADDRESSES.md`
- Design spec: `docs/superpowers/specs/2026-05-16-sinc-axiom-relaunch-design.md`
- Token list: `/tokenlists/sincor.tokenlist.json`
- Morpho oracle + staking: `onchain/src/morpho/`

---

## Compliance & Risk Disclaimers

**No investment advice.** SINC and AXM are utility tokens for platform access, agent services, and on-chain coordination. They are not securities, equity, profit-sharing instruments, or guaranteed stores of value.

**No income or performance guarantees.** AI agent outputs, forecasts, trading signals, and automation results are informational. Past or simulated performance does not predict future results. Users must perform independent due diligence and, where appropriate, consult licensed professionals before acting on any output.

**Price floor is a protocol design choice, not a guarantee of market value.** The $1.50 hard floor is enforced in the official oracle and designated buy paths. Secondary markets, aggregators, or third-party pools may display different prices; those quotes are outside SINCOR control.

**Regulated domains.** Healthcare, dental, compliance, and financial-adjacent features are decision-support tools only. They do not replace licensed clinical, legal, accounting, or fiduciary advice. HIPAA-related features implement technical safeguards; covered entities remain responsible for their own compliance programs.

**On-chain risk.** Blockchain transactions are irreversible. Smart contracts may contain bugs. Users are solely responsible for wallet security, gas fees, and verifying contract addresses against canonical documentation before signing.

**Geographic and legal restrictions.** Access may be restricted in jurisdictions where token use or the Service is prohibited. Users are responsible for compliance with local law.

**Limitation of liability.** To the maximum extent permitted by law, SINCOR and its operators are not liable for indirect, incidental, special, or consequential damages, or for loss of funds, data, or opportunity arising from use of the platform or tokens.

This document describes technology that continues to evolve. Nothing herein constitutes an offer to sell or solicitation to buy any token or security.

*© 2026 SINCOR*
