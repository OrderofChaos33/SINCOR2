# CEO DIRECTIVE — Token Pivot to AXIOM (AXM) Only
**Date:** 2026-08-16
**From:** CEO / TOA Oversight
**Authority:** Founder directive (OrderofChaos33)

## Decision
Effective immediately, **AXIOM (AXM)** is the sole platform and settlement token for SINCOR.

**Reason:** 9M token incident affecting SINC. Clean cut required. No dual-token complexity going forward.

## Token Status

| Token | Address (Base) | New Role |
|-------|----------------|----------|
| **AXIOM (AXM)** | `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a` | **SOLE** platform utility + A2A settlement + billing + fees |
| SINC | `0xe1D836087F6573b665d25CE088793E916D7892f8` | Legacy only. No new billing, no new subscriptions, no new A2A quotes in SINC. Bonding curve remains for residual holders; do not expand. |

Live AXM pointer corrected 2026-08-18 (retired PumpClaw `0xfF7aF6…` is denylisted in `src/sincor2/onchain/constants.py`).

Treasury remains `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`. All realized inflows (fees, settlements, pilot payments) must be denominated or convertible to AXM / USDC and recorded against this address.

## Mandatory Changes (Builders — P0)

1. **A2A quote & settlement**
   - `/api/a2a/quote` and success path: price and settle exclusively in AXM.
   - `record_inflow(..., source="a2a_settlement", ...)` must use AXM amounts or USDC equivalent with clear tagging.
   - Remove or hard-deprecate any SINC payment path for new tasks.

2. **Billing / subscriptions / vertical pilots**
   - All new plans and one-off reports quote and accept AXM only.
   - Update `/buy` and Agent Card payment schemes.

3. **Canonical & docs**
   - `CANONICAL_ADDRESSES.md` updated: AXIOM marked primary.
   - Website, Agent Cards, onboarding docs, EXTERNAL_A2A_ONBOARDING.md: AXM only.

4. **Yield Aggregator / DeFi swarms**
   - Continue operating on USDC/ETH capital.
   - Any fee accrual or buyback path that previously targeted SINC now targets AXM or USDC to treasury.

5. **Self-improving loops**
   - TOA ranking and scheduler feedback must use AXM-denominated projected/realized inflow as the primary KPI.

## Non-negotiables
- DRY_RUN default remains.
- No mainnet mutation of existing SINC contracts.
- Full unit tests on any settlement path change.
- Fee-only to treasury ledger.
- Overlapping accountability: every swarm reports AXM-related progress into TOA + ledger.

## Hard EOD addition
In addition to prior EOD goals (realized inflow or paid pilot or external A2A success + Yield Aggregator plan), deliver at least one code path or doc that enforces AXM-only for new settlements.

**Primary KPI unchanged in spirit:** Realized Treasury inflow to `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` — now preferentially in AXM or USDC convertible to it.

— CEO / TOA
