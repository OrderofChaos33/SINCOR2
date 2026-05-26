# SINCOR2

[![Live Demo](https://img.shields.io/badge/Live%20Demo-getsincor.com-00C853)](https://getsincor.com)
[![Deployed on Railway](https://img.shields.io/badge/Deployed%20on-Railway-0A0A0A?logo=railway)](https://railway.app)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)

**SINCOR2** is a production-grade autonomous AI workforce platform. It deploys a coordinated swarm of 43 specialized AI agents that function as a complete business operations team — handling market intelligence, competitive analysis, outbound sales, content creation, contract negotiation, quality assurance, and more.

Built as a real SaaS product, SINCOR2 is live, processes real Stripe payments, and is deployed on Railway. Clients subscribe to access the full agent swarm on demand.

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
- **Production Infrastructure**: Flask + Gunicorn, JWT auth, rate limiting, Pydantic validation, structured logging, and full Stripe integration

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
- **Stripe Subscriptions** – Live payments with Customer Portal and webhook handling

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
# Edit .env with your API keys (Anthropic, Stripe, etc.)
```

Then run locally:
```bash
python run.py
```

Full deployment instructions are in [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md).

---

## Project Status

SINCOR2 is **live and production-ready**. The platform is actively processing real customer subscriptions.

**Repository cleaned** for public viewing — internal development notes and duplicate files have been removed.

---

*Built by [OrderofChaos33](https://github.com/OrderofChaos33)*