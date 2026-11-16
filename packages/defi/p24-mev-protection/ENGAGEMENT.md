# P24 — MEV Protection & Capture (suite P04): Engagement Sheet

## Status — read first

- The module is **not separately deployed today**.
- A **buyer/counterparty is required**. Nothing in this package is a
  self-serve product, and no deployment exists to point at.
- **Deployment happens only inside a signed engagement** on the terms
  below. Until an engagement is signed, this package is an internal
  platform module prepared for counterparty review.
- State: **EXTERNAL** (buyer/counterparty).

## What this engagement is — and is not

This is a **protection / integration engagement**. SINCOR integrates
the module's protection and detection surfaces into the
counterparty's own order flow and infrastructure, configures them
against the counterparty's environment, and rehearses them on Sepolia
before any production discussion.

**No capture claims.** This engagement sells protection and
integration work only. SINCOR's own backrun lane was separately
measured and closed; it is not part of this offer. Nothing in this
engagement promises, projects, or implies MEV capture revenue, trading
returns, or yield of any kind, and no figure in this document should
be read as one. Detection flags produced by the module are signals
used to tune protection — they are not a revenue stream and are not
sold as one.

No performance promises are made: no detection-rate guarantee, no
loss-reduction guarantee, and no execution-quality guarantee is
offered or implied. The module is a Python reference build; the
engagement delivers integration, configuration, rehearsal, and
handover of that build, as scoped below.

## Priced terms

| Item | Terms |
|---|---|
| Engagement fee | **$61,050 one-time** — scoped integration/deployment engagement, per counterparty. |
| Underlying protected RPC/stream (if elected) | **$0–$300 as listed — pass-through at cost.** The counterparty's own protected RPC/stream service, procured at cost with no markup. The module itself consumes no RPC endpoint (see DEPLOY.md); this line covers only the third-party service where the counterparty elects one. |
| Payment-rail form | Scoped integration/deployment engagement, per counterparty. |

No other fees are quoted in this sheet. Any change to scope or price
requires a written amendment signed by both parties.

## Scope template

Complete every bracketed field at signing. Work outside the completed
scope is out of engagement until amended in writing.

**Counterparty:** [legal name]
**Counterparty environment:** [chains / venues / order-flow surfaces in scope]
**Integration surfaces (select and complete):**

- [ ] Protected-order evaluation integrated into [counterparty order
  flow / routing layer]: opt-in flagging, slippage guardrail value
  [___ bps, default 100], toxic-block revert behaviour.
- [ ] Flow metering and detection configured for [pools / venues]:
  flow-gate thresholds [notional $___ / impact ___ bps, defaults
  $1,000 / 50 bps], flag review workflow with counterparty operators.
- [ ] Signer hygiene for the integration: deny-list configuration and
  a configuration audit confirming no treasury EOA appears in any
  signer/key field of the integrated configuration.
- [ ] Protected RPC/stream wiring at the counterparty layer (if
  elected): [provider/service as listed], routing of counterparty
  order flow through it, pass-through at cost per the priced terms.
- [ ] Sepolia drill per DEPLOY.md, with the drill log filed to the
  engagement record.
- [ ] Operator handover: configuration record, runbook walkthrough,
  and acceptance review with [counterparty attendees].

**Deliverables:**

1. Integrated protection/detection configuration in the counterparty's
   environment, as scoped above.
2. Configuration audit result (signer deny-list) for the integrated
   configuration.
3. Sepolia drill log: test-suite counts, configuration values,
   protection-behaviour rehearsal results.
4. Operator handover session and configuration record.

**Acceptance:** [counterparty acceptance checklist — completed at
signing; the Sepolia drill steps in DEPLOY.md are the default
checklist]

**Timeline:** [start / milestones / acceptance date — completed at signing]

## Exclusions — outside this engagement at any price quoted here

- Operating, enabling, or configuring any live order submission,
  bidding, or capture activity. The module ships no execution path;
  this is a design property, not a missing feature to be added under
  this engagement.
- Custody of counterparty keys or funds, at any point. The deny-list
  behaviour (no treasury EOA as signer) is a hard design rule of the
  module and is never worked around.
- Production deployment outside the signed scope, and any deployment
  at all before the Sepolia drill is completed and accepted.
- Performance outcomes of any kind (see "no performance promises"
  above).

## Signatures

SINCOR: ______________________________ Date: __________

Counterparty: ________________________ Date: __________
