# Engagement Sheet — P22 Concentrated Liquidity Manager

Suite name: P02 · SKU: `SINCOR-DEFI-P02-CLMM` · Module: Concentrated
Liquidity Manager · State: **EXTERNAL**

## Status — read first

- This module exists as a platform module inside SINCOR2. **It is not
  separately deployed today.**
- **A buyer/counterparty is required.** There is no self-serve offering
  and no standing deployment to buy into.
- **Deployment happens only inside a signed engagement** — scoped,
  per counterparty, per the payment-rail form below. Nothing in this
  package is deployed by the act of packaging it.
- This sheet is a scope template for such an engagement. It makes no
  performance promises and states no results: the module is a reference
  implementation whose behaviour is specified by its code and tests,
  and any outcome in a counterparty's pools depends on that
  counterparty's pools, flow, and configuration.

## What the module is

Concentrated-liquidity position management for v3/v4-style pools:

- JIT (just-in-time liquidity) attack detection over same-block
  add/remove/swap event sequences, with an integer-exact sensitivity
  threshold.
- Tick-shift response that moves managed positions off an attack range
  (volatility-band bounded, cooldown- and cumulative-cap-guarded),
  emitted as dry-run intents.
- A capped transient surcharge on flagged JIT providers, with fee math
  isolated so a failure degrades to zero rather than blocking a swap.
- Pro-rata distribution of captured proceeds to passive LPs holding
  through the attack block (pull-based claims; 15 bps of captured
  proceeds to the treasury; integer-exact conservation).
- Realized-volatility range-width computation with dry-run re-range
  intents.
- Role-based access control (manager/guardian); a defense pause never
  blocks user withdrawals or claims.

The core ships in dry-run mode. Opening live positions is hard-blocked
in code until an auditor gate is flipped with a signed config — that
flip, if an engagement calls for it, is a separately scoped and
separately signed step, not part of the base scope below.

## Priced terms (exactly as catalogued)

| Form | Terms |
|---|---|
| Primary — one-time engagement | **$80,000 one-time engagement** |
| Secondary — service form | **15% of earned LP trading fees** |
| Payment-rail form | Scoped integration/deployment engagement, per counterparty |

Notes on the terms:

- The primary and secondary forms are alternatives/complements as
  agreed in the signed engagement document; this sheet does not invent
  combinations, discounts, or schedules beyond the table above.
- The 15% service form applies to LP trading fees **actually earned**
  during the engagement, computed integer-exactly by
  `p22_clmm_manager.service_fees` (floor of earned × 1500 / 10,000).
  It does not apply to projected, marked, or unearned amounts.
- The 15% service fee is distinct from the module's internal 15 bps
  (0.15%) treasury cut on captured JIT proceeds, which is protocol math
  inside `ProceedsDistributor`, not an engagement charge.

## Scope template (to be completed per counterparty)

1. **Counterparty:** _[legal name]_ · **Pools/positions in scope:**
   _[pool identifiers, chain/environment]_ · **Term:** _[start/end]_.
2. **Integration work:** wire the module's detector, responder, fee,
   distributor, and range agent to the counterparty's event feed and
   oracle adapter (the package's oracle surface is a stub contract —
   see `PACKAGING.md`); configure `PoolConfig` per pool and the env
   config surface in `deploy/DEPLOY.md`.
3. **Drill:** run the Sepolia drill in `deploy/DEPLOY.md` in dry-run
   mode against recorded and/or Sepolia event windows; deliver the
   drill report.
4. **Acceptance:** package test suite green in the counterparty
   environment; drill findings reviewed and either resolved or recorded
   as accepted findings.
5. **Out of scope unless separately signed:** flipping the auditor gate
   or executing any intent against live positions; any non-Sepolia
   production environment; custody of counterparty funds or keys (the
   module never takes custody — it emits intents and accounting only);
   ongoing operation beyond the engagement term.

## Sign-off block

| | Counterparty | SINCOR |
|---|---|---|
| Name / title | | |
| Signature | | |
| Date | | |
| Chosen priced form(s) | | |
| Pools in scope | | |
