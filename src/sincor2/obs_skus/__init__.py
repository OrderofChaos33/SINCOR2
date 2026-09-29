"""SINCOR observability SKU family (``obs_skus``).

Enterprise observability products built on the marketplace's real
observability primitives:

- ``vitals``        per-agent vitals snapshots        (sibling track)
- ``audit_trail``   incident / audit event log        (sibling track)
- ``drift_quality`` quality drift signals            (sibling track)
- ``enterprise_mesh`` (this package) fleet rollup, SLA evaluation,
  outbound alert adapters, and support-runbook generation.

``enterprise_mesh`` imports its siblings defensively: if a sibling module
is not installed yet, plain dict fixtures matching the documented schemas
in ``enterprise_mesh``'s docstring work everywhere instead.
"""
