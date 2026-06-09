# Dental Vertical Pack

**Domain Focus:** Practice operations automation, patient engagement, treatment workflow support, and dental office compliance.

**Integration Points**
- Marketplace capability matching
- Core task router
- On-chain settlement for paid tasks
- A2A Agent Card discovery

**Current Status**
Production-grade scaffolding in place. Base agent, schemas, and structure ready for implementation.

**Key Planned Capabilities**
- Scheduling and recall optimization agents
- Treatment plan and case follow-up automation
- Billing and front-desk workflow orchestration
- Compliance-aware operational task execution

**Next Implementation Steps**
1. Implement concrete agents inheriting from `VerticalAgent`
2. Define detailed Pydantic schemas in `schemas.py`
3. Register capabilities and expose via Agent Card
4. Add integration tests with core marketplace
