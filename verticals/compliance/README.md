# Compliance Vertical Pack

**Domain Focus:** Regulatory workflow automation, audit evidence generation, policy validation, and compliance monitoring.

**Integration Points**
- Marketplace capability matching
- Core task router
- On-chain settlement for paid tasks
- A2A Agent Card discovery

**Current Status**
Production-grade scaffolding in place. Base agent, schemas, and structure ready for implementation.

**Key Planned Capabilities**
- Regulatory requirement interpretation agents
- Audit trail and evidence packet automation
- Policy drift detection and remediation workflows
- Jurisdiction-aware compliance task orchestration

**Next Implementation Steps**
1. Implement concrete agents inheriting from `VerticalAgent`
2. Define detailed Pydantic schemas in `schemas.py`
3. Register capabilities and expose via Agent Card
4. Add integration tests with core marketplace
