# ENGAGEMENT — P21 Yield Aggregator Vault (suite name P01)

Scope template for a counterparty engagement. This sheet states the
priced terms and the conditions under which work happens. It is not
an offer to sell a deployed product.

## Status — read first

- The module is **not separately deployed or sold today**. It exists
  as a module inside the SINCOR platform; this package is its
  standalone extraction, prepared for engagement use.
- A **buyer/counterparty is required**. Nothing is delivered,
  integrated, or deployed without one.
- Deployment happens **only inside a signed engagement**, on the
  counterparty's infrastructure and accounts, per the drill in
  `DEPLOY.md`.
- State: **EXTERNAL**.

## What the module does

Routes deposits across yield sources inside the SINCOR platform. The
allocator builds risk-capped rebalance plans across registered
strategies (currently Morpho Gauntlet USDC Prime and Aave v3 USDC
supply on Base, plus a USDC cash reserve; two further registered
sources ship disabled). Default mode is dry-run planning; in
live-intent mode it emits intent payloads for an external signer —
it never signs or broadcasts itself.

## Priced terms

| Term | Value |
|---|---|
| One-time engagement price | **$120,000** |
| Payment-rail form | Scoped integration/deployment engagement, per counterparty |
| Performance fee (secondary service form) | **10%** of positive gross yield |
| Management fee | **0%** — no charge on assets under management |

Fee accounting for these terms is implemented in
`src/p21_yield_aggregator/fees.py` and tested in `tests/test_fees.py`.
Note: the allocator's internal 10 bps (0.10%) treasury fee concept
(`fee_to_treasury_bps` on plans) is a separate platform mechanism and
is not the engagement performance fee; whether it applies inside an
engagement is a term to be settled in the signed agreement, not
assumed from this sheet.

## Scope template (complete per counterparty)

1. **Parties**: SINCOR and [counterparty legal name].
2. **Scope of work**: scoped integration/deployment engagement —
   configuration of the allocator to the counterparty's agreed yield
   sources and treasury, the Sepolia drill in `DEPLOY.md`, and
   handover of the configured package on counterparty infrastructure.
3. **Out of scope**: custody of counterparty funds or keys; signing
   or broadcasting transactions; operation of the allocator after
   handover unless a separate service agreement is signed; any yield
   source not named in the signed scope.
4. **Counterparty provides**: infrastructure and accounts, an
   external signer/broadcaster, treasury and vault addresses, and a
   named technical contact.
5. **Acceptance**: package test suite green in the counterparty
   environment; dry-run plan verified against agreed risk caps;
   drill log recorded per `DEPLOY.md`.
6. **Fees**: per the priced terms above; performance fee calculated
   per `fees.py` on positive gross yield for each agreed period.

## No promises

No performance promises are made. Strategy APR figures in the code
are point-in-time estimates used for allocation weighting; they are
not projections, guarantees, or results. No historical results are
claimed for this module as a counterparty service, because it has
not been operated as one.
