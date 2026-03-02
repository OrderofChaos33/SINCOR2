# SINCOR — AI Business Automation SaaS Platform

> **The complete, autonomous AI workforce for modern business.** 42 specialized AI agents, real payment processing, executive dashboards, and a self-scaling monetization engine — ready to deploy.

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/template/sincor)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/flask-3.x-green.svg)](https://flask.palletsprojects.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## What is SINCOR?

SINCOR is a production-grade AI SaaS platform that delivers business automation as a service. Clients purchase access to a swarm of 42 purpose-built AI agents that handle sales, analytics, content, lead generation, partnerships, and more — completely autonomously.

### Core Value Proposition

| Feature | Details |
|---|---|
| **42 Specialized Agents** | Named after stars (Sirius, Vega, Orion…) — each mastering a distinct business domain |
| **Autonomous Revenue Engine** | Agents find leads, qualify them, close deals, and process payments with zero human intervention |
| **Dynamic Pricing** | AI-driven pricing adjusts in real time based on demand, client tier, and value delivered |
| **PayPal + On-Chain Payments** | Live PayPal REST API + SINC token (ERC-20 on Base) dual payment rails |
| **Executive Dashboards** | Real-time KPIs, agent utilization, revenue analytics, and client satisfaction scoring |
| **One-Click Deployment** | Railway/Docker-ready; clone → configure env → deploy |

---

## Architecture

```
sincor/
├── run.py                     # Application entry point (Gunicorn target)
├── gunicorn.conf.py           # Production server config
├── Dockerfile                 # Container build
├── Procfile                   # Heroku / Railway process definition
├── railway.json               # Railway deployment config
├── requirements.txt           # Python dependencies
├── .env.example               # Environment variable template
│
├── src/sincor2/               # Core application package
│   ├── mvp_app.py             # Main Flask application
│   ├── auth_system.py         # JWT authentication
│   ├── rate_limiter.py        # DDoS protection
│   ├── security_headers.py    # HTTP security headers
│   ├── validation_models.py   # Pydantic input validation
│   │
│   ├── monetization_engine.py        # Revenue orchestration
│   ├── dynamic_pricing_engine.py     # AI-driven pricing
│   ├── recursive_value_products.py   # Value product framework
│   ├── paypal_integration.py         # PayPal REST API
│   ├── paypal_integration_sync.py    # Sync wrapper for Flask
│   ├── checkout_engine.py            # Order processing
│   ├── checkout_routes.py            # /checkout endpoints
│   │
│   ├── lead_discovery_engine.py      # Autonomous lead generation
│   ├── agent_outreach_handler.py     # Automated outreach
│   ├── autonomous_scheduler.py       # Background task scheduler
│   ├── sales_closing_engine.py       # AI sales closing
│   ├── closing_routes.py             # /sales endpoints
│   ├── agent_commission_engine.py    # Agent revenue sharing
│   ├── commission_routes.py          # /commission endpoints
│   │
│   ├── instant_business_intelligence.py  # Real-time BI reports
│   ├── predictive_analytics_engine.py    # ML forecasting
│   ├── real_time_intelligence.py         # Live market signals
│   ├── quality_scoring_engine.py         # Self-improving quality
│   │
│   ├── partnership_framework.py      # B2B partnership system
│   ├── infinite_scaling_engine.py    # Agent fleet scaling
│   ├── swarm_coordination.py         # Multi-agent orchestration
│   ├── agency_kernel.py              # Core agent runtime
│   ├── cortecs_core.py               # Decision engine
│   ├── persona_engine.py             # Agent persona management
│   │
│   ├── waitlist_system.py            # Pre-launch waitlist
│   ├── memory_system.py              # Agent persistent memory
│   ├── lifecycle_system.py           # Agent lifecycle mgmt
│   ├── monitoring_dashboard.py       # Metrics & health
│   ├── production_logger.py          # Structured logging
│   └── ...
│
├── agents/                    # 42 agent YAML definitions
│   ├── E-sirius-08.yaml       # "The Closer" — elite sales
│   ├── E-vega-02.yaml         # "The Strategist" — growth
│   ├── E-rigel-03.yaml        # "The Analyst" — BI & data
│   └── ...                    # (43 agent files total)
│
├── templates/                 # Jinja2 HTML templates
│   ├── home.html / index.html
│   ├── pricing.html
│   ├── buy.html / checkout.html
│   ├── dashboard.html
│   ├── admin_dashboard.html
│   └── ...
│
└── static/                    # CSS, JS, images
```

---

## Revenue Streams

SINCOR generates revenue through five autonomous channels:

| Stream | Price Range | Delivery |
|---|---|---|
| **Instant BI Reports** | $2,500 – $15,000 | AI-generated in minutes |
| **Agent Subscriptions** | $500 – $5,000/month | Continuous automation |
| **Predictive Analytics** | $6,000 – $25,000 | ML forecasting packages |
| **Enterprise Partnerships** | $50,000 – $200,000 | White-label & API access |
| **Custom Agent Solutions** | Variable | Bespoke deployments |

---

## Quick Start

### Prerequisites

- Python 3.12+
- A PayPal developer account (sandbox or live)
- An Anthropic API key (for Claude AI agents)

### 1. Clone & Install

```bash
git clone https://github.com/OrderofChaos33/SINCOR2.git
cd SINCOR2
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your credentials (see Environment Variables below)
```

### 3. Run Locally

```bash
PYTHONPATH=src python run.py
# or with gunicorn:
PYTHONPATH=src gunicorn -c gunicorn.conf.py run:app
```

Visit `http://localhost:8080`

---

## Deployment

### Railway (Recommended)

1. Fork this repository
2. Create a new Railway project → **Deploy from GitHub repo**
3. Add environment variables in the Railway dashboard (see below)
4. Railway auto-detects `railway.json` and deploys via Docker

### Docker

```bash
docker build -t sincor2 .
docker run -p 8080:8080 --env-file .env sincor2
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | Claude AI API key |
| `JWT_SECRET_KEY` | ✅ | Random secret for JWT tokens |
| `PAYPAL_REST_API_ID` | ✅ | PayPal Client ID |
| `PAYPAL_REST_API_SECRET` | ✅ | PayPal Client Secret |
| `PAYPAL_MODE` | ✅ | `sandbox` or `live` |
| `PAYPAL_SANDBOX` | ✅ | `true` or `false` |
| `FLASK_SECRET_KEY` | ✅ | Flask session secret |
| `STRIPE_SECRET_KEY` | ⬜ | Optional Stripe backup |
| `DATABASE_URL` | ⬜ | PostgreSQL URL (defaults to SQLite) |
| `REDIS_URL` | ⬜ | Redis for rate limiting |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` | ⬜ | Email delivery |
| `SENTRY_DSN` | ⬜ | Error monitoring |

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Landing page |
| `GET` | `/pricing` | Pricing tiers |
| `GET` | `/health` | Health check (used by Railway) |
| `POST` | `/api/waitlist` | Join the waitlist |
| `POST` | `/api/auth/login` | Authenticate and get JWT |
| `POST` | `/api/paypal/create` | Create PayPal payment order |
| `POST` | `/api/paypal/execute` | Execute approved payment |
| `GET` | `/admin/dashboard` | Admin control panel (JWT required) |
| `GET` | `/admin/executive` | Executive KPI dashboard (JWT required) |
| `GET` | `/monetization/dashboard` | Revenue dashboard (JWT required) |
| `GET` | `/api/agents` | List available agents |
| `POST` | `/api/agents/task` | Submit agent task |

---

## Security

- **JWT Authentication** on all admin and paid endpoints
- **Rate Limiting** (Flask-Limiter) — configurable per endpoint class
- **Pydantic Input Validation** on all POST routes
- **HTTP Security Headers** (X-Frame-Options, CSP, HSTS, etc.)
- **Secrets via Environment** — no credentials in source code
- **CORS** configured for allowed origins only

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

---

## The 42 Agents

Each agent is defined in `agents/E-<name>.yaml` with a unique archetype, capabilities, pricing, and behavior profile.

| Archetype | Agents | Domain |
|---|---|---|
| **Sales Closers** | Sirius, Canopus, Rigel | High-ticket sales, closing |
| **Strategists** | Vega, Deneb, Spica | Growth, positioning |
| **Analysts** | Arcturus, Altair, Capella | BI, data, reporting |
| **Content** | Pollux, Procyon, Fomalhaut | Marketing, social media |
| **Lead Gen** | Betelgeuse, Bellatrix, Antares | Prospecting, outreach |
| **Operations** | Polaris, Regulus, Castor | Workflow, fulfillment |
| **Finance** | Mizar, Alioth, Dubhe | Pricing, revenue ops |
| **Partnerships** | Almaak, Mirach, Alpheratz | B2B, enterprise deals |
| + more… | — | — |

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.12, Flask 3.x |
| **Auth** | Flask-JWT-Extended, PyJWT |
| **Validation** | Pydantic v2, email-validator |
| **Rate Limiting** | Flask-Limiter |
| **Payments** | PayPal REST API, SINC Token (Base L2) |
| **AI** | Anthropic Claude (via `anthropic` SDK) |
| **Scheduling** | `schedule` library |
| **Serving** | Gunicorn (production), Flask dev server |
| **Container** | Docker |
| **Hosting** | Railway (primary), any Docker host |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## License

MIT — see [LICENSE](LICENSE).

---

**SINCOR** — *The definitive AI business automation platform.*