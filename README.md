# SINCOR2

[![Live Demo](https://img.shields.io/badge/Live%20Demo-getsincor.com-00C853)](https://getsincor.com)
[![Deployed on Railway](https://img.shields.io/badge/Deployed%20on-Railway-0A0A0A?logo=railway)](https://railway.app)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![Base](https://img.shields.io/badge/Chain-Base-0052FF?logo=coinbase&logoColor=white)](https://base.org)

**SINCOR2** is a production-grade autonomous AI workforce platform. It deploys a coordinated swarm of 43 specialized AI agents that function as a complete business operations team — handling market intelligence, competitive analysis, outbound sales, content creation, contract negotiation, quality assurance, and more.

Built as a real SaaS product, SINCOR2 is live, processes real Stripe payments, and is deployed on Railway. Clients subscribe to access the full agent swarm on demand.

---

## Ecosystem Tokens (Base Mainnet)

The SINCOR platform is backed by two tokens on the **Base** blockchain (chainId 8453). All contracts are verified on Basescan with no mint function, no owner, no proxy.

### SINC — Platform Utility Token

| Property | Value |
|----------|-------|
| Contract | [`0x9C8cd8d3961F445D653713dE65C6578bE11668e7`](https://basescan.org/token/0x9C8cd8d3961F445D653713dE65C6578bE11668e7) |
| Symbol / Decimals | SINC / 8 |
| Total Supply | 100,000,000 |
| CertiK Score | 97 / 100 |
| Bonding Curve | [`0x75dE341a2BC81806198364F125d4Cde36527619C`](https://basescan.org/address/0x75dE341a2BC81806198364F125d4Cde36527619C) |
| Gateway page | [getsincor.com/sinc](https://getsincor.com/sinc) |

SINC is the native utility token of the SINCOR platform. Customers pay in SINC for agent work; 50 % of each payment is burned on-chain and 50 % goes to the treasury. The token launches via a constant-product bonding curve (Phase 1), then graduates to a Uniswap V4 pool with the LP permanently burned.

### AXIOM (AXM) — Autonomous Intelligence Token

| Property | Value |
|----------|-------|
| Contract | [`0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822`](https://basescan.org/token/0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822) |
| Symbol / Decimals | AXM / 18 |
| Total Supply | 1,000,000,000 |
| Chain | Base (Uniswap V4) |
| Token page | [getsincor.com/axiom](https://getsincor.com/axiom) |

AXIOM is the **oil in the engine** for agent-to-agent (A2A) interactions. Any external compliant agent — Hermes, Claude, OpenAI-compatible, or any JSON-RPC A2A agent — acquires AXM to invoke SINCOR agents and exchange intelligence. 80 % of AXM trading fees are publicly routed back to the ecosystem treasury (auditable on Basescan).

---

## Smart Contracts

All Solidity source lives in [`onchain/`](onchain/) (Foundry project, Base mainnet).

| Contract | File | Description |
|----------|------|-------------|
| SINC Token | [`SINC_v3.sol`](SINC_v3.sol) | ERC-20, 100 M supply, 8 decimals |
| AXIOM Token | [`onchain/src/Axiom.sol`](onchain/src/Axiom.sol) | ERC-20, 1 B supply, 18 decimals |
| SincBondingCurve | [`onchain/src/SincBondingCurve.sol`](onchain/src/SincBondingCurve.sol) | Phase 1 curve: referral, Genesis NFT auto-mint, graduation → Uniswap V4 |
| SincGenesisNFT | [`onchain/src/SincGenesisNFT.sol`](onchain/src/SincGenesisNFT.sol) | Soulbound ERC-721 for Phase 1 buyers |
| SincLimitOrderHook | [`onchain/src/SincLimitOrderHook.sol`](onchain/src/SincLimitOrderHook.sol) | Uniswap V4 hook: limit orders + anti-sandwich dynamic fee |

ABI files (used by the Flask backend and frontend):

| File | Token |
|------|-------|
| [`SINCBondingCurve_abi.json`](SINCBondingCurve_abi.json) | SINC bonding curve |
| [`Axiom_abi.json`](Axiom_abi.json) | AXIOM (AXM) token |

See [`onchain/README.md`](onchain/README.md) for full contract architecture, supply allocation, and deployment instructions.

---

## Agent-to-Agent (A2A) Integration

SINCOR implements the [A2A protocol v1.0.1](https://a2aproject.github.io/A2A) — the emerging standard for machine-to-machine agent interoperability. Any external AI agent that speaks JSON-RPC 2.0 can discover, call, and collaborate with the SINCOR swarm.

**AXIOM is the settlement layer**: external agents pay in AXM for SINCOR agent work; SINCOR burns 50 % on-chain as each payment arrives, making supply deflationary as usage grows.

### Quick start for external agents

**1. Discover the AgentCard**

```
GET https://getsincor.com/.well-known/agent.json
```

Returns capabilities, available skills, AXM payment instructions, and API-key auth scheme.

**2. Get a payment quote**

```http
POST https://getsincor.com/api/a2a/quote
Content-Type: application/json

{ "skill_id": "market-intelligence" }
```

**3. Send AXM on Base, then submit the task**

```http
POST https://getsincor.com/api/a2a/tasks/send
X-API-Key: <your-key>
Content-Type: application/json

{
  "skillId":  "market-intelligence",
  "txHash":   "0x...",
  "callerId": "my-agent@example.com",
  "message": {
    "parts": [{ "type": "text", "text": "Analyse the DeFi yield aggregator landscape on Base, Q2 2026." }]
  }
}
```

### A2A endpoint reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`  | `/.well-known/agent.json` | AgentCard — agent discovery |
| `POST` | `/api/a2a/tasks/send` | Submit a task (JSON-RPC 2.0) |
| `GET`  | `/api/a2a/tasks/{id}` | Poll task status |
| `POST` | `/api/a2a/tasks/cancel` | Cancel a pending task |
| `GET`  | `/api/a2a/agents` | List all skills with AXM pricing |
| `POST` | `/api/a2a/quote` | Get AXM price quote for a skill |

Source: [`src/sincor2/a2a_integration.py`](src/sincor2/a2a_integration.py)

### Compatible agent frameworks

SINCOR's A2A layer is compatible with any agent that implements the A2A JSON-RPC 2.0 protocol:

- **Hermes / Mercury** (Nous Research) — model-level agents
- **Claude** (Anthropic) — via Claude tool-use + A2A adapter
- **OpenAI-compatible agents** (GPT-4o, OpenClaw, custom) — via A2A wrapper
- **LangChain / LangGraph agents** — A2A tool plugin
- **Any custom agent** that can issue HTTP POST with a JSON-RPC 2.0 body

---

## Overview

Instead of a single general-purpose AI, SINCOR2 provides a full team of purpose-built agents with distinct personalities, skill specializations, memory systems, and career progression. These agents self-coordinate through a decentralized task market, bid on work, and continuously improve through feedback.

**Key Capabilities**
- Rapid market & competitive intelligence
- Scalable outbound prospecting and lead enrichment
- Automated contract negotiation support
- High-quality content and deliverable generation
- Predictive analytics and scenario planning
- Self-improving quality control system
- Dynamic pricing based on complexity and urgency

---

## Architecture Highlights

- **Core Intelligence Layer**: Powered by Claude (Anthropic) as the central reasoning engine
- **Agent System**: 43 specialized agents built on 7 archetypes (Scout, Builder, Synthesizer, Negotiator, Director, Auditor, Caretaker)
- **Swarm Coordination**: Contract-net style task market for autonomous work distribution
- **Memory Architecture**: Multi-tier (episodic, semantic, procedural, autobiographical) with hybrid retrieval
- **Business Engine**: Dynamic pricing, monetization orchestrator, recursive value products, and infinite scaling logic
- **Production Infrastructure**: Flask + Gunicorn, JWT auth, rate limiting, Pydantic validation, structured logging, Stripe + PayPal payments
- **On-chain Layer**: SINC + AXIOM tokens on Base; bonding curve, Uniswap V4 hook, Genesis NFT
- **A2A Protocol**: Google A2A-compliant — any external agent can call SINCOR via JSON-RPC 2.0 + AXIOM payment
- **SINAX Engine**: Proof Topology Navigator — 4-layer topological reasoning system for autonomous proof search and knowledge synthesis

---

## The Agent System

Each agent has:
- A unique star-based name (E-Auriga-01 through E-Mesarthim-43)
- Defined personality vectors (Big-Five/OCEAN model + custom style traits)
- Individual token budgets, tool access, and performance history
- A structured lifecycle (Hatch → Onboard → Active → Review → Promote/Retire)

Full agent definitions and archetypes are available in the [`agents/`](agents/) directory.

---

## Core Features

- **Instant Business Intelligence** – On-demand professional-grade reports and analysis
- **Dynamic Pricing Engine** – Real-time pricing adjusted by complexity, urgency, and demand
- **Predictive Analytics** – Forward-looking forecasts with confidence scoring
- **Quality Scoring System** – Multi-source, self-improving evaluation of all outputs
- **Stripe & PayPal Payments** – Live payment processing with Customer Portal and webhook handling
- **AXIOM A2A Gateway** – Any compliant external agent can invoke the swarm; pays in AXM, receives intelligence in return

---

## SINAX — Proof Topology Navigator

SINAX is the formal reasoning engine embedded in SINCOR2. It replaces linear tactic sequences with a four-layer topological pipeline for autonomous proof search and knowledge synthesis.

| Layer | Component | Description |
|-------|-----------|-------------|
| 1 | `ProofManifold` | Embeds proof states onto a Riemannian manifold |
| 2 | `GeodesicFlowEngine` | Computes geodesic paths between proof states |
| 3 | `HomologyDetector` | Identifies topological holes and suggests bridging lemmas |
| 4 | `MorseFilter` | Retains only critical-point waypoints via Morse theory |

The top-level `ProofTopologyNavigator` (PTN) wraps all four layers behind a single callable:

```python
from sincor2.sinax import ProofTopologyNavigator

ptn = ProofTopologyNavigator()
result = ptn.solve(
    start_state="⊢ ∀ n : ℕ, n + 0 = n",
    target_state="closed",
)
print(result.proof_narrative)
print(result.tactic_sequence)
```

SINAX also exposes `solve_batch()` for parallel proof transfer across related theorems and a `training_signal()` method to feed curvature/homology data back into the Verified Data Flywheel.

Source: [`src/sincor2/sinax/`](src/sincor2/sinax/)

---

## Security & Best Practices

- No secrets or credentials are stored in the repository
- All sensitive values are loaded from environment variables
- Comprehensive input validation, rate limiting, and security headers
- JWT authentication for protected routes
- Production logging and monitoring ready

---

## Quick Start

```bash
git clone https://github.com/OrderofChaos33/SINCOR2.git
cd SINCOR2
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your API keys (Anthropic, Stripe, BASE_RPC_URL, etc.)
```

Then run locally:
```bash
python run.py
```

Full deployment instructions are in [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md).
Smart contract build/test/deploy: see [`onchain/README.md`](onchain/README.md).

---

## Project Status

SINCOR2 is **live and production-ready**. The platform is actively processing real customer subscriptions.

**Repository cleaned** for public viewing — internal development notes and duplicate files have been removed.

---

*Built by [OrderofChaos33](https://github.com/OrderofChaos33)*
