# SINCOR2 Architecture Overview (Transition Foundation)

## Purpose

This document establishes a high-level architecture baseline for transition planning toward maximum ecosystem scale.

## Current macro-architecture

```mermaid
flowchart TD
    Users[People / Teams] --> UI[Web Dashboard + Public Site]
    Agents[External A2A Agents] --> AgentCard[Agent Card Discovery]
    AgentCard --> A2A[A2A Gateway / Task API]
    UI --> App[Flask Application Layer]
    A2A --> App
    App --> Orchestration[Swarm Orchestration & Routing]
    Orchestration --> Skills[Specialized Agent Capabilities]
    App --> Payments[Stripe/PayPal + Billing]
    App --> Data[Operational Stores / Files]
    A2A --> Chain[Base On-chain Contracts]
    Chain --> Treasury[Treasury + Liquidity Flows]
    App --> Obs[Monitoring + Health]
```

## Transition target domains

```mermaid
flowchart LR
    core[core/] --- marketplace[marketplace/]
    marketplace --- dae[dae/]
    dae --- infrastructure[infrastructure/]
    docs[docs/] --- assets[assets/]
```

- **core**: orchestration runtime, policy, reliability, execution controls.
- **marketplace**: Agent Cards, discovery, matching, settlement coordination.
- **dae**: incentive design, governance mechanisms, decentralized identity.
- **infrastructure**: deployment, operations, liquidity and treasury integrations.
- **docs/assets**: communication surface for contributors, operators, and partners.

## Scaling focus areas

1. Throughput and latency constraints in orchestration paths.
2. Protocol/version governance for external A2A clients.
3. Economic integrity: fees, incentives, and treasury sustainability.
4. Contributor throughput: clearer modular boundaries and ownership.
5. Observability depth across marketplace, orchestration, and economic layers.
