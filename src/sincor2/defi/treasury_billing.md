# P41 — DAO Treasury Management: engagement & invoicing process

**SKU:** P41 (module P21, `treasury_dao.py`) — DAO Treasury Management, the 4-agent treasury swarm service. **Terms:** 1.0% of AUM per year + 10% performance fee above the high-water mark (catalog list terms, 2026-11-16 research). **Billing layer:** `treasury_billing.py` (built 2026-11-23 on `xioix/autonomous-p41-treasury-invoicing`).

## Why there is no PayPal link for this SKU

The fee is a percentage of a client's AUM, so the amount owed does not exist until AUM and the period are known. A fixed-price PayPal link cannot express it, and none was created — inventing a fixed amount would misprice every engagement. Collection runs on the invoicing process below; for engagements at this size the house rail is invoice with ACH/wire milestone terms (the same rail the Master Orchestration Directive sets for >$100K paths), with the computed invoice amount as the payable figure.

## Engagement process

1. **Contracting:** the engagement record opens with the client's identity, the fee terms above (stated in the engagement letter — the terms text is a founder/legal step before the first client signs), the AUM reporting cadence, and the invoicing cadence (monthly default). `BillingBook.open_engagement(client_id, at, aum_cents)` records the open with the starting AUM as the initial high-water mark.
2. **AUM reporting:** at the agreed cadence the client's reported AUM is recorded with `snapshot_aum(...)`. Each snapshot settles management accrual at the previous AUM up to the snapshot time first, so every interval is charged at the AUM actually in force. Snapshots are records of client-reported figures; verifying a client's AUM against their treasury wallets is an operator step at onboarding and at each invoice.
3. **Invoicing (monthly):** `generate_invoice(client_id, at)` crystallizes the period: management fee accrued pro-rata, performance fee on any gain above the high-water mark (the mark moves only here, never on a snapshot), accrual reset. The invoice record is content-addressed — its ID is the hash of its figures, so the figure set a client is sent is exactly the figure set on record.
4. **Issue & collection:** the operator issues the invoice (status DRAFT → ISSUED) and sends it through the client's channel with ACH/wire terms. Payment marks it PAID; an unpaid issued invoice past its terms moves to OVERDUE and then PAID on settlement. This code sends nothing and collects nothing — those are operator actions, on the record.
5. **Statements:** a zero-fee period still generates a zero invoice — the client's statement of account for the period, same content-addressed record.

## Worked example (the test suite's numbers)

$1,000,000 AUM held flat for a year → management fee $10,000. AUM rising to $1,200,000 inside the period → performance fee 10% × $200,000 = $20,000 at crystallization, and the high-water mark becomes $1,200,000: a later dip to $1,100,000 and recovery to $1,200,000 earns no further performance fee; only growth past $1,200,000 does.

## Still owed before the first client (named, not hidden)

- Founder/legal review of the engagement-letter terms text.
- A client. The swarm, the billing layer, and the invoicing process are ready; the engagement is the missing piece.
