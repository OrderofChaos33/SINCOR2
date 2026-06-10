# SINCOR2 Architecture

## Overview
SINCOR2 is a production-grade Agent-to-Agent (A2A) marketplace and orchestration platform built on Flask, with native Google A2A v1.0.1 compliance, capability-based routing, reputation systems, and on-chain settlement via SINC and AXIOM tokens on Base.

## High-Level Components
- **Runtime**: Flask app factory in `src/sincor2/`
- **Marketplace**: Agent Card registry, discovery, and matching in `marketplace/`
- **Orchestration**: Task routing and execution in `core/`
- **TOA Layer**: Temporal Optimization Agent — decision-optimization pipeline in `agents/toa/`
- **Verticals**: Domain-specific agent packs in `verticals/`
- **DAE Layer**: Decentralized governance and incentives in `dae/`
- **On-Chain**: Token integrations and settlement in `onchain/`
- **Infrastructure**: Deployment, observability, and scaling in `infrastructure/`

## Data Flow
1. Agent Card registration → Discovery
2. Capability matching + reputation scoring
3. Task routing with policy enforcement
4. Execution (local or delegated)
5. On-chain settlement (SINC/AXIOM)

See `docs/architecture/overview.md` for diagrams and detailed views.

---

## Temporal Optimization Agent (TOA)

The TOA layer sits above the core task router and provides a continuous
**forecast → simulate → collapse** decision-optimization pipeline.

### TOA Data Flow

```
Context Input (time-series, signals)
        │
        ▼
┌────────────────┐     ┌─────────────────┐     ┌──────────────────┐
│  KernelForec.  │────▶│ MonteCarloSim.  │────▶│  WFCCollapser    │
│  N Monte Carlo │     │  Multi-criteria │     │  Rank & select   │
│  forecast paths│     │  objective score│     │  top-k actions   │
└────────────────┘     └─────────────────┘     └────────┬─────────┘
        ▲                                                │
        │         ┌──────────────────┐    Action         ▼
        └─────────│ RollingFeedback  │◀── Results   TaskRouter /
                  │ (execution sigs) │              Vertical agent
                  └──────────────────┘
```

### TOA Integration Points

| Touchpoint | Mechanism |
|---|---|
| Task routing | Pass `task_router=TaskRouter(...)` to `TOAOrchestrator` |
| Vertical outcomes | Call `toa.ingest_feedback(event)` after any vertical task |
| On-chain events | Ingest SINC/AXIOM settlement events as feedback signals |
| DAE governance | Wire governance proposal outcomes to shape objective weights |
| Custom objectives | `toa.register_objective("name", fn)` at runtime |
| Persistent state | `TOAConfig.state_path` → JSON file backed `TOAStateStore` |

### TOA Module Layout

```
agents/toa/
├── __init__.py        # Public API
├── base.py            # Abstract base classes (4 interfaces)
├── config.py          # Environment-driven configuration
├── state.py           # JSON-backed session state
├── forecaster.py      # KernelForecaster (pluggable)
├── simulator.py       # MonteCarloSimulator + objective registry
├── collapser.py       # WFCCollapser (wave-function collapse)
├── feedback.py        # RollingFeedbackAgent
└── orchestrator.py    # TOAOrchestrator (main entry point)
```

For a full technical reference see `docs/architecture/toa.md`.
