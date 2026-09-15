# OBS-03 — Compliance

## Claim standard

All external claims for this SKU must be supportable by the canonical files in `/products` and the implementation paths mapped there.

## Allowed claims

- Status: Not sellable today
- Price: $399/mo per workspace
- Source implementation: `src/sincor2/quality_scoring_engine.py`

## Prohibited claims

- Any feature listed under "What it is not" in `SPEC.md`.
- Any statement that upgrades roadmap or blocked work into current sellable functionality.
- Any pricing or readiness statement that conflicts with `/products/skus/OBS-03.json`.

## Evidence checklist

- Confirm `/products/skus/OBS-03.json` matches the outbound claim.
- Confirm mapped source path still exists and supports the claim.
- Disclose blockers when status is not sellable today.
