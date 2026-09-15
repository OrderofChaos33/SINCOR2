# AUD-01 — Compliance

## Claim standard

All external claims for this SKU must be supportable by the canonical files in `/products` and the implementation paths mapped there.

## Allowed claims

- Status: Sellable today
- Price: $499 one-time
- Source implementation: `enterprise_infrastructure` + quality-scoring outputs

## Prohibited claims

- Any feature listed under "What it is not" in `SPEC.md`.
- Any statement that upgrades roadmap or blocked work into current sellable functionality.
- Any pricing or readiness statement that conflicts with `/products/skus/AUD-01.json`.

## Evidence checklist

- Confirm `/products/skus/AUD-01.json` matches the outbound claim.
- Confirm mapped source path still exists and supports the claim.
- Disclose blockers when status is not sellable today.
