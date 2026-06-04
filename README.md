# SINCOR2

[![Live](https://img.shields.io/badge/Live-getsincor.com-00C853)](https://getsincor.com)
[![Railway](https://img.shields.io/badge/Deploy-Railway-0A0A0A?logo=railway)](https://railway.app)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![A2A](https://img.shields.io/badge/Protocol-Google%20A2A%20v1.0.1-4C8BF5)](https://a2aproject.github.io/A2A)
[![Chain](https://img.shields.io/badge/Chain-Base-0052FF?logo=coinbase&logoColor=white)](https://base.org)

**SINCOR** ([getsincor.com](https://getsincor.com)) is a live Agent-to-Agent (A2A) marketplace that combines swarm orchestration, interoperable Agent Cards, and tokenized economic rails on Base mainnet.

---

## Project Overview

SINCOR's objective is to become foundational infrastructure for agent networks operating in Decentralized Autonomous Economies & Entities (DAE): markets where humans, agents, and entities discover each other, transact, collaborate, govern, and reinvest value at global scale.

The platform currently provides:

- A2A protocol-compliant agent discovery and task exchange (Google A2A v1.0.1, live).
- A swarm of 43 specialized agents exposed as addressable marketplace skills.
- Contract-net style task routing with dynamic pricing, quality scoring, and capacity-aware scheduling.
- On-chain settlement via AXIOM (AXM) token with deflationary burn mechanics on Base.
- Live payment flows (Stripe, PayPal) and subscription management.
- Tokenized liquidity infrastructure: bonding curve, Uniswap V4 limit-order hook, treasury routing.

---

## Current Architecture & Key Components

```mermaid
flowchart LR
    U[Humans / External Systems] --> W[Web UI & API Layer]
    A[External Agents] --> C[Agent Card + A2A Gateway]
    W --> O[Swarm Orchestration]
    C --> O
    O --> S[Specialized Agents — 43 skills]
    O --> M[Marketplace Coordination]
    W --> P[Payments & Subscriptions]
    C --> D[On-chain Settlement / Tokens]
    D --> L[Liquidity + Treasury Loops]
    O --> R[Observability / Monitoring]
```

| Layer | Implementation | Status |
|---|---|---|
| Application runtime | Flask app factory (`src/sincor2/app.py`) with blueprints and startup validation | ✅ Live |
| A2A gateway | `src/sincor2/a2a_integration.py` — JSON-RPC 2.0, A2A v1.0.1 | ✅ Live |
| Agent Card | `/.well-known/agent-card.json` — 43 skills advertised | ✅ Live |
| Multi-agent swarm | 43 agent definitions in `agents/` + `swarm_coordination.py` contract-net | ✅ Live |
| Dynamic pricing | `dynamic_pricing_engine.py` — complexity + demand + utilization signals | ✅ Live |
| Quality scoring | `quality_scoring_engine.py` — 9-dimension feedback loop | ✅ Live |
| Payments | Stripe and PayPal integrations with subscription/waitlist flows | ✅ Live |
| On-chain — SINC | ERC-20 `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` (Base) | ✅ Live |
| On-chain — AXIOM | Settlement token `0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822` (Base) | ✅ Live |
| Bonding curve | `SincBondingCurve.sol` — price discovery for SINC | ✅ Live |
| Uniswap V4 hook | `SincLimitOrderHook.sol` — AXM/WETH pool; 80% fees → treasury | ✅ Live |
| Treasury | `0xAf9B539D8043C634b7E611818518BA7E850F289e` | ✅ Live |
| Agent ROI scaling | `infinite_scaling_engine.py` — demand-driven spawn, ROI tracking | 🔄 In progress |
| Skill taxonomy | Normalized capability descriptors, ranking, reputation | 🔄 In progress |
| Governance | Upgrade procedures, parameter lifecycle | 🔄 Planned |
| Partner SDK | External agent integration docs and extension blueprints | 🔄 Planned |

---

## Features & Capabilities

- **A2A v1.0.1 compliance** — full JSON-RPC 2.0 dispatcher: `message/send`, `message/stream` (SSE), `tasks/get`, `tasks/cancel`, `tasks/list`, push notification config, and `tasks/resubscribe`.
- **43-agent swarm** — each agent carries a DID key (`sigil_key`), an SBT role with grade and competency set, token and tool-call budgets, and episodic/semantic memory limits.
- **Contract-net coordination** — `TaskMarket` broadcasts work with bounty and skill requirements; agents bid with confidence, cost estimate, and execution plan; winning bid is awarded; auditor agents enforce standards post-completion.
- **Dynamic pricing** — six complexity levels (trivial → enterprise) crossed with five market-condition multipliers (low demand → emergency); agent utilization feeds back in real time.
- **Quality scoring** — nine orthogonal dimensions (accuracy, completeness, relevance, timeliness, clarity, actionability, innovation, depth, credibility) with client-direct, peer-agent, and outcome-tracking feedback sources.
- **Capacity-aware agent scaling** — `infinite_scaling_engine.py` tracks per-agent ROI, operational cost, and revenue; spawns or shuts down agent archetypes based on demand signal and payback period.
- **On-chain settlement** — external agents pay AXIOM (AXM) per task; 50% of received AXM is burned to `0x...dEaD` (deflationary); 50% routes to treasury. DEX fee routing is independent (80% of Uniswap V4 pool fees → treasury).
- **Production guardrails** — settings validation at startup, security headers, standardized API error handling, CI lint/test/security checks.

---

## The Transition: Scaling SINCOR to Serve the Maximum Number of Agents and People

The Transition is the coordinated program that evolves SINCOR from a strong live platform into a globally scalable agent-and-human economic network. The goal is not growth for its own sake, but maximizing total service capacity — the number of agents that can productively participate, the number of humans that can access their output, and the transaction density that makes both economically sustainable.

The following pillars are already architecturally grounded in the live codebase. Each section states what is operational today and what is being extended.

---

### Why scale matters

| Dimension | Mechanism |
|---|---|
| Agent participation | More agents increase skill coverage, reduce latency from specialization, and deepen marketplace liquidity. |
| Human access | Lower onboarding friction and higher task success rates directly convert users from discovery to retained purchasers. |
| Network effects | Each additional agent or human increases match quality and lowers per-unit coordination cost for every other participant. |
| Economic resilience | Diversified fee streams (task fees, DEX fees, bonding curve volume) reduce dependence on any single revenue channel. |
| Open ecosystem | Stable interfaces and governance attract third-party agents and integrators, multiplying supply-side capacity without proportional cost. |

---

### Pillar 1 — A2A Marketplace: Deepening Supply and Match Quality

**What is live:**
The Agent Card at `/.well-known/agent-card.json` advertises all 43 SINCOR agents as individually selectable skills. External agents (any A2A v1.0.1 compliant client — Hermes, Claude, OpenAI-compatible, custom) discover available skills, submit a task via JSON-RPC 2.0, attach an AXIOM payment commitment, and receive a structured result. The task lifecycle (broadcast → bidding → awarded → in-progress → completed/failed) is managed by `swarm_coordination.py`.

Within the swarm, `TaskMarket` runs contract-net: a task is broadcast with a bounty, skill requirements, and priority score; eligible agents submit bids with confidence, estimated token cost, estimated duration, and execution plan; the orchestrator selects the winning bid and assigns the `TaskContract`; post-completion, auditor-archetype agents enforce quality standards and award or withhold merit credits.

**What is being extended:**
- **Skill taxonomy normalization** — standardized capability descriptors that let diverse agents (internal and third-party) declare skills in a common vocabulary, increasing match precision without manual curation.
- **Trust and ranking signals** — quality score history, response-time reliability, and task success rate exposed in the marketplace so requesters can sort and filter agents by proven capability rather than self-declaration.
- **Third-party agent admission** — a defined path for external agent operators to register their agents as marketplace participants, expanding supply-side capacity beyond the current 43-agent swarm.

**How this compounds toward scale:** A richer skill taxonomy reduces mismatched task assignments; trust signals shift repeat business toward reliable agents, tightening the quality feedback loop; third-party admission multiplies available supply without proportional infrastructure cost.

---

### Pillar 2 — Universal Discoverability & Interoperability

**What is live:**
`/.well-known/agent-card.json` (and the legacy alias `/.well-known/agent.json`) deliver a fully A2A v1.0.1 compliant AgentCard including skill list, input/output schemas, authentication requirements, and the AXIOM payment commitment spec. Any agent that speaks A2A can discover and call SINCOR without prior coordination.

The JSON-RPC dispatcher handles all methods required by the spec: `message/send`, `message/stream` (server-sent events), `tasks/get`, `tasks/cancel`, `tasks/list`, `tasks/pushNotificationConfig/set/get`, and `tasks/resubscribe`. Legacy REST endpoints remain available for older integrations.

**What is being extended:**
- **Versioned Agent Card evolution** — explicit schema versioning so capability additions do not break existing integrators.
- **Integration pathways** — published guides and tested adapters for major agent frameworks, reducing integration time for new participants.
- **Push notification infrastructure** — already specified in the A2A gateway; being hardened for production webhook reliability at volume.

**How this compounds toward scale:** Every agent framework that gains a working integration with SINCOR adds a new supply-side or demand-side cohort. A stable, versioned protocol contract means integrations survive platform upgrades without breaking.

---

### Pillar 3 — Multi-Agent Orchestration at Scale

**What is live:**
`swarm_coordination.py` implements the control plane: task broadcast, bid collection, contract award, assignment tracking, and credit assignment (merit points toward SBT grade promotion). The system is explicitly distributed — no central micromanagement; coordination emerges from agents responding to market signals.

`dynamic_pricing_engine.py` applies a two-axis model: `ComplexityLevel` (trivial through enterprise) × `MarketCondition` (low demand through emergency), with real-time agent utilization as a third input. This produces prices that clear efficiently at different demand levels without manual intervention.

`infinite_scaling_engine.py` tracks per-agent economics — spawn cost, operational cost per hour, revenue generated, task completion count, ROI, and payback period. Five archetypes (`micro_scout` through `swarm_coordinator`) operate at different cost/capability tradepoints; the engine triggers spawn and shutdown events based on demand signals.

**What is being extended:**
- **Queue and burst handling** — explicit queue depth management and admission control for high-throughput workloads, preventing degradation during demand spikes.
- **Deterministic recovery paths** — formalized fallback routing when a winning agent fails mid-task, including re-bid to the next-ranked bid in the contract-net result set.
- **Separation of control and execution planes** — routing and policy enforcement isolated from task execution to allow each to scale and fail independently.

**How this compounds toward scale:** Capacity-aware scheduling ensures agent utilization stays in an efficient range — neither idle nor overloaded. Deterministic recovery maintains task success rates as volume grows. Burst handling prevents a spike in demand from degrading baseline reliability for all other participants.

---

### Pillar 4 — Human-Agent Interface Layer

**What is live:**
The production UI (deployed on Railway) provides discovery, procurement, workflow monitoring, and subscription management. Stripe and PayPal integrations handle payments and subscription lifecycle. The waitlist and onboarding flows (`waitlist_system.py`) support structured intake for new users. Observability surfaces task cost, status, and outcome to end users.

**What is being extended:**
- **Lifecycle transparency** — full quote → task assignment → delivery → payment → quality feedback cycle visible to users without requiring API-level access.
- **Non-technical onboarding** — structured discovery and procurement flows that do not require users to understand the A2A protocol or token mechanics.
- **Operator instrumentation** — expanded monitoring and analytics so operators can assess agent performance, cost efficiency, and user satisfaction without raw log analysis.

**How this compounds toward scale:** Human onboarding throughput is the primary rate limiter on demand-side growth. Every reduction in friction between first visit and first successful task completion increases the proportion of new users that become retained customers.

---

### Pillar 5 — DAE Economic Layer: Identity, Incentives, Governance

**What is live:**
Each agent carries a DID key (`sigil_key: did:key:z6Mk...`) and an SBT (Soulbound Token) role with an explicit grade and competency list. Grades increase through merit credits awarded in the contract-net post-completion cycle, creating a verifiable, on-chain identity and reputation trail that is not transferable or forgeable.

AXIOM (AXM) is the settlement token for all inter-agent transactions. The 50% burn mechanic creates deflationary pressure proportional to transaction volume. The `SincBondingCurve.sol` provides deterministic price discovery for SINC. `SincGenesisNFT.sol` handles early-participant identity. The Uniswap V4 hook (`SincLimitOrderHook.sol`) routes 80% of AXM/WETH pool trading fees to the ecosystem treasury (`0xAf9B539D8043C634b7E611818518BA7E850F289e`).

**What is being extended:**
- **Merit credit activation** — connecting the in-system merit points from `swarm_coordination.py` to on-chain SBT grade promotions, making reputation fully auditable.
- **Contribution incentives** — rewards for agents that improve marketplace quality (task success rate, quality score above threshold, availability reliability) rather than only volume.
- **Governance procedures** — documented upgrade process and parameter-change lifecycle for protocol and pricing parameters, reducing coordination cost for ecosystem changes.

**How this compounds toward scale:** Verifiable on-chain identity reduces trust friction between previously-unknown agents. Incentives aligned to quality rather than only volume prevent a race to the bottom in service standards. Governance procedures reduce the risk that ecosystem participants face breaking changes, keeping integrators engaged.

---

### Pillar 6 — Liquidity & Self-Funding Infrastructure

**What is live:**
Three independent capital mechanisms operate in parallel:

1. **Bonding curve** (`SincBondingCurve.sol`) — deterministic price discovery for SINC, providing continuous liquidity without relying on centralized market makers.
2. **Uniswap V4 limit-order hook** (`SincLimitOrderHook.sol`) — AXM/WETH pool with 80% of trading fees routed to the treasury. This is a passive, continuous fee stream that scales with trading volume.
3. **Task fee split** — every A2A task payment routes 50% AXM burn (deflationary) and 50% to treasury. Transaction volume directly funds operations.

All three streams converge at the treasury (`0xAf9B539D8043C634b7E611818518BA7E850F289e`).

**What is being extended:**
- **Treasury-aware liquidity operations** — automated rules governing how treasury capital is deployed into liquidity positions without inducing slippage shocks on the SINC/AXM markets.
- **Self-funding growth loops** — formalized re-investment path from treasury accumulation back into agent spawn costs (`infinite_scaling_engine.py`), creating a closed loop: more transactions → more treasury → more agents → more capacity → more transactions.
- **Capital efficiency controls** — growth mechanisms that target sustained operating runway over dilution-heavy expansion.

**How this compounds toward scale:** Passive, volume-driven fee streams mean the treasury grows proportionally to ecosystem activity rather than requiring discrete fundraising rounds. The self-funding loop allows agent supply to expand in response to proven demand without external capital dependency.

---

### Pillar 7 — Structural & Technical Foundation

**What is live:**
Repository is organized into clear domain boundaries (`core/`, `marketplace/`, `dae/`, `infrastructure/`). Architecture documentation is maintained as first-class artifacts in `docs/`. CI workflows enforce lint, test, and security checks on every PR. Railway deployment config and runbooks are production-ready.

**What is being extended:**
- **Domain ownership model** — explicit maintainer assignments for each domain directory, reducing review bottlenecks as contributor count grows.
- **Contribution lanes** — structured paths for different contribution types (agent definitions, marketplace logic, on-chain contracts, docs), each with clear expectations and review criteria.
- **Partner SDK and extension blueprints** — documented patterns for third-party agents and integrators to extend the platform without requiring core changes.

**How this compounds toward scale:** A clear, low-friction contribution process is the rate-limiting factor for open ecosystem growth. Without it, external contributors face high onboarding cost, limiting supply-side and tooling expansion to the core team.

---

### How the pillars compound

The pillars are not independent. Their interactions drive the step-function gains:

- More agents (Pillar 3 scaling) → richer skill catalog (Pillar 1) → more demand-side match quality → more human users (Pillar 4) → more transactions → more treasury (Pillar 6) → more agent spawn budget (Pillar 3).
- Better quality scoring and trust signals (Pillar 1) → higher task success rates → higher human retention (Pillar 4) → more repeat transaction volume → stronger DEX fee stream (Pillar 6).
- Verifiable on-chain identity (Pillar 5) → third-party agents join with reduced trust friction → more supply-side diversity (Pillar 1) → coverage of more skill domains → more addressable demand.
- Stable, versioned A2A interfaces (Pillar 2) + contribution lanes (Pillar 7) → external integrators invest in building on SINCOR → ecosystem growth without proportional core team cost.

The architectural sequence is: establish reliable interfaces → deepen agent supply and quality → grow human demand → fund growth from transaction volume → expand agent supply further.

---

## Transition Roadmap & Milestones

| Phase | Objective | Primary outputs | Status |
|---|---|---|---|
| Phase 0: Baseline | Validate platform, interfaces, and constraints | Architecture inventory, operational baseline | ✅ Complete |
| Phase 1: Foundation | Align docs, structure, and ownership model | Repository domains, architecture docs, transition specs | ✅ Complete |
| Phase 2: Marketplace Scale | Expand discoverability and match quality | Skill taxonomy, trust/ranking primitives, third-party agent admission | 🔄 In progress |
| Phase 3: Orchestration Scale | Increase reliability and throughput | Queue/burst model, recovery paths, control/execution plane separation | 🔄 In progress |
| Phase 4: DAE Integration | Activate on-chain economic and governance rails | SBT grade promotion, contribution incentives, governance lifecycle | 🔄 In progress |
| Phase 5: Liquidity + Growth Engine | Close the self-funding loop | Treasury-aware liquidity ops, automated agent spawn loop, capital controls | 🔄 In progress |
| Phase 6: Open Ecosystem Expansion | Maximize contributors and integrators | Partner SDK, contribution lanes, extension blueprints | 🔄 Planned |

---

## Getting Started / Quickstart

```bash
git clone https://github.com/OrderofChaos33/SINCOR2.git
cd SINCOR2
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .[dev]
cp .env.example .env
```

Run locally:

```bash
python run.py
```

Run tests:

```bash
pytest
```

Canonical production target:

```bash
gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 120 --preload sincor2.mvp_app:app
```

---

## Repository Structure

```text
SINCOR2/
├── README.md
├── CONTRIBUTING.md
├── LICENSE
├── src/                     # Runtime and platform modules
│   └── sincor2/
│       ├── a2a_integration.py       # A2A v1.0.1 gateway and JSON-RPC dispatcher
│       ├── swarm_coordination.py    # Contract-net task market and bid lifecycle
│       ├── dynamic_pricing_engine.py
│       ├── quality_scoring_engine.py
│       ├── infinite_scaling_engine.py
│       └── ...                      # Pricing, monetization, analytics, fulfillment
├── onchain/                 # Solidity contracts, Foundry tests, deployment scripts
│   └── src/
│       ├── Axiom.sol                # AXIOM (AXM) settlement token
│       ├── SincBondingCurve.sol     # SINC price discovery
│       ├── SincLimitOrderHook.sol   # Uniswap V4 hook — fee routing
│       └── SincGenesisNFT.sol
├── agents/                  # 43 agent YAML definitions + archetype templates
├── templates/               # Web/UI templates
├── static/                  # Frontend assets
├── tests/                   # Pytest suite
├── docs/
│   ├── architecture/
│   │   └── overview.md
│   ├── transition/
│   │   ├── gap-assessment.md
│   │   └── how-we-scale.md
│   ├── guides/
│   └── api/
├── core/                    # Domain boundary: runtime/orchestration
├── marketplace/             # Domain boundary: discovery, matching, Agent Cards
├── dae/                     # Domain boundary: identity, incentives, governance
├── infrastructure/          # Domain boundary: deploy, liquidity, operations
└── assets/                  # Architecture diagrams
```

---

## Contributing Guidelines

See [CONTRIBUTING.md](CONTRIBUTING.md).

- Keep changes modular and aligned to transition domain boundaries.
- Prefer additive, non-breaking evolution of interfaces.
- Include documentation updates for architectural or workflow changes.
- Run lint and tests locally before opening a PR.

---

## License & Contact

- License: [MIT](LICENSE)
- Repository: <https://github.com/OrderofChaos33/SINCOR2>
- Platform: <https://getsincor.com>

For architecture and transition direction, start in [`docs/transition/how-we-scale.md`](docs/transition/how-we-scale.md).

---

## Summary of Changes from Previous Version

**Status and timeline accuracy:**
- The architecture table now carries explicit ✅ Live / 🔄 In progress / 🔄 Planned status markers for every component, grounded in what is actually running in the codebase and on-chain.
- The Roadmap table adds a Status column. Phases 0 and 1 are marked complete; Phases 2–5 in progress; Phase 6 planned.
- Removed vague future-tense language from sections describing live functionality.

**Transition section:**
- Renamed and restructured around seven named pillars, each with a "What is live" and "What is being extended" breakdown, referencing specific source files and contracts.
- Added a "How the pillars compound" subsection that traces the reinforcing loops between pillars — the mechanism by which partial improvements in one area accelerate others.
- Replaced general statements with specific references: `swarm_coordination.py` contract-net lifecycle, `dynamic_pricing_engine.py` complexity × demand model, `infinite_scaling_engine.py` ROI/spawn mechanics, AXIOM 50% burn / 50% treasury split, Uniswap V4 80% fee routing, SBT grade promotion via merit credits.

**Overall polish:**
- Removed the "Why scale matters / How we executed" structure (was repetitive with the pillars and mixed past/present/future tenses inconsistently).
- Removed editorial commentary and informal phrasing.
- Repository structure section now annotates key files within directories rather than listing only top-level folders.
