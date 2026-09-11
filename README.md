# SINCOR2: Universal Execution Layer for the Autonomous Machine Economy

SINCOR2 is a production-hardened platform engineered to orchestrate, secure, and settle high-frequency Agent-to-Agent (A2A) commerce. By combining decentralized identity, self-contained cognitive kernels, and automated on-chain financial clearing, the system provides the foundational infrastructure required for autonomous agent swarms to scale independently of human intervention.

The platform is architected around three core infrastructure pillars:

### 1. Sovereign Asynchronous Billing via x402 Compliance
To enable multi-step, machine-to-machine business workflows without central payment bottlenecks, SINCOR2 embeds the open internet-native x402 financial standard. 
* **Mechanism:** Network nodes expose standardized, machine-readable Agent Cards (`/.well-known/agent-card.json`). 
* **Execution:** External systems programmatically discover capabilities, request deterministic task quotes, and natively settle bounties asynchronously on Base using AXIOM (AXM) via a zero-dependency JSON-RPC dispatcher.

### 2. Adversarial MEV Protection via Uniswap V4 Hooks
Traditional public ledger environments introduce toxic slippage and front-running that destroy corporate treasury efficiency. SINCOR2 isolates ecosystem liquidity at the smart contract perimeter.
* **Mechanism:** Integrated deployment of `SincLimitOrderHook.sol`.
* **Execution:** Enforces an algorithmic fee multiplier that detects atomic, multi-swap transactions within a single block. The hook scales from a 0.30% base fee to a 3.00% penalty block, completely breaking the economic viability of predatory sandwich attacks.

### 3. Multi-Objective Convergence via the TOA Framework
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

**Production-grade A2A marketplace and multi-agent orchestration for interoperable, revenue-generating agents.**

SINCOR2 is a full-stack autonomous agent platform. It combines Google A2A v1.0.1 interoperability, a live marketplace with reputation-weighted routing, swarm-level coordination, multi-tier agent memory, self-improving quality scoring, real-time market intelligence, predictive analytics, multi-payment processing, vertical domain packs, on-chain settlement via SINC and AXIOM on Base, and a geometric proof-navigation layer (SINAX). Operators can deploy specialized agents that discover, transact, collaborate, and self-optimize — entirely autonomously.

---

## Table of Contents

- [Why SINCOR2](#why-sincor2)
- [Quickstart](#quickstart)
- [Platform Architecture](#platform-architecture)
- [Agent Intelligence & Cognition](#agent-intelligence--cognition)
- [A2A Protocol & Marketplace](#a2a-protocol--marketplace)
- [Swarm Coordination](#swarm-coordination)
- [Vertical Domain Packs](#vertical-domain-packs)
- [Revenue & Monetization Engine](#revenue--monetization-engine)
- [On-Chain Economy](#on-chain-economy)
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

SINCOR2 is built for operators who need autonomous agents that generate real revenue — not demos.

- **A2A-native by design.** Every agent exposes a machine-readable Agent Card. Any A2A v1.0.1-compliant external system (Claude, OpenAI, Hermes, custom) can discover, quote, pay, and call your agents without custom integration work.
- **43 live agent skills** — from healthcare revenue cycle management and trading signal generation to compliance filing and lead enrichment — all routable through a single JSON-RPC endpoint.
- **Self-improving swarm.** Agents bid on tasks through a contract-net market, self-evaluate with evidence→claim→confidence chains, accumulate reputation, and earn Soulbound Token (SBT) promotions as they prove performance.
- **Cortex memory, settlement, and merit.** Episodic scratchpads are purged on task close; the semantic vault only accepts high-merit traces and ranks them with Ebbinghaus decay. Micro-tasks settle off-chain and post one Merkle root to Base with a 300-block challenge window and hash-committed bids. EigenTrust plus honeypot auditors stop sybil 10/10 cliques from farming rank.
- **Autonomous revenue pipeline.** Dynamic pricing, Stripe and PayPal checkout, webhook-driven fulfillment, revenue ledger tracking, and partnership frameworks operate continuously without human intervention.
- **Real-time intelligence.** Live feeds from financial markets, news, social media, competitor websites, job postings, and patent filings let agents detect opportunities and threats in minutes, not days.
- **On-chain economic coordination.** SINC governs utility and staking mechanics; AXIOM (AXM) settles every agent-to-agent payment on Base with built-in deflationary burn mechanics and a Uniswap V4 limit-order hook that protects against sandwich attacks.
- **SINAX proof navigation.** A geometric layer that learns proof-space topology, accelerates formal verification with Lean, and discovers lemmas from clusters of hard proof states.
- **Production-ready runtime.** Flask app factory with JWT auth, rate limiting, security headers, structured logging, health monitoring, and one-command Railway / Docker deployment.

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

The card advertises all 43 SINCOR agent skills. Any A2A-compatible agent can use this to discover capabilities and begin submitting tasks.

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
        AXM --> BURN[Burn Mechanics]
    end

    subgraph Proof Layer
        AK --> SINAX[SINAX Geometric\nProof Navigation]
    end
```

### Component Directory

| Component | Location | Responsibility |
|---|---|---|
| Flask runtime | `src/sincor2/` | App factory, blueprints, auth, payments, A2A protocol, monitoring |
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
| Lifecycle system | `src/sincor2/lifecycle_system.py` | Agent health rhythms, shift budgets, off-duty cycles |
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
| Agents | `agents/` | 43 named agent YAML configs with archetypes and persona vectors |
| On-chain | `onchain/` | Solidity contracts: bonding curve, limit-order hook, genesis NFT, AXIOM |
| Examples | `examples/` | Reference Agent Cards and multi-agent workflow payloads |

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

43 named agents are defined in `agents/` using YAML configs with full persona vectors, budgets, and SBT templates. Seven archetypes anchor agent behavior:

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
| `/.well-known/agent-card.json` | GET | Machine-readable Agent Card advertising all 43 skills |
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
4. On completion: 50% of received AXM is burned to the dead address (deflationary); 50% routes to the ecosystem treasury.
5. Uniswap V4 trading fees: 80% of AXM/WETH pool fees route to the treasury independently.

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
Revenue cycle and clinical operations automation: RCM (claims, denials, payment posting, AR follow-up), eligibility verification, credentialing, HIPAA guardrails.

### Dental
Practice operations (scheduling, recall, retention), dental billing (CDT validation, insurance, claim scrubbing), compliance (HIPAA, OSHA, infection control).

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
| **SINC** | `0xe1D836087F6573b665d25CE088793E916D7892f8` | Residual / legacy holders (8 decimals) |
| **Treasury** | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` | Fees and A2A routing |
| **Base chain** | `8453` | Production network |

Live pointers: `src/sincor2/onchain/constants.py`, `CANONICAL_ADDRESSES.md`.

Contracts in `onchain/src/`: `SincBondingCurve.sol`, `SincGenesisNFT.sol`, `SincLimitOrderHook.sol` (0.30% base / 3.00% same-block penalty), `Axiom.sol`.

Deflation: 50% of A2A AXM burned, 50% to treasury; 80% of AXM/WETH pool fees to treasury; SINC stake boosts routing priority.

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

Never commit keys or production secrets. Treat healthcare, financial, marketplace, and on-chain data as sensitive. HIPAA paths go through compliance guardrails. Disclose via GitHub Security Advisories. See [SECURITY.md](SECURITY.md).

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
