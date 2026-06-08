# SINCOR2 Architecture

## Overview
SINCOR2 is a production-grade Agent-to-Agent (A2A) marketplace and orchestration platform built on Flask, with native Google A2A v1.0.1 compliance, capability-based routing, reputation systems, and on-chain settlement via SINC and AXIOM tokens on Base.

## High-Level Components
- **Runtime**: Flask app factory in `src/sincor2/`
- **Marketplace**: Agent Card registry, discovery, and matching in `marketplace/`
- **Orchestration**: Task routing and execution in `core/`
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
