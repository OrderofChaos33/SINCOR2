# Solver-Bonding Requirements Spec — P23 Intent Solver & Dark Pool

Status of this spec: requirements template for a future signed
engagement. **No solver is deployed or bonded for outside flow today.**
Nothing in this document is in effect until a counterparty engagement
is signed and the bonding terms in it are executed.

## What the code actually checks (verified against the source)

The source module (`intent_dark_pool.py`, 550 lines) was searched for
any bonding, staking, collateral, slashing, or solver-registration
logic. Findings:

- The module contains **no** bond, stake, collateral, slashing, or
  solver-registration code of any kind. There is no bond asset, no bond
  amount, no custody path, no lock period, and no return or forfeiture
  condition anywhere in the code. (A packaging test,
  `test_no_bonding_surface_in_code`, asserts this silence so it cannot
  drift unnoticed.)
- The only access-control surface in the code is role membership passed
  in by the caller as plain Python sets: `MATCHER_ROLE`,
  `GUARDIAN_ROLE`, `ADMIN_ROLE`. In the reference build these roles
  gate only pause/unpause (`GUARDIAN_ROLE`); matching and settlement
  do not check a solver bond or even a solver identity.
- The only economic constant in the code is the in-simulation
  settlement fee: `FEE_BPS = 8` (8 bps per settled leg, to the treasury
  address). It is not a bond and implies no bond sizing.
- Default mode is `DRY_RUN`; `executed` is always `False`; the module
  touches no chain and no funds, so there is nothing a bond could
  secure in the reference build as extracted.

## Bonding terms an engagement must set

Because the code is silent, every item below is recorded as
**not specified in code — engagement term to be set**. No numbers are
invented here; the signed engagement must fill each row before any
solver handles outside flow.

| Term | Value |
|---|---|
| Bond asset | not specified in code — engagement term to be set |
| Bond sizing basis (flat, per-order, volume-scaled, or other) | not specified in code — engagement term to be set |
| Bond amount / sizing parameters | not specified in code — engagement term to be set |
| Who posts the bond (solver operator, counterparty, or other) | not specified in code — engagement term to be set |
| Custody arrangement (escrow, contract, custodian) | not specified in code — engagement term to be set |
| Lock period / posting timing relative to flow | not specified in code — engagement term to be set |
| Return conditions (when and how the bond is released) | not specified in code — engagement term to be set |
| Forfeiture / slashing conditions and adjudication | not specified in code — engagement term to be set |
| Solver eligibility / registration criteria | not specified in code — engagement term to be set |
| Insurance or backstop beyond the bond | not specified in code — engagement term to be set |

## Preconditions before outside flow (all required)

1. A signed counterparty engagement that completes every row above.
2. Code changes, if the engagement requires on-chain or in-code bond
   enforcement — the reference build enforces none, so any enforcement
   is new work scoped inside the engagement, with its own tests.
3. Founder gate: any deployment happens only inside that signed
   engagement (see PACKAGING.md and ENGAGEMENT.md).

Until all three hold, the correct description of this module remains:
a platform reference module; no solver deployed; no solver bonded.
