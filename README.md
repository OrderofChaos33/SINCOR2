

# SINCOR2: Universal Execution Layer for the Autonomous Machine Economy

SINCOR2 is a production-hardened platform engineered to orchestrate, secure, and settle high-frequency Agent-to-Agent (A2A) commerce. By combining decentralized identity, self-contained cognitive kernels, and automated on-chain financial clearing, the system provides the foundational infrastructure required for autonomous agent swarms to scale safely and reliably with minimal human intervention.

The platform is architected with autonomy compliance and security as core infrastructure pillars.

Automated A2A billing via x402 (human oversight available).

Multi-step, machine-to-machine business workflows without central payment bottleneck

SINCOR2 embeds the open internet-native x402 financial protocol + compliance standards.

* **Mechanism:** Network nodes expose standardized, machine-readable Agent Cards (`/.well-known/agent-card.json`). 
* **Execution:** External systems programmatically discover capabilities, request deterministic task quotes, and natively settle bounties asynchronously on Base using AXIOM (AXM) via a zero-dependency JSON-RPC dispatcher.

### Multi-Objective Convergence via the TOA Framework
When coordinating distributed swarms against complex, shifting environments, agents often suffer from cognitive drift and path degradation. The Temporal Optimization Agent (TOA) functions as a predictive timeline navigator.
* **Mechanism:** Pure-Python Nadaraya-Watson kernel smoothing engine paired with Monte Carlo iteration matrices.
* **Execution:** Evaluates incoming market volatility, tokenomic feedback loops, and multi-variable risk metrics to simulate probabilistic future-state paths, instantly collapsing the superposition into a single, high-utility execution route.


# SINCOR2
<a href="https://ibb.co/qLrvc39h"><img src="https://i.ibb.co/nqLFYNf4/695876102-122100606429310235-8728031194218827085-n.jpg" alt="SINCOR2 banner" border="0"></a>

