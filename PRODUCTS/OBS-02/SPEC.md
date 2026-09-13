# OBS-02 — Agent Audit Trail

- Status: Sellable today
- Price: $199/mo per deployment
- Source implementation: `enterprise_infrastructure/comprehensive_audit_logging.py`
- Readiness: Ready to sell after fixing the key-loading syntax bug.

## What it is

- Packaging of the current audit logger: per-event SHA-512 hashes, Ed25519 signatures, attribution fields, storage, and compliance reporting.
- A tamper-evident audit evidence layer for agent actions.
- A recurring observability product grounded in implemented code.

## What it is not

- Not a predecessor-linked hash chain.
- Not a new logging subsystem separate from `enterprise_infrastructure`.
- Not a dashboard-centric product.

## Canonical source

- `/products/catalog.json`
- `/products/skus/OBS-02.json`
- `/products/skus/OBS-02.md`
