# ENGAGEMENT.md — P25 DeFi Risk Mutual (PACK-4)

Engagement sheet and scope template for a counterparty engagement on the
P25 (suite P05) DeFi Risk Mutual module.

**Plain statements, up front:**

- **No mutual is capitalized or open today.** The module exists as a
  platform module and as this standalone package; it holds no funds and
  has written no cover.
- **A buyer/counterparty is required.** State: EXTERNAL. Nothing happens
  without a counterparty who signs an engagement and funds the
  capitalization in CAPITALIZATION.md.
- **Deployment happens only inside a signed engagement**, on the
  counterparty's infrastructure and accounts, per DEPLOY.md.
- This sheet makes **no coverage promises** and claims **no results**.
  Cover terms exist only once an engagement is signed, capitalization is
  in place, and the live release has been given under that engagement.

## 1. Priced terms (exact)

| Term | Value |
|---|---|
| One-time engagement | **$36,250** (one-time) |
| Secondary service form | Cover premium **2.5%–3.25% of cover amount per year**, set per engagement within that band |
| Payment-rail form | Scoped integration/deployment engagement, priced per counterparty |

Notes on the premium term: the 2.5%–3.25% band is the commercial premium
term of the engagement. The module's internal reference pricing is a
separate mechanism — a tiered schedule of 100/200/400 bps (1%/2%/4%) of
cover per year from the risk score, capped at 1,500 bps — used for
simulation and drill purposes. The engagement must state which schedule
governs billed premiums before any cover is written; this sheet does not
silently equate the two.

The module also forwards 25 bps of every premium to the treasury address
at collection (`FEE_BPS`); the engagement must name that address.

## 2. Scope template (fill per counterparty)

**Counterparty:** _[legal name]_ · **Engagement date:** _[date]_ ·
**Designated environment:** _[counterparty infrastructure]_

In scope (default template — strike what does not apply):

1. Delivery of this package (module, tests, docs) into the
   counterparty's environment, with the package suite green (56 tests).
2. Configuration per DEPLOY.md §2: treasury address; dry-run posture.
3. Sepolia drill per DEPLOY.md §4, with the drill transcript filed as
   pre-deployment evidence.
4. Capitalization plan per CAPITALIZATION.md: amount ≥ 130% of the
   initial cover schedule, plus every "engagement term to be set" item
   in that spec resolved in writing.
5. Assessor panel setup: allowlist, bond asset/units, voting weights
   sufficient for the > 5× cover quorum rule.
6. Release procedure: who gives the auditor + actuarial + founder
   release that unblocks underwriting, and on what evidence.
7. If cover is written under the engagement: cover schedule (protocols,
   amounts, durations), premium schedule per §1, claim-window (30 days)
   and review (14 days) operations, governance and 48 h timelock
   operation.

Out of scope unless separately written in:

- On-chain contract development or deployment (the module is an
  off-chain reference build with no broadcast path).
- Custody of counterparty funds or keys.
- Cover for protocols, amounts, or durations outside the signed cover
  schedule.
- Any representation that cover is available before §3's conditions
  are all met.

## 3. Conditions before any cover could be written

All of: (a) this engagement signed; (b) capitalization delivered per
CAPITALIZATION.md §5; (c) assessor panel appointed; (d) treasury
address confirmed; (e) live release given under the engagement's
release procedure. Until all five hold, the correct description of the
mutual is: **not capitalized, not open, no cover available.**

## 4. Capitalization requirements spec

The full spec is in **CAPITALIZATION.md** and forms part of this
engagement sheet. Summary: the code enforces exactly one capitalization
rule — reserves ≥ 130% of outstanding cover at every underwrite and
redemption — plus a default-blocked live gate. The code's named
$200 `MIN_CAPITAL_WEI` constant is not enforced, and minimum
capitalization, concentration limits, bond denominations, custody, and
top-up triggers are not specified in code; each is an engagement term
to be set in writing under CAPITALIZATION.md §§2–3.