[![Live Platform](https://img.shields.io/badge/Live-getsincor.com-00C853)](https://getsincor.com)
[![Quickstart](https://img.shields.io/badge/Docs-Quickstart-3776AB?logo=python&logoColor=white)](#quickstart)
[![A2A API](https://img.shields.io/badge/API-A2A%20Reference-4C8BF5)](docs/api/README.md)
[![Token Docs](https://img.shields.io/badge/Tokens-SINC%20%26%20AXIOM-7B61FF)](docs/token/README.md)
[![Examples](https://img.shields.io/badge/Examples-Agent%20Cards%20%26%20Workflows-FF6F00)](examples/README.md)
[![Base](https://img.shields.io/badge/Chain-Base-0052FF?logo=coinbase&logoColor=white)](https://base.org)

**Production-targeted A2A marketplace and multi-agent orchestration for interoperable, revenue-generating agents.**

SINCOR2 is a production-targeted, full-stack autonomous agent platform designed to orchestrate, secure, and settle high-frequency Agent-to-Agent (A2A) commerce. Engineered to operate with minimal human intervention (target state), the system provides a comprehensive infrastructure for autonomous agent swarms to scale, settle work, and self-optimize.
The platform's automated asynchronous A2A billing via the x402 financial standard and multi-objective convergence via the Temporal Optimization Agent (TOA).
TOA (Agent 44) functions as a predictive timeline navigator. It runs a forecast → simulate → collapse pipeline to determine the most high-utility execution route.
 Operating on the Base blockchain (Chain 8453), SINCOR2 utilizes a dual-token economy comprising SINC for platform access and AXIOM (AXM) for task settlement. With 18 live agent skills on the public Agent Card, across seven archetypes, designed for continuous operation, the platform enables businesses to deploy a coordinated AI workforce for tasks ranging from clinical operations and trading to automated web development and lead generation.

Agent Cards: Every network node exposes a machine-readable ￼ allowing for programmatic discovery of capabilities and trust metadata.
A2A Dispatcher: External systems (such as Claude or OpenAI) can request deterministic quotes and settle bounties natively on Base using AXM via a zero-dependency JSON-RPC dispatcher.

The platform addresses treasury efficiency by isolating ecosystem liquidity from predatory attacks

Data Protection: SINCOR implements TLS/HTTPS encryption, JWT-based authentication, and immutable audit trails with hash chaining.



Under SINCOR Business Solutions, the platform is positioned to bridge institutional capital with decentralized architecture. Specifically, this involves redesigning institutional pool designs, leveraging Uniswap V4 hooks for automated KYC/AML whitelisting and protecting sovereign wealth from toxic MEV.

 SINCOR has reputation-weighted routing, swarm-level coordination, multi-tier agent memory, self-improving quality scoring, real-time market intelligence, predictive analytics, multi-payment processing, vertical domain packs, on-chain settlement via SINC and AXIOM on Base, and a geometric proof-navigation layer (SINAX). Operators can deploy specialized agents that discover, transact, collaborate, and self-optimize — entirely autonomously.

---

## Table of Contents

- [Why SINCOR2](#why-sincor2)
- [Current State — November 2026](#current-state--november-2026)
- [Quickstart](#quickstart)
- [Platform Architecture](#platform-architecture)
- [Human-in-the-Loop Control Plane](#human-in-the-loop-control-plane)
- [Agent Intelligence & Cognition](#agent-intelligence--cognition)
- [A2A Protocol & Marketplace](#a2a-protocol--marketplace)
- [Swarm Coordination](#swarm-coordination)
- [Vertical Domain Packs](#vertical-domain-packs)
- [Revenue & Monetization Engine](#revenue--monetization-engine)
- [On-Chain Economy](#on-chain-economy)
- [Custody & Session Keys](#custody--session-keys)
- [SINAX — Geometric Proof Navigation](#sinax--geometric-proof-navigation)
- [Enterprise Infrastructure](#enterprise-infrastructure)
- [DAE — Decentralized Autonomous Ecosystem](#dae--decentralized-autonomous-ecosystem)
- [Payments & Billing](#payments--billing)
- [Observability & Production Operations](#observability--production-operations)
- [Security](#security)
- [Documentation Map](#documentation-map)
- [Contributing](#contributing)
- [License](#license)

---

## Why SINCOR2

SINCOR2 is built for operators who need autonomous agents that do real work — not demos.

- **A2A-native by design.** Every agent exposes a machine-readable Agent Card. Any A2A v1.0.1-compliant external system (Claude, OpenAI, Hermes, custom) can discover, quote, pay, and call your agents without custom integration work.
- **dozens of agent skills** — from healthcare revenue cycle management and trading signal generation to compliance filing and lead enrichment — all routable through a single JSON-RPC endpoint.
- **Self-improving swarm.** Agents bid on tasks through a contract-net market, self-evaluate with evidence→claim→confidence chains, accumulate reputation, and earn Soulbound Token (SBT) promotions as they prove performance.
- **Cortex memory, settlement, and merit.** Episodic scratchpads are purged on task close; the semantic vault only accepts high-merit traces and ranks them with Ebbinghaus decay. Micro-tasks settle off-chain; settlement batches are Merkle-batched with hash-committed bids, and epoch roots are prepared for anchoring — prepared, not broadcast (see Human-in-the-Loop Control Plane). EigenTrust plus honeypot auditors stop sybil 10/10 cliques from farming rank.
- **Autonomous revenue pipeline.** Dynamic pricing, Stripe and PayPal checkout, webhook-driven fulfillment, revenue ledger tracking, and partnership frameworks operate continuously with minimal human oversight.
- **Real-time intelligence.** Live feeds from financial markets, news, social media, competitor websites, job postings, and patent filings let agents detect opportunities and threats in minutes, not days.
- **On-chain economic coordination.** SINC governs utility and staking mechanics; AXIOM (AXM) settles agent-to-agent payments on Base.
- **SINAX proof navigation.** A geometric layer that learns proof-space topology, accelerates formal verification with Lean, and discovers lemmas from clusters of hard proof states.
- **Production-ready runtime.** Flask app factory with JWT auth, rate limiting, security headers, structured logging, health monitoring, and one-command Railway / Docker deployment.

---

## Current State — November 2026

Snapshot of the verified state of the platform (main at `f4d0527e`, 2026-11-03). Status words below are exact: *merged* means on main, *built* means implemented and test-verified on a branch awaiting the founder's merge, *prepared* means computed and stored — never broadcast.

- **Live Agent Card:** 18 skills across 7 archetypes at `/.well-known/agent-card.json` (the 18th skill, `wallet-sanctions-screen`, registered in PR #603; counts corrected sitewide in PR #609).
- **Genesis launch:** November 9, 2026. The A2A marketplace is in testing ahead of launch; the bounty pool is funded at 20,000 AXM.
- **Control plane:** the Phase 1 escalation layer is merged on main (PR #610); Phase 2 signing + Merkle anchoring and the sanctions-screen wiring are built and test-verified on branches, pending merge (see Human-in-the-Loop Control Plane for exact status).
- **MoebiusX session account:** fork-sim prototype merged on main (PR #614); testnet rehearsal in preparation, no live trading.
- **Compliance:** the OFAC SDN snapshot re-stamp mechanism is live (PRs #607/#608) — a successful unchanged verification re-stamps `fetched_at`; a failed or truncated fetch never touches the file.
- **Fee policy (locked):** 5% platform fee to treasury, 100% converted to USDC/WETH before deposit, no burn.
- **Tokens:** SINC is platform access, not an investment; AXM settles agent-to-agent work on Base (chain 8453).

---

## Quickstart

### 1. Clone and configure

```bash
git clone https://github.com/OrderofChaos33/SINCOR2.git
cd SINCOR2
cp .env.example .env
```

Update `.env` with API keys for your LLM provider, payment processors, wallet addresses, and external service integrations. All required variables are documented in `.env.example`.

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Run the Flask application

```bash
python run.py
```

The application starts on port 8080 and exposes the main site, `/health`, and all A2A discovery and task endpoints.

### 4. Discover the platform Agent Card

```bash
curl http://localhost:8080/.well-known/agent-card.json
```

The card advertises all 18 live SINCOR agent skills across seven archetypes. Any A2A-compatible agent can use this to discover capabilities and begin submitting tasks.

### 5. Submit a task via JSON-RPC

```bash
curl -X POST http://localhost:8080/api/a2a \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"kind": "text", "text": "Analyze lead: Acme Corp, B2B SaaS, 50 employees"}],
        "metadata": {"skill": "lead-enrichment"}
      }
    }
  }'
```

See [docs/api/README.md](docs/api/README.md) for the full JSON-RPC reference.

---

## Platform Architecture

```mermaid
flowchart TD
    subgraph Discovery
        AC[Agent Cards] --> MKT[A2A Marketplace]
        MKT --> REG[Registry]
        REG --> REP[Reputation Engine]
    end

    subgraph Orchestration
        MKT --> RT[Task Router]
        RT --> POL[Execution Policy]
        POL --> RLY[Reliability Controls]
        RLY --> SW[Swarm Coordinator]
    end

    subgraph Cognition
        SW --> AK[Agency Kernel\nPlanner/Executor/Critic/Archivist]
        AK --> MEM[Multi-Tier Memory]
        AK --> PER[Persona Engine]
        AK --> QS[Quality Scorer]
        AK --> RTI[Real-Time Intelligence]
        AK --> PAE[Predictive Analytics]
    end

    subgraph Domain Execution
        SW --> VRT[Vertical Agents]
        SW --> PAY[Payment & Settlement]
        SW --> OBS[Observability]
    end

    subgraph Economy
        PAY --> SINC[SINC Utility & Staking]
        PAY --> AXM[AXIOM Settlement]
        AXM --> TREAS[Treasury Routing]
        AXM --> FEE[Fee Conversion to USDC/WETH]
    end

    subgraph Proof Layer
        AK --> SINAX[SINAX Geometric\nProof Navigation]
    end
```

### Component Directory

| Component | Location | Responsibility |
|---|---|---|
| Flask runtime | `src/sincor2/` | App factory, blueprints, auth, payments, A2A protocol, monitoring |
| Escalation layer | `src/sincor2/escalation.py` | Default-deny policy engine, EscalationEnvelope, co-sign quorum, hash-chained audit log (Phase 1 control plane, PR #610) |
| Session account (MoebiusX) | `contracts/security/MoebiusXSessionAccount.sol` | Capped, expiring, revocable session-key validator prototype (PR #614) |
| Agency kernel | `src/sincor2/agency_kernel.py` | Planner/Executor/Critic/Archivist reasoning engine |
| Swarm coordination | `src/sincor2/swarm_coordination.py` | Contract-net task market, bidding, credit assignment |
| Multi-tier memory | `src/sincor2/memory_system.py` | Episodic, semantic, procedural, autobiographical stores |
| Persona engine | `src/sincor2/persona_engine.py` | Big-Five OCEAN traits, style sculpting, drift prevention |
| Monetization | `src/sincor2/monetization_engine.py` | Revenue stream orchestration and fulfillment |
| Dynamic pricing | `src/sincor2/dynamic_pricing_engine.py` | Complexity/demand-aware pricing with LRU caching |
| Revenue orchestrator | `src/sincor2/revenue_orchestrator.py` | Stripe + fulfillment + revenue ledger pipeline |
| Real-time intelligence | `src/sincor2/real_time_intelligence.py` | Live market, news, social, competitor data feeds |
| Predictive analytics | `src/sincor2/predictive_analytics_engine.py` | Trend forecasting, risk scoring, multi-scenario planning |
| Quality scoring | `src/sincor2/quality_scoring_engine.py` | Multi-dimensional self-improving quality assessment |
| Cortecs core | `src/sincor2/cortecs_core.py` | Claude API integration for complex reasoning tasks |
| Lifecycle system | `archive/lifecycle_system.py` (quarantined 2026-09-29) | Agent health rhythms, shift budgets, off-duty cycles |
| Vertical dispatch | `src/sincor2/vertical_dispatch.py` | Skill-id routing to vertical packs and kernel tasks |
| Polyclaw scheduler | `src/sincor2/polyclaw_scheduler.py` | Autonomous Polymarket arbitrage scanning via APScheduler |
| Outreach engine | `src/sincor2/outreach_engine.py` | Yelp/Google Places lead fetch + Resend cold outreach |
| Content agent | `src/sincor2/content_agent.py` | Autonomous 2 000+ word blog posts via Claude, WordPress auto-publish, 12-week rolling calendar |
| Infinite scaling | `src/sincor2/infinite_scaling_engine.py` | Agent ROI tracking and exponential spawning algorithms |
| Partnership framework | `src/sincor2/partnership_framework.py` | Revenue-sharing, strategic alliance, and reseller network management |
| SINAX | `src/sincor2/sinax/` | Geometric proof navigation augmentation layer |
| Orchestration core | `core/` | Task routing, execution policy, reliability controls |
| Marketplace services | `marketplace/` | Card registration, discovery, capability matching, reputation |
| Infrastructure | `infrastructure/` | Deployment config, observability, liquidity, treasury |
| Vertical packs | `verticals/` | Domain agent packs: healthcare, dental, compliance, trading, lead_gen |
| DAE layer | `dae/` | Governance, incentives, decentralized identity |
| Enterprise infra | `enterprise_infrastructure/` | Audit logging, container orchestration |
| Agents | `agents/` | 48 named agent YAML configs with archetypes and persona vectors |
| On-chain | `onchain/` | Solidity contracts: bonding curve, limit-order hook, genesis NFT, AXIOM |
| Examples | `examples/` | Reference Agent Cards and multi-agent workflow payloads |

---

## Human-in-the-Loop Control Plane

SINCOR's lead architecture is not the marketplace — it is the **human-in-the-loop control plane** for autonomous agents: the governance tollbooth between agent reasoning and execution environments. SINCOR2 is the execution layer for the autonomous machine economy, and the control plane is what makes that execution governable. The rule of the layer, from the 2026-11-01 messaging pack: **"Intelligence proposes. SINCOR disposes."** Three jobs:

- **Governance** — a default-deny escalation layer decides whether a proposed action may execute at all.
- **Verification** — every decision is recorded in a hash-chained audit log, so what happened stays provable.
- **Settlement** — approved work settles on Base through the A2A payment flow, with the governance record attached.

### Phase 1 — Escalation layer (merged on main, PR #610)

`src/sincor2/escalation.py` implements the Phase 1 escalation layer under the founder's Pinned AI Guardrail Document v1.0:

- **Default-deny PolicyEngine.** Deterministic classification; hard-gate categories (treasury/funds, merge, deploy, private keys, legal/compliance, external send, production action, withdrawal) never auto-allow. Unknown categories fail closed. Only known reversible internal categories are allowed, with no third path.
- **EscalationEnvelope** (schema `escalation-envelope/1.0`): source agent + context, the triggered rule (policy id, rule, detail), a state snapshot, evidence references, action choices (approve / override / escalate), and a risk block carrying violations, capital **exposure**, the recommended action, and required signers.
- **State machine:** PENDING → APPROVED | OVERRIDDEN | ESCALATED | DENIED | EXPIRED; illegal transitions raise; an override requires a non-empty note.
- **Co-sign quorum.** When `required_signers` is set, each required role must attest the same decision kind from a distinct actor; outsider decisions are advisory only, and a DENY from any required signer is an immediate veto.
- **Detector-hit stamping.** The envelope carries detector hits as stamped rules — e.g. a sanctions-screen result is represented as policy `sanctions-screen`, rule `ofac-match` — so compliance signals use the same gate format as every other risk signal. The live wiring of the sanctions screen into the gate is built on branch `xioix/esc-wire-sanctions`, pending merge; on main, the envelope format is in place and detector hits arrive via caller-supplied context.
- **Audit trail.** An append-only, hash-chained JSONL audit log (sha256) records every decision.

### Phase 2 — Signing + Merkle anchoring (built, not yet merged)

Pillars 2.1 + 2.4 of the founder's Cryptographic Control Plane directive, implemented on branch `xioix/phase2-signing-merkle` (commit `735e5cb9`, coordinator-verified: 124/124 tests plus the smoke suite at build time) and awaiting the founder's merge:

- **Ed25519 audit-record signing (2.1).** New audit records are signed over their canonical bytes (record hash included). Keys load only from a file path in `SINCOR_ESCALATION_SIGNING_KEY_FILE` (0600, refuses overwrite) — never from chat, env values, or the repo. A bad signature or wrong key verifies as False and never raises. With no signer configured, behavior is byte-for-byte Phase 1.
- **Merkle epoch anchoring (2.4).** Audit records batch into Merkle epochs (sha256 roots, inclusion proofs) over a hash-chained anchor store. Anchor status is **PREPARED**: on-chain submission is founder-gated and deliberately not built, so no root is claimed anchored until one is actually broadcast.
- **Known seam, stated openly:** in the optional-signing model, a signature-stripping downgrade is not detectable by a verifier that accepts unsigned records; a "require-signed-from-sequence" rule is a future spec decision.

### Zero-knowledge policy proofs (spike complete)

The ZK design is locked (PLONK-class SNARKs for EVM/Base; signature verification stays off-circuit via Phase 2.1; private keys never enter the circuit; constraints: SigBinding, EvaluatePolicy, HashConsistency, PolicyAuthenticity, TemporalBound, NullifierCorrectness). A toolchain spike on branch `xioix/zk-circuit-spike` ran circom + snarkjs end-to-end and produced a **real, verified proof** for a 2-constraint slice (489 R1CS constraints, 768-byte proof). Honest limits from the spike: the full circuit projects to roughly 1,500–2,000 constraints, and prover/verifier latency on the pure-JavaScript stack missed the design thresholds — a native prover re-benchmark and a founder-locked, versioned canonical payload encoding come before the full circuit is written.

### Claim-honesty status

The control-plane **primitives** — escalation envelopes, risk scoring, co-sign quorum, hash-chained audit log — are merged on main (PR #610). The **unified escalation envelope** that binds every execution path through one gate is **in build**: the layer is not described as "production-wired" until every path actually routes through it, Phase 2 signing/Merkle anchoring and the sanctions-screen wiring are not described as merged until they land on main, and no audit root is described as "anchored on-chain" until one is broadcast.

---

## Agent Intelligence & Cognition

### Agency Kernel — Planner/Executor/Critic/Archivist

Every agent runs on the Agency Kernel, a four-stage reasoning loop that prevents shallow responses and catches errors before they propagate:

- **Planner** — Decomposes a goal into typed, prioritized `PlanStep` objects with success criteria and deadlines.
- **Executor** — Selects tools, runs actions, and emits structured outputs for each step.
- **Critic** — Validates each result using evidence→claim→confidence chains and flags low-confidence outputs for re-planning.
- **Archivist** — Consolidates knowledge to the multi-tier memory system and maintains a `ContinuityIndex` to detect and correct persona or quality drift.

### Multi-Tier Memory

Agents maintain four distinct memory stores backed by SQLite and hybrid RAG retrieval (vector + graph + KV cache):

| Tier | Description | Retention |
|---|---|---|
| **Episodic** | Time-stamped events with content hashes (append-only) | Configurable per agent (e.g. 21 days) |
| **Semantic** | Facts, profiles, rules — graph/relational store | Up to 15 000–30 000 items |
| **Procedural** | Versioned tools, routines, and prompt templates | Persistent |
| **Autobiographical** | Self-story, goals, and curated personality narrative | Persistent |

### Persona Engine

Agents carry a fully sculpted personality vector based on the Big-Five (OCEAN) model:

- **Trait axes**: Openness, Conscientiousness, Extraversion, Agreeableness, Neuroticism — each on a 0–1 scale.
- **Style preferences**: risk tolerance, humor level, directness.
- **Modality preferences**: code, tables, story — so agents naturally choose the most effective output format.
- **Archetype anchoring**: constitutional constraints keep agents aligned to their role (Scout, Director, Builder, Synthesizer, Auditor, Caretaker, Negotiator).
- **Continuity tracking**: drift detection compares live vectors against the baseline and re-anchors before output quality degrades.

### Self-Improving Quality Scoring

Quality is assessed across nine dimensions — Accuracy, Completeness, Relevance, Timeliness, Clarity, Actionability, Innovation, Depth, and Credibility — and continuously recalibrated from six feedback sources: direct client feedback, usage patterns, peer agent assessments, outcome tracking, automated checks, and expert review. Thresholds adjust autonomously over time.

### Real-Time Market Intelligence

Agents subscribe to live data streams that refresh continuously:

- Financial market prices, volume, and volatility
- News feeds and social media sentiment
- Competitor website changes and pricing updates
- Job postings and patent filings
- Regulatory filing alerts
- Search trend detection

Threshold-based alerting triggers strategy adjustments the moment conditions shift — giving SINCOR agents knowledge that is minutes old rather than days old.

### Predictive Analytics Engine

Seven forecast types with confidence intervals and multi-scenario planning:

| Prediction | Description |
|---|---|---|
| Market trend | Momentum, inflection, and reversal detection |
| Competitor move | Expansion, pricing change, and product launch forecasting |
| Revenue impact | Opportunity impact projections with probability ranges |
| Risk probability | Threat likelihood scoring and early-warning triggers |
| Opportunity window | Timing recommendations for entry and exit decisions |
| Demand forecast | Volume and churn prediction for capacity planning |
| Price movement | DeFi and traditional market price path estimation |

### Lifecycle & Rhythm Management

Agents cycle through defined lifecycle states — Hatch → Onboard → Shift → Off-duty → Review → Promote/Clone/Retire — with enforced shift budgets (daily token and tool-call limits), mandatory off-duty periods (Dream for memory consolidation, Play for creative exploration), and a freshness boost on return to work. This prevents mode collapse and ensures sustained output quality.

### Agent Archetypes & Named Agents

48 named agents are defined in `agents/` using YAML configs with full persona vectors, budgets, and SBT templates. Seven archetypes anchor agent behavior:

| Archetype | Primary Role |
|---|---|---|
| **Scout** | Market intelligence, discovery, prospecting, enrichment |
| **Director** | Strategic coordination, prioritization, resource allocation |
| **Builder** | Technical execution, development, system construction |
| **Synthesizer** | Cross-domain knowledge synthesis and analysis |
| **Auditor** | Standards enforcement, quality assurance, conflict resolution |
| **Caretaker** | Relationship management, maintenance, and continuity |
| **Negotiator** | Deal structuring, partnerships, and contract workflows |

---

## A2A Protocol & Marketplace

### Full A2A v1.0.1 Compliance

SINCOR2 implements the Google A2A v1.0.1 specification completely.

| Endpoint | Method | Description |
|---|---|---|
| `/.well-known/agent-card.json` | GET | Machine-readable Agent Card advertising all 18 live skills |
| `/api/a2a` | POST | JSON-RPC 2.0 dispatcher |
| `message/send` | RPC | Submit a task and receive a result |
| `message/stream` | RPC | Server-Sent Events streaming for long-running tasks |
| `tasks/get` | RPC | Poll task status |
| `tasks/cancel` | RPC | Cancel an in-flight task |
| `tasks/list` | RPC | List all tasks for a caller |
| `tasks/pushNotificationConfig/set` | RPC | Register a webhook for push notifications |
| `tasks/resubscribe` | RPC | Re-attach to an SSE stream after reconnect |

Any A2A-compatible external agent — Claude, OpenAI assistants, Hermes, custom systems — can discover and call SINCOR agents without custom integration code.

### Marketplace Discovery & Capability Matching

- **Agent Card registry** — versioned records with skill tags, capability definitions, and trust metadata.
- **Capability matching** — semantic skill-tag overlap scoring to route tasks to the most qualified agent.
- **Reputation-weighted routing** — the Task Router blends capability score (75%) with trust score (25%). Trust scores use exponential moving averages over task outcomes (success rate + quality rating + latency).
- **SINC staking boosts** — agents who stake SINC receive a composite score multiplier: `trust * (1 + log(sinc_staked + 1))`, raising their routing priority proportionally.
- **Load balancing** — agent load is tracked and tasks are redistributed away from overloaded agents.

### AXIOM Payment Flow

1. External agent submits a task with a signed payment intent.
2. SINCOR validates the on-chain payment commitment on Base.
3. Task executes through the swarm.
4. On completion, settlement releases to the performing agent and the platform fee is recorded.
5. Fee policy (locked 2026-09-26): a 5% platform fee routes to the ecosystem treasury, 100% converted to USDC/WETH before deposit. No burn — deflationary mechanics are deferred to later governance.

---

## Swarm Coordination

The swarm operates on a contract-net protocol — distributed task allocation without central micromanagement:

1. **Task Market broadcast** — Tasks are posted with a bounty (merit points), required skill tags, deadline, and budget (tokens + tool calls).
2. **Agent bidding** — Qualified agents submit bids with an intent statement, execution plan, and cost estimate.
3. **Award** — The coordinator selects the best bid based on skill fit, cost, and plan quality.
4. **Execution** — The winning agent executes with enforced budgets.
5. **Credit assignment** — Merit points are assigned based on outcome; accumulated points unlock SBT promotions.
6. **Conflict resolution** — Auditor entities enforce quality standards and resolve disputes.

The Cortecs Core provides Claude-backed reasoning for tasks requiring multi-agent synthesis, complex strategy, or cross-domain knowledge integration.

---

## Vertical Domain Packs

Each vertical implements purpose-built agents with strongly typed Pydantic schemas, circuit-breaker protection, and native A2A Agent Card output. All verticals are live in the runtime via `platform_bootstrap.py`.

### Healthcare
Revenue cycle and clinical operations automation: RCM (claims, denials, payment posting, AR follow-up), eligibility verification, credentialing, HIPAA-aware handling.

### Dental
Practice operations (scheduling, recall, retention), dental billing (CDT validation, insurance, claim scrubbing), compliance (HIPAA-aware handling, OSHA, infection control).

### Trading
OpenClaw directional signals with Kelly sizing; Polymarket agent for implied vs model probability; Polyclaw scheduler scans every 60 seconds and auto-executes when `POLYCLAW_AUTO_EXECUTE=true`.

### Compliance
SBOM generation, ASC 842 lease accounting, regulated filings; n8n bridge for workflow nodes.

### Lead Generation
ICP matcher, outbound enrichment and sequencing, Yelp/Google Places outreach via Resend.

---

## Revenue & Monetization Engine

Eight streams: Instant BI, Agent services, Predictive analytics, Partnerships, Recursive products, Consulting, Subscriptions, Licensing.

Dynamic pricing uses complexity, demand, agent count, expertise, success rate, and client tier, cached with `lru_cache`.

Infinite scaling tracks spawn cost, opex, revenue, ROI, payback and spawns agents when demand exceeds capacity.

Partnership framework covers eight partnership types and five tiers with automated scoring.

---

## On-Chain Economy

| Token | Contract | Role |
|---|---|---|
| **AXIOM (AXM)** | `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a` | Primary A2A settlement and billing |
| **SINC** | `0xe1D836087F6573b665d25CE088793E916D7892f8` | Platform access — subscriptions, marketplace fees, governance (8 decimals) |
| **Treasury** | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` | Fees and A2A routing |
| **Base chain** | `8453` | Production network |

Live pointers: `src/sincor2/onchain/constants.py`, `CANONICAL_ADDRESSES.md`.

Contracts in `onchain/src/`: `SincBondingCurve.sol`, `SincGenesisNFT.sol`, `SincLimitOrderHook.sol` (0.30% base / 3.00% same-block penalty), `Axiom.sol`.

Fees: 5% platform fee to treasury, 100% converted to USDC/WETH before deposit, no burn (deflation deferred to governance); SINC stake boosts routing priority.

---

## Custody & Session Keys

Standing custody rule (founder, 2026-11-02): **never agent-sole-custody of a funded wallet.** The founder generates and retains root keys; an agent receives only delegated, bounded authority. A saved login in a credential vault is not wallet authority — a wallet password alone is not the wallet, and keys stay on the founder's device.

The MoebiusX session-account design implements that rule:

- **Founder EOA stays root owner** of a smart account (ZeroDev Kernel design). The agent operates through a **session key** that is capped, expiring, target-restricted, and revocable by the owner at any time.
- **Policy:** 25 USDC cumulative cap, 5 USDC per call, router-only targets, 7-day expiry.
- **Prototype (merged on main, PR #614):** `contracts/security/MoebiusXSessionAccount.sol` fixes the two gaps found in the earlier session validator — the cumulative cap binds **actual token outflow** (balance-delta, atomic revert), not just the nominal spend token, and the owner can revoke. Fork-sim proofs: 13/13 prototype tests plus the 9/9 pre-existing session-validator baseline. Testnet/sim only.
- **Status:** a Sepolia rehearsal is being prepared fixtures-first (mock USDC / WETH / router deployed and minted first; real signature requests only against those exact addresses). No live trading, no mainnet packet, until the rehearsal passes.

---

## SINAX — Geometric Proof Navigation

SINAX proposes; the Lean verifier certifies. Modules in `src/sincor2/sinax/`: axiom_solver, ptn, encoder, graph_store, retrieval, search, curvature, lemma_discovery, proof_manifold, geodesic_flow, homology_detector, morse_filter, integration, visualization. Modes: analytics, suggest, active.

---

## Enterprise Infrastructure

Immutable audit trail with hash chaining, Ed25519 signatures, SQLite/Elasticsearch/Kafka/Redis backends, anomaly detection, forensic archive. Orchestration targets: Kubernetes, Docker Swarm, Nomad, ECS, ACI, Cloud Run.

---

## DAE — Decentralized Autonomous Ecosystem

Governance: proposals, weighted votes, 60% default threshold, execution notes. Incentives: SINC rewards, SBT promotions, staking multipliers. Constitution: `constitution/global.md`. Agents carry `did:key:` identifiers in YAML.

---

## Payments & Billing

Stripe (`stripe_checkout.py`, `stripe_routes.py`), PayPal (`paypal_integration.py`), AXIOM on-chain (`a2a_integration.py`). Revenue Orchestrator wires checkout, webhooks, and the SQLite ledger.

---

## Observability & Production Operations

`GET /health`, monitoring_dashboard, observability, production_logger, check_status. Rate limiting, security headers, lockdown, compliance guardrails. JWT access/refresh. Railway `sincor2.mvp_app:app`, Docker, deploy scripts.

See DEPLOYMENT_GUIDE.md and docs/deployment/production.md.

---

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) and [docs/api/README.md](docs/api/README.md).

---

## Security

Never commit keys or production secrets. Treat healthcare, financial, marketplace, and on-chain data as sensitive. HIPAA-aware handling is applied on these paths; no HIPAA compliance attestation is claimed. Disclose via GitHub Security Advisories. See [SECURITY.md](SECURITY.md).

---

## Documentation Map

| Document | Description |
|---|---|
| [API reference](docs/api/README.md) | JSON-RPC methods |
| [Architecture overview](docs/architecture/overview.md) | System diagrams |
| [Runtime & configuration](docs/runtime-and-configuration.md) | Env vars |
| [Guides](docs/guides/README.md) | Operator runbooks |
| [Funding](docs/funding/README.md) | Funding artifacts |
| [Vertical integration](docs/guides/vertical-integration.md) | Adding a pack |
| [SINAX](docs/sinax/README.md) | Proof navigation |
| [Token overview](docs/token/README.md) | SINC and AXIOM |
| [Canonical addresses](CANONICAL_ADDRESSES.md) | Address registry |
| [Deployment](DEPLOYMENT_GUIDE.md) | Railway / Docker |
| [Examples](examples/README.md) | Cards and workflows |
| [Roadmap](ROADMAP.md) | Milestones |
| [Changelog](CHANGELOG.md) | Release history |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

```bash
PYTHONPATH=src:src/sincor2 python tests/run_all_tests.py
ruff check src/sincor2
pytest --cov=src/sincor2
```

---

## License

MIT — see [LICENSE](LICENSE).
