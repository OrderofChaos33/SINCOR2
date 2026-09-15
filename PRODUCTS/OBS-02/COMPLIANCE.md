# OBS-02 — Compliance

## Claim standard

All external claims for this SKU must be supportable by the canonical files in `/products` and the implementation paths mapped there.

## Allowed claims

- Status: Sellable today
- Price: $199/mo per deployment
- Source implementation: `enterprise_infrastructure/comprehensive_audit_logging.py`

## Prohibited claims

- Any feature listed under "What it is not" in `SPEC.md`.
- Any statement that upgrades roadmap or blocked work into current sellable functionality.
- Any pricing or readiness statement that conflicts with `/products/skus/OBS-02.json`.

## Evidence checklist

- Confirm `/products/skus/OBS-02.json` matches the outbound claim.
- Confirm mapped source path still exists and supports the claim.
- Disclose blockers when status is not sellable today.
