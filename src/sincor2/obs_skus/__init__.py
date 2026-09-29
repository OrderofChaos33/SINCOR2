"""SINCOR observability SKU toolkit.

Service-delivery modules for AUD-01 (Agent Forensic Audit) and AUD-02
(Compliance Pack) engagements:

- forensic_audit.py ... AUD-01 wedge product: verify a signed audit archive,
  reconstruct an attributable per-agent timeline, classify failure modes from
  real evidence, and render a customer-ready report.
- compliance_pack.py .. AUD-02: PHI guardrail redaction profiles, deterministic
  paginated audit exports with a chain-of-custody manifest, and the setup
  engagement checklist.

Each module is standalone. Sibling modules built in parallel
(audit_trail.py - hash-chained signed log + archive packing,
drift_quality.py - quality trends + drift) are consumed defensively through
documented schemas and are never required at import time. Every schema
assumption is written down in the module docstrings so a future reader can
check them against the real sibling implementations.
"""
