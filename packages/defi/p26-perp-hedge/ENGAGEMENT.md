# ENGAGEMENT — P26 Perp DEX Hedging Swarm (suite name P06)

## Status, stated plainly

- This module exists as a platform module (a Python reference build).
- **No hedging service is running for outside accounts today.**
- A buyer/counterparty is required. There is no self-serve signup,
  no standing offer of hedging as a service, and no account to open.
- Deployment happens only inside a signed engagement, per
  counterparty. Nothing is deployed for a counterparty before that
  signature.
- State: EXTERNAL (buyer/counterparty).

## Priced terms (catalogued — exact)

| Term | Value |
|---|---|
| One-time engagement | $17,500 one-time |
| Secondary service form (upkeep) | 15%–25% of build cost per year |
| Upkeep on the $17,500 build | $2,625–$4,375 per year |
| Payment-rail form | Scoped integration/deployment engagement, per counterparty |

No other price, discount, rate, or term is offered by this sheet.
The 12 bps figure in the code is an in-simulation fee on positive
realized P&L inside the reference engine; it is not an engagement
price and is not charged to anyone by this package.

## Scope template (fill in per counterparty, then sign)

1. **Counterparty:** [legal name, contact, wallet/venue accounts —
   counterparty-controlled]
2. **Engagement scope:** integration and deployment of the Perp DEX
   Hedging Swarm reference package (`packages/defi/p26-perp-hedge/`)
   for the counterparty's own accounts, limited to: [venues named],
   [asset pairs — package declares `ETH/USD`, `BTC/USD` price needs],
   [environment — Sepolia drill first, per DEPLOY.md].
3. **Deliverables:** the packaged module as tested at the merged
   commit; deployment inside the counterparty's environment per
   DEPLOY.md; a drill transcript; operator handover notes.
4. **Out of scope unless added in writing:** real-funds operation,
   mainnet use, venue adapters beyond the named venues, price-feed
   adapters beyond the stub shape in DEPLOY.md, ongoing operation of
   the swarm on the counterparty's behalf (that is the separate
   upkeep service form below, if separately signed).
5. **Upkeep service form (optional, secondary):** if separately
   signed, upkeep at 15%–25% of build cost per year (on the median
   $17,500 build: $2,625–$4,375/yr), scope and service levels to be
   written into that agreement. The percentage is set per engagement
   inside that range; no upkeep obligation exists until it is signed.
6. **Counterparty responsibilities:** venue access and accounts,
   wallet custody and keys (keys never pass to this package or its
   maintainers), funding decisions, and any regulatory obligations
   attaching to the counterparty's own trading.
7. **Gates that stay in force:** dry-run by default; isolated margin
   only; oracle staleness blocks new actions; execution paths stay
   blocked until the conversion proof step inside the engagement
   (DEPLOY.md step 8). These are properties of the delivered code,
   not optional extras.
8. **Term and signatures:** [start date], [term], [signatures].

## What is not promised

- No performance promises: no return, yield, funding-rate capture,
  Sharpe, drawdown, or profitability figure is promised or implied.
- No invented results: the reference engine's simulated numbers are
  design-validation arithmetic, not trading results. There are no
  outside-account results to report, because no hedging service is
  running for outside accounts.
- No timeline promise beyond what a signed scope states in writing.
- Nothing here is financial advice or an offer of a security.
