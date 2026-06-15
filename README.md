# SINCOR2

**SINCOR2** ([getsincor.com](https://getsincor.com)) is a production autonomous workforce platform: 42+ specialized AI agents coordinated through a decentralized task market, with wallet-native billing on Base (**SINC** subscriptions, **AXM** execution). Live on Railway with Gunicorn, health checks, and verifiable on-chain contracts.

### Documentation (public)

| Resource | URL |
|----------|-----|
| **Whitepaper** | [getsincor.com/whitepaper](https://getsincor.com/whitepaper) |
| **Pitch deck** (interactive) | [getsincor.com/pitch](https://getsincor.com/pitch) |
| **Markdown source** | [`static/docs/SINCOR_whitepaper.md`](static/docs/SINCOR_whitepaper.md) |
| **NotebookLM bundle** | [`content/notebooklm/SINCOR_pitch_source.md`](content/notebooklm/SINCOR_pitch_source.md) — import with the whitepaper into [NotebookLM](https://notebooklm.google.com) for audio overviews |
| **Canonical addresses** | [`CANONICAL_ADDRESSES.md`](CANONICAL_ADDRESSES.md) |

Default billing: **SINC + AXM on Base** (`LEGACY_FIAT_PAYMENTS_ENABLED=false`). Stripe/PayPal are legacy-only.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture at a Glance](#architecture-at-a-glance)
3. [The Agent System](#the-agent-system)
   - [Archetypes](#archetypes)
   - [The 43 Agents](#the-43-agents)
   - [Agent Lifecycle](#agent-lifecycle)
   - [Persona & Personality Engine](#persona--personality-engine)
4. [Core Intelligence Layer](#core-intelligence-layer)
   - [Cor-tecs Brain (Claude Integration)](#cor-tecs-brain-claude-integration)
   - [Agency Kernel (Planner / Executor / Critic / Archivist)](#agency-kernel-planner--executor--critic--archivist)
   - [Swarm Coordination (Task Market)](#swarm-coordination-task-market)
   - [Memory Architecture](#memory-architecture)
5. [Business Engine](#business-engine)
   - [Instant Business Intelligence](#instant-business-intelligence)
   - [Dynamic Pricing Engine](#dynamic-pricing-engine)
   - [Real-Time Intelligence Feeds](#real-time-intelligence-feeds)
   - [Predictive Analytics Engine](#predictive-analytics-engine)
   - [Recursive Value Products](#recursive-value-products)
   - [Quality Scoring System](#quality-scoring-system)
   - [Partnership Framework](#partnership-framework)
   - [Infinite Scaling Engine](#infinite-scaling-engine)
   - [Monetization Engine (Orchestrator)](#monetization-engine-orchestrator)
6. [Payment & Commerce](#payment--commerce)
   - [Stripe Integration](#stripe-integration)
7. [Security & Infrastructure](#security--infrastructure)
   - [Authentication (JWT)](#authentication-jwt)
   - [Rate Limiting](#rate-limiting)
   - [Input Validation](#input-validation)
   - [Security Headers](#security-headers)
   - [Production Logging](#production-logging)
8. [Web Application](#web-application)
   - [Routes & Pages](#routes--pages)
   - [Templates](#templates)
9. [Deployment](#deployment)
10. [Environment Variables](#environment-variables)
11. [Development Setup](#development-setup)
12. [Project Structure](#project-structure)

---

## Overview

SINCOR2 is not a chatbot wrapper. It is a purpose-built autonomous workforce platform. The core idea is straightforward: instead of one general-purpose AI assistant, you get a team of 43 specialized agents that each have their own personality vectors, daily token budgets, memory stores, and career trajectories. They coordinate with each other through a decentralized task market, bid on work they are qualified for, and earn merit points toward promotion.

On the customer-facing side, it looks like a SaaS platform with tiered subscriptions (Starter, Professional, Enterprise). Under the hood, it is a fully realized multi-agent OS.

**Key capabilities:**
- Market and competitive intelligence in under an hour
- Outbound prospecting and lead enrichment at scale  
- Contract-net style task auctions with automatic agent-skill matching
- Dynamic pricing that adjusts based on complexity and urgency
- Predictive analytics with confidence intervals
- Self-improving quality scoring that learns from client feedback
- SINC/AXM wallet checkout with on-chain treasury verification

---

## Architecture at a Glance

```
getsincor.com (Railway + Gunicorn + Flask)
│
├── sincor_stripe_app.py          ← Production entrypoint (Stripe-first)
├── run.py                        ← WSGI bootstrap
│
├── src/sincor2/                  ← Core application package
│   ├── Intelligence Layer
│   │   ├── cortecs_core.py       ← Claude brain + nested learning
│   │   ├── agency_kernel.py      ← Planner/Executor/Critic/Archivist
│   │   ├── swarm_coordination.py ← Distributed task market
│   │   └── memory_system.py      ← 4-tier memory (episodic/semantic/procedural/auto)
│   │
│   ├── Business Engine
│   │   ├── monetization_engine.py          ← Master revenue orchestrator
│   │   ├── instant_business_intelligence.py← Rapid BI deliverables
│   │   ├── dynamic_pricing_engine.py       ← Complexity-aware pricing
│   │   ├── real_time_intelligence.py       ← Live data feeds
│   │   ├── predictive_analytics_engine.py  ← Forecasting & scenarios
│   │   ├── recursive_value_products.py     ← Self-funding product loop
│   │   ├── quality_scoring_engine.py       ← Self-improving QA
│   │   ├── partnership_framework.py        ← Long-term partner management
│   │   └── infinite_scaling_engine.py      ← Agent ROI & auto-spawning
│   │
│   ├── Agent System
│   │   ├── persona_engine.py       ← OCEAN personality vectors
│   │   ├── lifecycle_system.py     ← Hatch→Onboard→Shift→Off-duty→Promote
│   │   └── agency_kernel.py        ← Per-agent reasoning loop
│   │
│   └── Infrastructure
│       ├── auth_system.py          ← JWT authentication
│       ├── rate_limiter.py         ← DDoS & brute-force protection
│       ├── validation_models.py    ← Pydantic input validation
│       ├── security_headers.py     ← HTTP security headers
│       ├── production_logger.py    ← Structured logging
│       ├── stripe_checkout.py      ← Stripe session management
│       ├── stripe_routes.py        ← Payment API routes
│       ├── waitlist_system.py      ← Lead capture
│       └── email_sender.py         ← Transactional email (SendGrid)
│
└── agents/                       ← 43 agent definitions (YAML)
    ├── E-auriga-01.yaml through E-mesarthim-43.yaml
    └── archetypes/               ← 7 archetype templates
```

---

## The Agent System

### Archetypes

Every agent is built on one of seven archetypal templates that define default personality traits, communication style, and core competencies. The archetype is a genetic starting point — each agent then diverges from it through interaction feedback over time.

| Archetype | Primary Drive | Core Competencies |
|-----------|--------------|-------------------|
| **Scout** | Discovery & Intelligence | Prospecting, web scraping, market monitoring, research, data enrichment |
| **Builder** | Creation & Execution | System architecture, code generation, content production, tool development |
| **Synthesizer** | Analysis & Insight | Pattern recognition, cross-source synthesis, strategic framing, reporting |
| **Negotiator** | Influence & Closure | Outreach, objection handling, deal structuring, relationship management |
| **Director** | Leadership & Coordination | Goal setting, resource allocation, team coordination, decision making |
| **Auditor** | Quality & Compliance | Validation, fact-checking, standards enforcement, risk flagging |
| **Caretaker** | Support & Continuity | Onboarding, knowledge transfer, system health, retention workflows |

Each archetype YAML (`agents/archetypes/`) defines OCEAN trait defaults, style preferences (risk tolerance, humor, directness), communication modality weights (code vs. tables vs. narrative), and the SBT (Soulbound Token) role configuration that governs promotions.

### The 43 Agents

All agents follow the naming convention `E-{starname}-{number}`, where the star name is the agent's given name and the number reflects its place in the founding cohort. Agent 43 (Mesarthim) is the final member of the current roster.

| ID | Name | Archetype | Secondary |
|----|------|-----------|-----------|
| E-auriga-01 | Auriga | Scout | Synthesizer |
| E-vega-02 | Vega | Scout | Negotiator |
| E-rigel-03 | Rigel | Scout | Builder |
| E-altair-04 | Altair | Scout | — |
| E-spica-05 | Spica | Scout | — |
| E-deneb-06 | Deneb | Scout | — |
| E-capella-07 | Capella | Scout | — |
| E-sirius-08 | Sirius | Scout | Negotiator |
| E-polaris-09 | Polaris | Synthesizer | Director |
| E-arcturus-10 | Arcturus | Synthesizer | — |
| E-betelgeuse-11 | Betelgeuse | Synthesizer | Builder |
| E-aldebaran-12 | Aldebaran | Synthesizer | — |
| E-antares-13 | Antares | Synthesizer | Caretaker |
| E-procyon-14 | Procyon | Synthesizer | — |
| E-canopus-15 | Canopus | Builder | — |
| E-achernar-16 | Achernar | Builder | — |
| E-bellatrix-17 | Bellatrix | Builder | — |
| E-castor-18 | Castor | Builder | — |
| E-pollux-19 | Pollux | Builder | — |
| E-regulus-20 | Regulus | Builder | — |
| E-mizar-21 | Mizar | Builder | — |
| E-fomalhaut-22 | Fomalhaut | Negotiator | — |
| E-acrux-23 | Acrux | Negotiator | — |
| E-mimosa-24 | Mimosa | Negotiator | — |
| E-gacrux-25 | Gacrux | Negotiator | — |
| E-shaula-26 | Shaula | Negotiator | — |
| E-kaus-27 | Kaus | Negotiator | — |
| E-alkaid-28 | Alkaid | Caretaker | — |
| E-dubhe-29 | Dubhe | Caretaker | — |
| E-merak-30 | Merak | Caretaker | — |
| E-phecda-31 | Phecda | Caretaker | — |
| E-megrez-32 | Megrez | Caretaker | — |
| E-alioth-33 | Alioth | Auditor | — |
| E-meback-34 | Meback | Auditor | — |
| E-benetnash-35 | Benetnash | Auditor | — |
| E-cor-caroli-36 | Cor Caroli | Auditor | — |
| E-alphard-37 | Alphard | Director | — |
| E-alpheratz-38 | Alpheratz | Director | — |
| E-mirach-39 | Mirach | Director | — |
| E-almaak-40 | Almaak | Director | — |
| E-hamal-41 | Hamal | Director | — |
| E-sheratan-42 | Sheratan | Director | — |
| E-mesarthim-43 | Mesarthim | Director | — |

### Agent Lifecycle

Agents move through structured lifecycle states managed by `lifecycle_system.py`. The system enforces mandatory rest periods to prevent mode collapse and cognitive drift — a design decision taken seriously from day one.

```
Hatch → Onboard → Shift ⟷ Off-duty → Review → Promote / Clone / Retire
                              │
                         Dream Mode  (memory consolidation, 20-min sessions)
                         Play Mode   (creative exploration, 15-min sessions)
```

- **Hatch** — Agent is initialized with its archetype defaults and constitutional references
- **Onboard** — Supervised warm-up period; learns system conventions and tool registry
- **Shift** — Active work mode with daily token/tool-call budgets enforced
- **Off-duty** — Mandatory rest; Dream mode consolidates episodic memory, Play mode surfaces novel connections
- **Review** — Performance evaluation against quality scores and peer feedback
- **Promote** — SBT grade advances, expanding competency access and budget allocation

Shift budgets are set per-agent in the YAML: typically 12,000 tokens/day and 200 tool calls. These are hard caps, not soft suggestions.

### Persona & Personality Engine

`persona_engine.py` implements Big-Five (OCEAN) personality vectors for every agent. Traits are continuous floats between 0.0 and 1.0. Interaction feedback loops — labeled by outcome quality, novelty, and client satisfaction — gradually sculpt the persona away from archetype defaults and toward what actually works for that agent in that role.

Three additional dimensions sit alongside the Big-Five:
- **Style**: risk tolerance, humor level, directness
- **Modality**: preference weighting between code output, structured tables, and narrative prose
- **Continuity Index**: drift detection to flag if an agent is moving too far from its constitutional identity

---

## Core Intelligence Layer

### Cor-tecs Brain (Claude Integration)

`src/sincor2/cortecs_core.py` is the central reasoning engine. It wraps Anthropic's Claude API (claude-sonnet-4-5) in both sync and async modes and exposes it as a shared brain that agents call into for complex reasoning, cross-agent synthesis, and nested learning tasks.

Key classes:
- **`ClaudeClient`** — Thin wrapper around `anthropic.Anthropic` / `AsyncAnthropic`; handles auth, model selection, and error normalization
- **`CortecsBrain`** — Manages agent registration, coordinates multi-agent tasks, runs nested learning loops where outputs from one agent become structured inputs for the next
- **`NestedLearningTask`** — Dataclass representing a multi-step reasoning chain with intermediate checkpoints and confidence scores

The brain does not centrally micromanage. It orchestrates when agents need collective synthesis; otherwise agents reason locally through their own Agency Kernel.

### Agency Kernel (Planner / Executor / Critic / Archivist)

`src/sincor2/agency_kernel.py` implements the four-role reasoning loop that every agent runs internally:

1. **Planner** — Decomposes a goal into ordered `PlanStep` objects with expected inputs/outputs
2. **Executor** — Carries out each step via tool calls or reasoning; tracks actual vs. expected results
3. **Critic** — Validates output quality; builds evidence→claim→confidence chains; flags drift or hallucination risk
4. **Archivist** — Writes finalized knowledge to the memory system; manages what gets promoted from episodic to semantic

This pattern ensures agents do not just answer — they verify and document their own reasoning.

### Swarm Coordination (Task Market)

`src/sincor2/swarm_coordination.py` implements a contract-net style task market for distributed work assignment. There is no central scheduler.

How it works:
1. A task is **broadcast** to the `TaskMarket` with a bounty (merit points), required skills, priority, and deadline
2. Qualified agents **bid** with an intent statement, execution plan, cost estimate, and confidence score
3. The market evaluates bids on skill match, historical success rate, current load, and bid quality
4. The winning agent is **awarded** the contract; its `TaskContract` is committed and work begins
5. On completion, the Auditor archetype validates the result before merit is credited

Status lifecycle: `BROADCAST → BIDDING → AWARDED → IN_PROGRESS → COMPLETED` (or `FAILED` / `CANCELLED`)

### Memory Architecture

`src/sincor2/memory_system.py` implements a four-tier memory system with hybrid retrieval:

| Tier | Type | Description |
|------|------|-------------|
| **Episodic** | Append-only event log | Time-stamped actions, observations, interactions with SHA-256 content hashing |
| **Semantic** | Subject-predicate-object graph | Structured facts with confidence scores and source citations |
| **Procedural** | Versioned registry | Tool definitions, prompt templates, workflow blueprints |
| **Autobiographical** | Curated narrative | Agent self-story, goals, quirks, constitutional identity |

Retrieval blends vector similarity, graph traversal, and key-value cache lookup. Episodic events older than the configured `episodic_days` window are compressed into semantic facts during Dream mode.

---

## Business Engine

### Instant Business Intelligence

`src/sincor2/instant_business_intelligence.py` is the primary revenue-generating product. It accepts a `BusinessIntelligenceRequest` (deliverable type, urgency, budget, specific questions) and orchestrates the swarm to deliver within a defined SLA.

Deliverable types:
- Market Analysis, Competitor Intelligence, Revenue Optimization
- Customer Insights, Growth Strategy, Risk Assessment, Investment Recommendation

Urgency tiers affect pricing and agent allocation:
- **Standard** — 4–6 hours, base price
- **Priority** — 2–4 hours, 2× multiplier
- **Emergency** — 30 min–2 hours, 5× multiplier

### Dynamic Pricing Engine

`src/sincor2/dynamic_pricing_engine.py` calculates real-time prices based on six factors:
- Task complexity (1–6 scale: Trivial → Enterprise)
- Market demand conditions (LOW_DEMAND at 0.5× to EMERGENCY at 3.0×)
- Agent utilization and availability
- Client tier (affects baseline and loyalty discounts)
- Historical success rate for similar task types
- Time-to-deadline urgency

Pricing results include base price, market multiplier, final price, and a human-readable justification string. Results are cached with `lru_cache` to prevent re-computation on identical task profiles.

### Real-Time Intelligence Feeds

`src/sincor2/real_time_intelligence.py` gives agents access to live data streams including financial markets, news feeds, social media sentiment, competitor website changes, job postings, patent filings, and pricing intelligence. Alerts are classified by severity (LOW / MEDIUM / HIGH / CRITICAL) and routed to relevant agents based on skill match.

### Predictive Analytics Engine

`src/sincor2/predictive_analytics_engine.py` goes beyond reactive reporting into forward-looking forecasts:
- **Market Trend** — Directional probability with confidence intervals
- **Competitor Move** — Anticipated strategic actions based on signal patterns
- **Revenue Impact** — Projected financial effect of strategic decisions
- **Risk Probability** — Likelihood and severity scoring for identified risks
- **Opportunity Window** — Time-bounded probability windows for market entry
- **Demand Forecast** — Volume projections with scenario branching

Each prediction carries a `PredictionType`, a time horizon (SHORT / MEDIUM / LONG), a confidence score, and supporting evidence citations.

### Recursive Value Products

`src/sincor2/recursive_value_products.py` implements the self-funding product logic. Each `ValueProduct` tracks creation cost, base price, and recurring revenue. Revenue from sold products is automatically allocated toward spawning new agent capacity and creating new product variants — a recursive loop where the platform funds its own expansion.

Revenue models supported: one-time, subscription, usage-based, revenue share, and hybrid.

### Quality Scoring System

`src/sincor2/quality_scoring_engine.py` is a self-improving QA engine that scores deliverables across nine dimensions:

Accuracy, Completeness, Relevance, Timeliness, Clarity, Actionability, Innovation, Depth, Credibility

Scores come from three sources: automated validation checks, peer agent review, and client satisfaction feedback. The system tracks score history per agent and per task type, adjusting quality thresholds over time as the platform learns what good looks like for each client segment.

### Partnership Framework

`src/sincor2/partnership_framework.py` manages long-term business relationships across partnership types (technology integration, data partnership, revenue sharing, strategic alliance, joint venture, reseller, development, market expansion) and tiers (Startup → Growth → Enterprise → Fortune 500 → Global Leader). Partner health is scored continuously and escalation paths are triggered automatically.

### Infinite Scaling Engine

`src/sincor2/infinite_scaling_engine.py` tracks economic metrics per agent: spawn cost, operational cost per hour, revenue generated, tasks completed, and ROI. When an agent's economics reach target thresholds, the engine recommends spawning a new sibling agent in the same archetype. The design intent is that the fleet grows only when it can fund itself.

### Monetization Engine (Orchestrator)

`src/sincor2/monetization_engine.py` is the top-level orchestrator that ties everything together. It manages `RevenueOpportunity` objects across eight revenue streams:

| Stream | Description |
|--------|-------------|
| Instant BI | On-demand business intelligence deliverables |
| Agent Services | Subscription access to the swarm |
| Predictive Analytics | Forward-looking forecasts and scenario planning |
| Partnerships | Long-term strategic partnership agreements |
| Recursive Products | Self-funding product bundles |
| Consulting | High-touch enterprise engagements |
| Subscriptions | Monthly/annual SaaS tiers |
| Licensing | White-label and API access |

Five monetization strategies can be toggled: Aggressive Growth, Premium Positioning, Market Penetration, Value Maximization, Partnership Leverage.

---

## Payment & Commerce

### Stripe Integration

Payment processing runs through Stripe. The live price IDs for the three subscription tiers live in `src/sincor2/stripe_routes.py`.

| Plan | Stripe Price ID |
|------|----------------|
| Starter | `price_1T84ngDuhR2MxqDMrEdObjnD` |
| Professional | `price_1T84oyDuhR2MxqDMPmMKJkfQ` |
| Enterprise | `price_1T84qHDuhR2MxqDMG1LNnMbm` |

Key components:
- **`StripeCheckout`** (`stripe_checkout.py`) — Creates Checkout Sessions, supports both Price ID lookups and inline price_data; handles subscription and one-time modes
- **`stripe_routes.py`** — Blueprint with `/api/stripe/` endpoints for session creation, webhook processing (`stripe-signature` verified), and Customer Portal sessions
- **Abandoned checkout recovery** — SQLite table logs incomplete sessions with customer email for follow-up
- **Customer Portal** — `/billing?customer_id=` redirects authenticated customers to Stripe's self-service portal

Webhook events handled: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_succeeded`, `invoice.payment_failed`.

---

## Security & Infrastructure

### Authentication (JWT)

`src/sincor2/auth_system.py` wraps `flask-jwt-extended` to protect admin and payment endpoints. Access tokens expire in 1 hour; refresh tokens last 30 days. The `admin_required` decorator checks both JWT validity and user role. All error responses are JSON-normalized.

Admin credentials are environment-variable driven — the default dev key is intentionally obvious so it cannot be accidentally left in production.

### Rate Limiting

`src/sincor2/rate_limiter.py` uses `flask-limiter` with fixed-window strategy. Default: 1,000 requests/day, 200/hour per IP. Endpoint-specific limits:

| Category | Limit |
|----------|-------|
| Auth endpoints | Strict (brute-force protection) |
| Payment endpoints | Conservative |
| Public endpoints | Permissive |
| Admin endpoints | Strict + IP allowlist |
| Monitoring | Moderate |

Redis is supported for production clustering; falls back to in-memory storage gracefully.

### Input Validation

`src/sincor2/validation_models.py` uses Pydantic v2 models for all inbound API data. Email addresses are validated with `email-validator`. Payment amounts, plan names, and user inputs are type-checked and range-validated before any business logic runs.

### Security Headers

`src/sincor2/security_headers.py` injects standard HTTP security headers on every response: `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Strict-Transport-Security`, and `Referrer-Policy`.

### Production Logging

`src/sincor2/production_logger.py` provides structured logging with separate streams for application events, security events (login success/fail, rate limit hits, suspicious patterns), and performance metrics. Log files are written to `logs/` (gitignored) and are not bundled into the repository.

---

## Web Application

### Routes & Pages

The live application (`sincor_stripe_app.py`) serves these routes:

| Route | Description |
|-------|-------------|
| `/` | Home page — main marketing and product showcase |
| `/buy` | Conversion-optimized checkout page |
| `/pricing` | Tiered pricing comparison |
| `/payment/success` | Post-checkout confirmation with session details |
| `/payment/cancel` | Abandoned checkout recovery page |
| `/billing` | Redirect to Stripe Customer Portal |
| `/health` | Health check endpoint for Railway monitoring |
| `/api/stripe/*` | Stripe session creation, webhooks, portal |

### Templates

Jinja2 templates live in `templates/`. Key templates include:
- `home.html` / `home_mvp.html` — Marketing homepage with agent showcase
- `buy_converting.html` / `buy_stripe.html` — Checkout pages
- `pricing.html` — Plan comparison grid
- `payment_success.html` / `payment_cancel.html` — Post-payment flows
- `executive_dashboard.html` — Internal performance dashboard
- `admin_panel.html` / `admin_dashboard.html` — Admin interfaces
- `enterprise-dashboard.html` / `professional_dashboard.html` — Tier-specific dashboards
- `login.html` — Authentication page
- `security.html` / `privacy.html` / `terms.html` — Legal pages
- `whitepaper.html` — Technical whitepaper ([/whitepaper](https://getsincor.com/whitepaper))
- `pitch.html` — Interactive pitch deck ([/pitch](https://getsincor.com/pitch))
- `sin-airdrop.html` — SINC token airdrop page

---

## Deployment

SINCOR2 runs on [Railway](https://railway.app) behind Gunicorn with 2 workers.

```
# Procfile
web: gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 120 --preload run:app
```

The Dockerfile uses `python:3.12-slim`, installs from `requirements.txt`, sets `PYTHONPATH=/app/src`, and runs the same Gunicorn command. Health checks hit `/health` with a 300-second timeout (allowing the model to warm up on first request).

Railway deployment config (`railway.json`):
- Builder: Dockerfile
- Health check: `/health`
- Restart policy: `ON_FAILURE` with up to 10 retries

---

## Environment Variables

Copy `.env.example` to `.env` and populate:

```bash
# Core
FLASK_ENV=production
SECRET_KEY=<long-random-string>
JWT_SECRET_KEY=<long-random-string>

# AI
ANTHROPIC_API_KEY=sk-ant-api03-...

# Stripe (required for live payments)
STRIPE_API_KEY=sk_live_...
STRIPE_PUBLISHABLE_KEY=pk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...

# Email (SendGrid)
SENDGRID_API_KEY=SG....
FROM_EMAIL=support@getsincor.com

# Optional: Redis for distributed rate limiting
RATE_LIMIT_STORAGE_URI=redis://...

# Optional: monitoring
SENTRY_DSN=https://...
```

Never commit `.env` or any file containing real credentials. The `.gitignore` excludes `.env`, `*.env`, and any credential-bearing text files by pattern.

---

## Development Setup

```bash
# Clone and install
git clone https://github.com/OrderofChaos33/SINCOR2.git
cd SINCOR2
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Run locally
python run.py
# → http://localhost:8080
```

For Stripe webhook testing, use the Stripe CLI:
```bash
stripe listen --forward-to localhost:8080/api/stripe/webhook
```

---

## Project Structure

```
SINCOR2/
├── run.py                        # WSGI entry point
├── sincor_stripe_app.py          # Production Flask app
├── requirements.txt              # Python dependencies
├── pyproject.toml                # Package metadata
├── Dockerfile                    # Container definition
├── Procfile                      # Railway/Heroku process config
├── railway.json                  # Railway deployment settings
├── .env.example                  # Environment variable template
├── .gitignore                    # Comprehensive exclusions
│
├── src/sincor2/                  # Main application package
│   ├── __init__.py
│   ├── app.py                    # Full-featured app (auth + rate limits)
│   ├── cortecs_core.py           # Claude brain integration
│   ├── agency_kernel.py          # Agent reasoning loop
│   ├── swarm_coordination.py     # Distributed task market
│   ├── memory_system.py          # 4-tier memory architecture
│   ├── persona_engine.py         # OCEAN personality vectors
│   ├── lifecycle_system.py       # Agent lifecycle management
│   ├── monetization_engine.py    # Revenue orchestration
│   ├── instant_business_intelligence.py
│   ├── dynamic_pricing_engine.py
│   ├── real_time_intelligence.py
│   ├── predictive_analytics_engine.py
│   ├── recursive_value_products.py
│   ├── quality_scoring_engine.py
│   ├── partnership_framework.py
│   ├── infinite_scaling_engine.py
│   ├── auth_system.py
│   ├── rate_limiter.py
│   ├── validation_models.py
│   ├── security_headers.py
│   ├── production_logger.py
│   ├── stripe_checkout.py
│   ├── stripe_routes.py
│   ├── waitlist_system.py
│   ├── email_sender.py
│   ├── pdf_generator.py
│   ├── order_fulfillment.py
│   └── monitoring_dashboard.py
│
├── agents/                       # Agent definitions
│   ├── E-auriga-01.yaml through E-mesarthim-43.yaml
│   └── archetypes/
│       ├── Scout.yaml
│       ├── Builder.yaml
│       ├── Synthesizer.yaml
│       ├── Negotiator.yaml
│       ├── Director.yaml
│       ├── Auditor.yaml
│       └── Caretaker.yaml
│
├── templates/                    # Jinja2 HTML templates
├── static/                       # CSS, JS, favicon
├── docs/                         # Documentation and guides
├── tests/                        # Test suite
├── examples/                     # Usage examples
└── enterprise_infrastructure/    # Enterprise configuration
```

---

## Security Notes

- **No secrets in source** — All credentials are loaded from environment variables. The `.env.example` contains only placeholder values.  
- **Stripe webhook validation** — All incoming webhook events are verified against `stripe-signature` before processing.
- **JWT authentication** — Admin and payment endpoints require a valid Bearer token. Dev-mode fallback keys are obviously labeled.
- **Rate limiting on all public endpoints** — Prevents abuse and brute-force attacks.
- **Input validation** — Pydantic models enforce schema on every API request.
- **HTTP security headers** — CSP, HSTS, X-Frame-Options, and X-Content-Type-Options on every response.
- **Logs are gitignored** — No access logs, security logs, or application logs are committed to the repository.
- **Databases are gitignored** — SQLite files (waitlist, abandoned checkouts) are local only.

---

*Built by [OrderofChaos33](https://github.com/OrderofChaos33) — live at [getsincor.com](https://getsincor.com)*