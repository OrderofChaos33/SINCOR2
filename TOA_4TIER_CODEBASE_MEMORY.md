# TOA + 4-Tier Memory — Codebase Continuous Self-Improvement (No Scope Drift)

**Authority:** Founder / TOA Oversight  
**Created:** 2026-08-18  
**Purpose:** Permanent, hierarchical memory attached to the SINCOR2 GitHub repository that forces every future change (human or agent) to stay inside the AXM-primary token scope defined by CEO directive 2026-08-16. Prevents regression to SINC-primary paths, dual-token complexity, or unapproved expansion.

## Tier 0 — Immutable Directive (never mutate without explicit Founder override)
- AXIOM (`0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822`) is the **sole** primary platform, A2A settlement, billing, and fee token.
- SINC (`0x9C8cd8d3961F445D653713dE65C6578bE11668e7`) is legacy only. No new billing, no new A2A quotes, no new subscriptions, no new access gates in SINC.
- Treasury remains `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`. All realized inflows must be AXM-denominated or USDC convertible to it.
- CANONICAL_ADDRESSES.md is the single source of truth. Any file that disagrees is wrong and must be corrected.
- DRY_RUN remains the default for any mutating path until explicit live authorization.

## Tier 1 — Structural Invariants (enforced by CI / pre-commit / TOA critic)
1. Default value of `A2A_PRIMARY_TOKEN` must be `AXIOM` (or absent → AXIOM).
2. `marketplace/settlement.py` create_quote default token_symbol must resolve to AXIOM.
3. Any new payment / quote / billing path must accept AXM; SINC paths must be explicitly labeled `legacy` and gated behind feature flag or explicit argument.
4. Environment variable names for the primary token must prefer `AXIOM_CONTRACT_ADDRESS` / `AXM_*`.
5. All new Agent Cards, onboarding docs, and EXTERNAL_A2A_ONBOARDING.md must list AXM only for payment schemes.
6. No hard-coded SINC address may appear in new code paths without a `LEGACY_` prefix or comment containing the string "legacy only".

## Tier 2 — Operational Memory (updated after every successful change)
- 2026-08-18: settlement.py flipped to AXIOM primary + full address/amount/symbol validation + init-time canonical checks. Commit 44324d10.
- Next required actions (ordered):
  1. settings.py — add axiom_contract_address field, keep sinc only for legacy reads.
  2. a2a_integration.py — confirm all quote paths already use AXIOM (partially done).
  3. templates (buy*.html, sinc_gateway.html, billing_tokens.html) — replace SINC CTAs with AXM.
  4. static/js/sinc_wallet.js → rename/refactor to axiom_wallet.js or dual with AXM primary.
  5. docs/SINC_INTEGRATION.md → retitle or mark as historical; create AXM_INTEGRATION.md.
  6. Agent YAMLs and marketplace/agent_cards.json — paymentScheme → AXM.
  7. Tests: update test_sinc_integration.py assertions to expect AXIOM default.
  8. .env.example and config/railway_add_these.env — AXIOM_CONTRACT_ADDRESS first.
  9. onchain scripts that still hard-code SINC as primary for new deploys must be annotated legacy.

## Tier 3 — Self-Improvement Loop (TOA + Critic + Optimizer)
Every PR / agent run that touches token logic must:
1. Run a grep for `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` and `A2A_PRIMARY_TOKEN.*SINC` and report any new non-legacy occurrences.
2. Critic agent must reject any change that re-introduces SINC as default or removes the address validation guards.
3. Optimizer agent must propose the next highest-ROI remaining item from Tier 2 list after each successful merge.
4. Memory itself is updated only by appending dated entries under Tier 2; Tier 0 and Tier 1 are append-only under Founder signature.

## Enforcement
- This file lives at repository root: `TOA_4TIER_CODEBASE_MEMORY.md`.
- TOA agent (E-toa-44) is permanently assigned read + enforce rights on this memory.
- Any agent that mutates settlement, a2a, billing, or token-related files must load this memory first and include a "scope compliance" section in its output.
- Scope drift detection: if a change increases the number of live SINC-primary code paths, the Governor layer must veto.

**Last verified:** 2026-08-18 by Grok + TOA protocol.
