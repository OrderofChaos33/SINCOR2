# Trading Vertical Pack

**Domain Focus:** Market intelligence, signal orchestration, risk-aware execution support, and strategy automation workflows.

**Integration Points**
- Marketplace capability matching
- Core task router
- On-chain settlement for paid tasks
- A2A Agent Card discovery

**Current Status**
Production-grade scaffolding in place. Base agent, schemas, and structure ready for implementation.

**Key Planned Capabilities**
- Signal generation and strategy evaluation agents
- Position sizing and risk control automation
- Opportunity detection and market monitoring workflows
- Execution-aware trade support orchestration

**Next Implementation Steps**
1. Implement concrete agents inheriting from `VerticalAgent`
2. Define detailed Pydantic schemas in `schemas.py`
3. Register capabilities and expose via Agent Card
4. Add integration tests with core marketplace
