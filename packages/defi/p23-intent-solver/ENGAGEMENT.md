# Engagement Sheet — P23 Intent Solver & Dark Pool

- Module: P23 — Intent Solver & Dark Pool (suite name P03;
  SKU `SINCOR-DEFI-P03-DARKPOOL`)
- What it is: Intent-solving and dark-pool execution module. Exists as
  a platform module; no solver is deployed or bonded for outside flow.
- State: **EXTERNAL** — a buyer/counterparty is required.
- Payment-rail form: scoped integration/deployment engagement, per
  counterparty.

## Plain statement of status

No solver is deployed or bonded for outside flow today. Deployment
happens only inside a signed engagement. This sheet is a scope and
price template for such an engagement; it is not an offer of a running
service, and it makes no performance promises and states no results.

## Priced terms (catalog, verbatim)

- Primary form: **$127,500 one-time engagement.**
- Secondary service form: **2 bps of volume + 50% of price
  improvement, capped at 0.98% of order volume.**
- Payment-rail form: scoped integration/deployment engagement, per
  counterparty.

The secondary-form arithmetic is implemented and tested in
`src/sincor_p23_intent_solver/engagement_fees.py`
(`engagement_fee(order_volume, price_improvement)`), integer math,
floor division. Note the distinction: the reference code's internal
settlement fee constant is 8 bps per settled leg to the treasury
(`FEE_BPS = 8`); that is an in-simulation protocol constant, separate
from the engagement terms above.

## Scope template (to be completed per counterparty)

1. Counterparty: [name / entity — to be completed]
2. Scope of integration: [systems, venues, and assets in scope — to be
   completed; note the reference code settles AXM/USDC only]
3. Solver operation model: [who operates the solver — to be
   completed]
4. Solver bonding: per SOLVER_BONDING.md — every bonding term is
   "not specified in code — engagement term to be set" until this
   engagement sets it in writing.
5. Price-feed adapters: the package vendors only a minimal oracle
   wiring stub; real feed adapters, if required, are scoped here.
6. Deployment environment and chain(s): [to be completed — Sepolia
   drill first, per DEPLOY.md]
7. Term, fees (priced terms above), and payment schedule: [to be
   completed]
8. Exclusions: no outside flow before signature; no mainnet activity
   unless expressly written into the signed scope.

## Solver-bonding requirements

See **SOLVER_BONDING.md** in this package for the full spec. Summary:
the code specifies no bond asset, no sizing basis, and no
custody/return conditions — each is an engagement term to be set in
the signed engagement. No solver may handle outside flow under this
package until those terms are set and executed.

## Gates

- Founder gate: signature on the engagement before any deployment
  work begins.
- Founder gate: merge tap for this extracted package (see
  PACKAGING.md).
- No performance promises, no invented results: any execution-quality
  measurement procedure (including how price improvement is measured)
  must be defined in the signed engagement itself.
