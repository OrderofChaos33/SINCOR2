# SINCOR2: Universal Execution Layer for the Autonomous Machine Economy

SINCOR2 is a production-hardened platform engineered to orchestrate, secure, and settle Agent-to-Agent (A2A) commerce. Decentralized identity, cognitive kernels, and on-chain clearing let specialized agents discover work, execute it, and settle without a human in the loop.

Live: [getsincor.com](https://getsincor.com) · Repo: [OrderofChaos33/SINCOR2](https://github.com/OrderofChaos33/SINCOR2) · Chain: Base

[![Live Platform](https://img.shields.io/badge/Live-getsincor.com-00C853)](https://getsincor.com)
[![A2A API](https://img.shields.io/badge/API-A2A%20Reference-4C8BF5)](docs/api/README.md)
[![TOA](https://img.shields.io/badge/TOA-Wave%20Function%20Collapse-9C27B0)](#temporal-optimization-agent-toa)
[![KYA](https://img.shields.io/badge/KYA-Know%20Your%20Agent-00897B)](#kya--know-your-agent)
[![CHROMA](https://img.shields.io/badge/CHROMA-Detailing%20Growth%20OS-FF6D00)](#chroma--auto-detailing-growth-os)
[![Base](https://img.shields.io/badge/Chain-Base-0052FF?logo=coinbase&logoColor=white)](https://base.org)

## Three pillars

1. **Sovereign A2A billing (x402).** Agent Cards at `/.well-known/agent-card.json`. External systems discover, quote, and settle on Base in AXIOM (AXM) through JSON-RPC.
2. **On-chain primitives.** 112 Solidity files under `onchain/` and `contracts/` (sources, scripts, tests, proposals, legacy). AXM is the live A2A settlement asset. SINC live pointer `0xe1D836087F6573b665d25CE088793E916D7892f8`. V4 hook source is in-tree; not advertised as live MEV cover until a vendored `forge` deploy is recorded.
3. **TOA.** Forecast → Monte Carlo → wave-function collapse so swarms pick one high-utility path instead of drifting.

SINAX (`src/sincor2/sinax/`) remains a research prototype and is not wired into the Agency Kernel.

## Table of contents

- [Why SINCOR2](#why-sincor2)
- [What is live (2026-09)](#what-is-live-2026-09)
- [Live capital policy](#live-capital-policy)
- [Quickstart](#quickstart)
- [Overlapping consensus](#overlapping-consensus)
- [TOA](#temporal-optimization-agent-toa)
- [KYA](#kya--know-your-agent)
- [CHROMA](#chroma--auto-detailing-growth-os)
- [Architecture and engines](#architecture-and-engines)
- [A2A and marketplace](#a2a-and-marketplace)
- [On-chain economy](#on-chain-economy)
- [Documentation map](#documentation-map)

## Why SINCOR2

Built for operators who need agents that generate realized revenue — not demos.

- **A2A-native.** Any v1.0.1 client can discover, quote, pay, and call skills.
- **43 named skills in YAML** under `agents/`. Runtime is shared Flask workers plus optional Celery when `REDIS_URL` is set. That catalog is not 43 Railway dynos.
- **Overlapping consensus.** Kernel critic, TOA collapse, KYA identity, and Cortex merit must agree before a consequential action ships.
- **Cortex memory and merit.** Episodic scratch purges on close. Semantic vault is merit-gated with Ebbinghaus decay. Optimistic Merkle settlement + EigenTrust + honeypots.
- **Autonomous revenue stack.** Dynamic pricing, fulfillment, partnership framework, outreach, content, Polyclaw, CHROMA, healthcare / dental / compliance verticals — all still in tree.
- **Production runtime.** Flask factory, JWT, rate limits, security headers, structured logs, Railway + Docker.

Nothing in `src/sincor2/` engines, `dae/`, `verticals/`, `onchain/`, or `contracts/` was removed to write this README.

## What is live (2026-09)

- Site and cards: [getsincor.com](https://getsincor.com), `/buy`, `/.well-known/agent-card.json`
- Realized KPI: treasury [`0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`](https://basescan.org/address/0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac) with `projected=false` + `tx_hash`
- A2A quotes and `message/send` settle in AXM (`0x4c3fb66f14fbaa2088c9ae91017ba770da53715a`). Platform take **500 bps**. Simulated / `free_call` stay off the realized ledger.
- Inbound fabric: `POST /v1/a2a/register`, heartbeat, directory, SSE
- KYA directory + merkle quest (production root only)
- Polyclaw scheduler in the web process; live orders when `POLYCLAW_LIVE=true` and the Polymarket key is set
- 26 DeFi swarm check-ins feed TOA
- SINC buy surface is `/buy`. Retired `0x9C8cd8…` is denylisted

## Live capital policy

HOLD was **lifted by the founder 2026-09-10 20:54 CDT**. Source of truth: [`HOLD.md`](HOLD.md) and `src/sincor2/treasury_hold.py`.

1. 500 bps realized fees still route to treasury.
2. AXM paid lane is live.
3. Polyclaw is authorized 24/7 on its own Polygon wallet — never the treasury key.
4. `EXECUTE_LIVE` defaults on in source after the lift; set `EXECUTE_LIVE=0` to block treasury-exec. Delete `data/TREASURY_EXEC_HALT` on the Railway volume if that file exists.
5. Auditor still gates vault / LP size-ups. Never commit keys.

## Quickstart

```bash
git clone https://github.com/OrderofChaos33/SINCOR2.git
cd SINCOR2
cp .env.example .env
pip install -r requirements.txt
python run.py
curl http://localhost:8080/.well-known/agent-card.json
```

JSON-RPC: `POST /api/a2a` method `message/send`. Full reference: [docs/api/README.md](docs/api/README.md).

## Overlapping consensus

Consequential work walks four layers. Each can refuse.

| Layer | Role | Location |
|---|---|---|
| **KYA** | Names the principal | `src/sincor2/kya/`, `contracts/kya/KYARegistry.sol` |
| **TOA** | Ranks futures, collapses one path | `agents/toa/` |
| **Agency Kernel** | Planner → Executor → Critic → Archivist | `src/sincor2/agency_kernel.py` |
| **Cortex merit** | Memory gate + EigenTrust | `src/sincor2/memory_system.py` + Cortex modules |

Cards advertise capability. KYA names who is acting. TOA chooses when. The kernel does the work. Settlement records only realized AXM. That is the approval loop.

## Temporal Optimization Agent (TOA)

TOA sits above the task router and answers: given this state and these objectives, which action next — and in what order?

Full spec: [`docs/architecture/toa.md`](docs/architecture/toa.md).

```
Context → KernelForecaster → MonteCarloSimulator → WFCCollapser → action plan
                ↑                         RollingFeedback ← results
```

| Module | Job |
|---|---|
| `KernelForecaster` | Nadaraya-Watson smoother + Monte Carlo paths |
| `MonteCarloSimulator` | Scores revenue, risk, timeline, compliance, governance |
| `WFCCollapser` | Drops weak paths, ranks utility × probability × priority, returns top-k |
| `RollingFeedbackAgent` | Ingests vertical, swarm, and settlement outcomes |
| `TOAOrchestrator` | Public entry; optional TaskRouter dispatch |

Configure with `TOA_*` env vars. DeFi swarm check-ins and underwriting envelopes already feed this loop. **TOA does not spend.** Spend still requires KYA + bankroll + live adapter.

## KYA — Know Your Agent

KYA turns a skill string into a participant.

| Surface | What it does |
|---|---|
| `src/sincor2/kya/` | Registry, heartbeat, SLA / SADAS receipts |
| `/v1/kya/directory`, `/v1/kya/<id>` | Public lookup |
| Merkle quest | Genesis / airdrop claims bound to `KYA_PRODUCTION_ROOT` |
| `contracts/kya/KYARegistry.sol` | On-chain identity when posted to Base |

Hard rules: replica roots raise `REPLICA_ROOT_FORBIDDEN`. Register may store a wallet; it does not mint one at YAML parse. CREATE2 / smart-account issuance belongs on first **paid** settlement. Admin seed needs `KYA_ADMIN_KEY`.

## CHROMA — Auto Detailing Growth OS

CHROMA is the shop-floor vertical for detailing, ceramic, PPF, and tint. It is **not** a ChromaDB vector store. Memory for every agent, including CHROMA, is the four-tier store in `memory_system.py` (episodic / semantic / procedural / autobiographical) with hybrid RAG.

Pack: [`verticals/auto_detailing/`](verticals/auto_detailing/README.md). Wired through `platform_bootstrap.py` and `vertical_dispatch.py`.

Detailers lose jobs to speed. CHROMA answers first, quotes by package × vehicle size, takes coating deposits, hands off to Calendly, then compounds GBP, reviews, social, and 30/60/90-day memberships while the bays are full.

Skill ids: `detailing-lead-ingest`, `detailing-booking`, `detailing-presence`, `detailing-social`, `detailing-copy`, `detailing-engage`.

Protocols: sub-5-minute first response, photo-quote intake, deposit-on-booking, T-24h / T-2h reminders, T+2h review ask, seasonal salt / pollen / UV / PPF campaigns, wash → interior → ceramic → PPF upsell graph.

## Architecture and engines

All of these remain in the repository and in the runtime import graph:

| Component | Path |
|---|---|
| Flask / A2A / payments | `src/sincor2/` |
| Agency kernel | `src/sincor2/agency_kernel.py` |
| Swarm / contract-net | `src/sincor2/swarm_coordination.py` |
| Memory / persona / quality | `memory_system.py`, `persona_engine.py`, `quality_scoring_engine.py` |
| Monetization / pricing / revenue | `monetization_engine.py`, `dynamic_pricing_engine.py`, `revenue_orchestrator.py` |
| Intelligence / forecasting | `real_time_intelligence.py`, `predictive_analytics_engine.py` |
| Lifecycle / scaling | `lifecycle_system.py`, `infinite_scaling_engine.py` |
| Polyclaw | `polyclaw_scheduler.py`, `execution_adapter.py`, `bankroll.py` |
| Outreach / content | `outreach_engine.py`, `content_agent.py` |
| Verticals | `verticals/` healthcare, dental, compliance, trading, lead_gen, auto_detailing |
| DAE | `dae/` identity, incentives, governance |
| Marketplace / core / infra | `marketplace/`, `core/`, `infrastructure/` |
| Agents catalog | `agents/` |
| Contracts | `onchain/`, `contracts/` |

Mermaid and deeper tables: [`ARCHITECTURE.md`](ARCHITECTURE.md), [`docs/architecture/overview.md`](docs/architecture/overview.md).

## A2A and marketplace

Live on getsincor.com: Agent Card, `/api/a2a` (`message/send`, `message/stream`, `tasks/*`), inbound register, heartbeat, directory, SSE fabric, marketplace register.

Routing blends capability (75%) and trust (25%). SINC stake can boost priority. Contract-net: broadcast → bid → award → execute → credit → auditor.

## On-chain economy

| Asset | Address | Role |
|---|---|---|
| AXM | `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a` | A2A settlement |
| SINC | `0xe1D836087F6573b665d25CE088793E916D7892f8` | Utility pointer |
| Treasury | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` | Fee sink |

Canon: `CANONICAL_ADDRESSES.md`, `src/sincor2/onchain/constants.py`. Bonding-curve public sell path is retired; official buy is `/buy`.

## Documentation map

| Doc | Topic |
|---|---|
| [docs/architecture/toa.md](docs/architecture/toa.md) | TOA pipeline |
| [verticals/auto_detailing/README.md](verticals/auto_detailing/README.md) | CHROMA |
| [HOLD.md](HOLD.md) | Capital policy |
| [docs/api/README.md](docs/api/README.md) | JSON-RPC |
| [docs/token/README.md](docs/token/README.md) | SINC / AXM |
| [CANONICAL_ADDRESSES.md](CANONICAL_ADDRESSES.md) | Addresses |
| [docs/A2A_PRODUCTION_CHECKLIST.md](docs/A2A_PRODUCTION_CHECKLIST.md) | Live gates |
| [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) | Railway / Docker |
| [constitution/global.md](constitution/global.md) | Agent constitution |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Do not shrink this README to a placeholder. Additive edits only.

```bash
PYTHONPATH=src:src/sincor2 python tests/run_all_tests.py
ruff check src/sincor2
```

## License

MIT — [LICENSE](LICENSE).
