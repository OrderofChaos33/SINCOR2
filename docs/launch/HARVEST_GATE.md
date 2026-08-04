# HARVEST MOON ACTIVATION GATE

**Document**: `docs/launch/HARVEST_GATE.md`  
**Directive Date**: 2026-08-04  
**Target Activation**: 2026-09-26 16:48 UTC (Harvest Full Moon)  
**Treasury**: `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`  
**Status**: 🔴 GATE CLOSED — awaiting product conversion threshold

---

## Hard Gate Criteria

The `/harvest` landing page and any public claim announcement remain **offline and unannounced** until **at least one** of the following is satisfied:

| # | Criterion | Measurement | Met? |
|---|-----------|-------------|------|
| A | ≥ 1 paying customer on any plan (Starter / Pro / Enterprise) | Stripe / SINC payment confirmed to treasury | ❌ |
| B | ≥ 3 qualified demos closed with signed letter of intent **or** verified on-chain wallet interaction with a paid skill | Demo log + wallet tx on Basescan | ❌ |

**Both criteria track independently.** Either A or B clears the gate.

---

## Daily Reporting Loop

The following agents report gate status into every 5-minute check-in cycle and the CEO TOA revenue ranking:

- **Negotiator** (`E-antares-13`) — demo pipeline, signed LOI count
- **Caretaker** (`E-arcturus-10`) — paying customer confirmation, onboarding state
- **WebBuilder** (`E-betelgeuse-11`) — vertical conversion events, checkout completions

Each agent emits a structured JSON event:

```json
{
  "event": "harvest_gate_checkin",
  "agent": "<agent_id>",
  "timestamp": "<ISO-8601>",
  "paying_customers": 0,
  "qualified_demos": 0,
  "gate_status": "CLOSED"
}
```

Events are logged via the existing structured logger and surfaced on the operator dashboard.

---

## Kill Switch

Any of the following individuals or mechanisms can pause or abort the Harvest activation at any time:

1. **Contract pause** — `HarvestClaim.sol` exposes `pause()` callable by the contract owner (treasury multi-sig). Execution window: < 2 minutes.
2. **Landing page offline** — Set env var `HARVEST_PAGE_ENABLED=false` and redeploy. Route `/harvest` returns 404. Execution window: < 3 minutes via Railway.
3. **Claim authority revocation** — The Merkle root setter role on `HarvestClaim.sol` can be renounced after the root is set, permanently freezing new roots. Execution window: one on-chain tx.
4. **Agent kill directive** — Issuing `{"directive": "harvest_abort"}` to the TOA orchestrator halts all harvest-related agent tasks and outreach sequences.

**Total reversibility target: < 5 minutes for any single kill action.**

---

## Soft-Launch Sequence (Post-Gate Clearance)

1. Gate cleared → Director confirms → Builder deploys `/harvest` route (set `HARVEST_PAGE_ENABLED=true`).
2. No public social push yet. Agents begin controlled outreach only to warm list.
3. Daily TOA + CEO check-in continues; conversion rate monitored.
4. Final Merkle root generated and set on-chain (Phase 3).
5. Full dry-run with internal wallets.
6. Public activation: **2026-09-26 16:48 UTC**.

---

## Non-Negotiables

- No free-token distribution language until gate is cleared.
- All public copy is pure utility language: "agent access credits" — no investment or ROI language.
- No new token mint. Claim pool funded from pre-allocated treasury slice only (max 1–2% of controlled supply).
- Product revenue is primary metric. Harvest is a conversion funnel, not a speculative event.

---

## Reference

- CEO Execution Plan: `docs/CEO_EXECUTION_2026-08-04.md`
- Claim Specification: `docs/launch/CLAIM_SPEC.md`
- Asset Inventory: `docs/launch/ASSET_INVENTORY_2026-08.md`
- Token Adoption Plan: `docs/TOKEN_ADOPTION_PLAN.md`
