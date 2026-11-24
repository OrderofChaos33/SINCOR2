# P45 — Agent-Managed Portfolio: the offer, productized

**SKU:** P45 (module P25) — Agent-Managed Portfolio. **Catalog price:** 0.6% of AUM per year. **Status:** the portfolio engine (allocator, rebalancer, risk, fee ledger) was built and tested; the client-facing layer was missing. Built 2026-11-23 on `xioix/autonomous-p45-portfolio-productization`: `statements.py` (this package) + this document.

## ⚠ Founder ratification item — the fee rate disagrees with itself

The catalog sells this SKU at **0.6% of AUM per year (60 bps)**. The code accrues at **10 bps p.a.** (`PARAMS["fee_bps"] = 10`; the package docstring and `fees.py` both say 10 bps — the code is internally consistent, it just doesn't match the catalog). One of the two is wrong, and which one is a pricing decision only the founder can make. **Nothing in this build changed the rate.** What this build does guarantee: statements and invoices carry the rate actually applied (`fee_bps` from PARAMS), so no client can ever be shown a rate the ledger doesn't charge. Ratify one number before the first client signs; if the answer is 60, the change is one PARAMS line plus a test update — after ratification, not before.

## The offer

A client's portfolio is managed by the P25 layer: swarm TOA signals become per-user target portfolios under a 0.28 blended risk budget, a 5% USDC cash floor, and daily 2.5%-drift rebalancing. Advisory + accounting only — the layer never holds user keys and never broadcasts (its standing posture, unchanged). The management fee accrues per tick on AUM, denominated in AXM (default) or SINC (chosen at onboarding, reversible quarterly per the fee module's policy), and settles to treasury in ≥ $10-equivalent batches.

## What the client receives (the part that was missing)

- **Statements:** `StatementBook.statement(user_id, start, end)` — per-period: accrual count, fees charged (token wei + USD), settlements in the period, and the outstanding balance, with the applied `fee_bps` printed on the statement.
- **Invoices:** `generate_invoice(...)` issues the period's invoice record — content-addressed ID, overlapping windows refused (a period can never be billed twice), lifecycle DRAFT → ISSUED → PAID / OVERDUE tracked on the record. The operator sends the invoice through the client's channel; this code sends nothing.

## Contracting process

1. **Onboarding:** client agreement names the fee rate (see the ratification item above — the agreement must state the ratified number), the fee denomination (AXM default / SINC), the statement cadence (monthly default), and the advisory-only posture in plain words: SINCOR computes plans and accounting; execution stays with the client's own keys/venues.
2. **Operation:** signals → plans → rebalances run on the module's cadences; every fee tick accrues through `StatementBook.accrue(...)` so the journal is complete.
3. **Monthly:** the operator issues the statement + invoice for the period; settlement batches move through the FeeLedger's existing ≥ $10 path to treasury.
4. **Quarterly:** denomination changes (AXM ↔ SINC) are taken at quarter boundaries per the fee module's policy.

## Still owed before the first client (named, not hidden)

- **The rate ratification above** — the one real blocker.
- Founder/legal review of the client-agreement terms text.
- A client. The engine, the ledger, the statements, and the process are ready.
