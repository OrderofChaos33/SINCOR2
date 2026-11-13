# GENESIS RUNBOOK — RAIL EDITION — DRAFT (NOT APPROVED)

> **STATUS: DRAFT — FOR OWNER REVIEW AND APPROVAL ONLY.**
> Nothing in this document is authorized procedure until the owner signs off.
> Do not execute gate flips, rollbacks, or kill-switch trips from this draft
> without explicit owner approval of the final version.
>
> Items requiring the founder's direct action are marked **FOUNDER-GATED**.

## 1. Purpose and scope

The genesis funnel is the launch path for the SINCOR genesis cohort:

1. A visitor claims a genesis allocation at `POST /api/genesis/claim`
   (page: `/genesis`). The claim is persisted in `genesis_cohort.db` on the
   Railway `/data` volume (`src/sincor2/genesis_cohort.py`).
2. The member pays over the **x402/USDC rail** — the parked Stripe checkout path was REPLACED and is not part of this launch. The buy page calls
   `POST /rail/checkout` → the server mints a 402 x402 challenge; the buyer
   pays USDC on Base to the treasury from their own wallet; the buy page
   then calls `POST /rail/settle` → the server verifies the on-chain
   transfer (fail-closed), finalizes the payment, passes the treasury money
   gate, and fulfills the pack (intake record + confirmation email +
   vertical-agent deployment job). Each rail payment event is tagged with
   the member's genesis allocation at log time
   (`genesis_member` / `genesis_allocation` columns on `rail_events`,
   `src/sincor2/rail_routes.py`).
3. Attribution is verified in the admin rail revenue summary:
   `GET /v1/ops/rail-revenue` (header `X-Admin-Key: <ADMIN_PASSWORD>`) — the
   `genesis` key carries `attributed_revenue`, `attributed_transactions`,
   and the tagged `attributed_events`.

This runbook covers: pre-launch checklist, gate-flip (opening the funnel),
the launch-day buyer flow, first-payment verification, attribution
verification, rollback (halting new challenges), and kill-switch
(emergency halt). All sections remain DRAFT pending owner approval.

Server-side pack prices (from `PACK_SKUS`; a client-supplied price is never
trusted, and checkout refuses to mint when the x402 resource config drifts):

| pack_sku | name | price (USDC) |
|---|---|---|
| `credentialing_launch` | Credentialing Launch | 297.00 |
| `chroma_compliance` | Compliance Operations | 997.00 |
| `sadas_execution` | SADAS Execution | 2997.00 |

## 2. Pre-launch checklist

All boxes must be checked before the gate-flip (section 3):

- [ ] `main` is green on the repo CI for the rail + genesis scope
      (`tests/pytest/test_rail_routes.py` and
      `tests/pytest/test_rail_genesis_attribution.py` passing).
- [ ] Rail resources priced: `config/x402_pricing.yaml` `rail-pack-<sku>`
      amounts agree with the server-side `PACK_SKUS` table (checkout fails
      closed on drift — it will not mint a mis-priced challenge).
- [ ] Railway `/data` volume is mounted and writable (`SINCOR_DATA_DIR`
      resolves; `genesis_cohort.db` creatable) and the rail ledger DB
      (`SINCOR_REVENUE_DB`, default `data/revenue/revenue_ledger.db`) is
      writable (`rail_events` creatable).
- [ ] Coinbase Onramp is configured. **FOUNDER-GATED:** the CDP API keys
      must be created by the founder and installed through the secure env
      flow — never pasted into chat. Fail-closed note: the rail accepts
      **only USDC on Base**. Until the onramp is live, a buyer has no
      in-flow path from card/bank to USDC — the funnel must not advertise
      card payment until the onramp is confirmed working. Without the
      onramp, only buyers who already hold USDC on Base can purchase.
- [ ] Money-gate drill completed (section 5): the treasury kill switch
      provably denies rail provisioning (fail closed), with the alert
      recorded and provisioning resuming after the halt is cleared.
- [ ] Owner has confirmed the public copy on `/genesis` and the buy page
      (no aspirational claims; working-products-only rule).
      **FOUNDER-GATED** (owner approval of copy).
- [ ] Alert-contacts table (section 9) is filled in. **FOUNDER-GATED**
      (owner supplies the real names and channels — none are invented here).

## 3. Gate-flip procedure (opening the genesis funnel)

Prerequisites: every box in section 2 is checked. Execute in order; stop on
any failure.

1. Deploy the approved `main` to Railway; confirm the deploy is healthy
   (`/health` or equivalent status endpoint returns OK).
   **FOUNDER-GATED** (deploys are the founder's call).
2. Smoke-test the claim path end to end in production:
   `POST /api/genesis/claim` with a test email → expect `{"ok": true, ...}`.
3. Mint a checkout in production: `POST /rail/checkout`
   `{"pack_sku": "credentialing_launch"}` → expect HTTP 402 with
   `challenge_id`, `amount_usdc: 297.0`, and the x402 `payment` payload.
4. Run one founder-authorized payment through the full rail: pay the
   challenge in USDC on Base from a wallet, then `POST /rail/settle` with
   the `challenge_id` and `tx_hash`; confirm the event lands in
   `rail_events` with `finalized=1` and `fulfillment_status='delivered'`.
   **FOUNDER-GATED:** a real-money smoke payment requires explicit owner
   authorization (or the owner runs it themselves).
5. Verify attribution per section 6 before announcing: the test payment
   must appear under `genesis.attributed_events` with the correct
   allocation if the buyer_email is a claimed member, and must NOT appear
   there otherwise.
6. Announce the funnel open to the alert contacts (section 9).

Rollback criteria during the flip: any step fails, or attribution is wrong —
go to section 7 (rollback).

## 4. Launch-day flow (buyer's path)

1. The buyer connects their own wallet on the site (SINCOR creates no
   custodial accounts; the buyer always pays from a wallet they control).
2. The buyer picks a pack → the buy page calls `POST /rail/checkout`
   `{"pack_sku": "<sku>", "payer_wallet": "<buyer>", "buyer_email": "<optional>"}`
   → the server returns the 402 x402 challenge (`challenge_id`, payment
   payload, bind info). The price is server-derived; the page never sends
   one.
3. A buyer without USDC uses Coinbase Onramp to convert to USDC on Base
   (requires the section-2 onramp configuration to be live). The buyer pays
   the exact challenge amount in USDC to the treasury — an on-chain
   transfer from their wallet.
4. The buy page calls `POST /rail/settle`
   `{"challenge_id": "<id>", "tx_hash": "<0x…>", "payer_wallet": "<buyer>",
   "buyer_email": "<optional>"}` → the server verifies the on-chain
   transfer (fail-closed on `rpc_failed` / `tx_pending`), finalizes the
   payment, passes the treasury money gate, and fulfills: an intake record
   is created, the buyer is emailed the intake link, and the vertical-agent
   deployment job is queued.
5. Wallet-native buyers (no email): no email is sent; the intake link is
   returned in the settle response — the buyer must keep it.
6. If the `buyer_email` matches a claimed genesis member — or, with no
   email, the payer wallet was registered to a member's claim — the payment
   row is tagged with that member's allocation at log time (section 6).
   Tagging records *who* paid; it never changes the amount.

## 5. Money-gate drill (pre-launch)

Purpose: prove the treasury kill switch gates rail provisioning
(fail-closed) before any buyer touches the funnel.

1. In a non-production checkout, trip the kill switch using the canonical
   check `sincor2/agents/treasury_execution_agent.py::kill_switch_tripped`
   (default HALT file `data/TREASURY_EXEC_HALT`; missing/unreadable/
   malformed state counts as tripped — fail closed).
2. Run a rail settle through the pipeline (test harness path, no real
   funds): expect `{"ok": false, "error": "money_gate_denied"}`, the rail
   event left at `fulfillment_status='gate_denied'`, and a
   `pack_fulfillment_gate_denied` alert recorded in the ledger.
3. Clear the halt **only on explicit owner instruction** and re-run:
   expect the event to proceed to `delivered`. **FOUNDER-GATED** (clearing
   the halt is the owner's call).

## 6. Verifying the first rail payment landed

The rail ledger is the `rail_events` table in the SQLite DB at
`$SINCOR_REVENUE_DB` (default `data/revenue/revenue_ledger.db`):

```sql
SELECT event_id, pack_sku, payer_wallet, tx_hash, amount_usdc,
       finalized, fulfillment_status,
       genesis_member, genesis_allocation, created_at
FROM rail_events
ORDER BY created_at DESC
LIMIT 5;
```

Expected for the first real payment: `finalized=1`,
`fulfillment_status='delivered'`, `amount_usdc` matching the pack price
(e.g. `297.0` for `credentialing_launch`), and `tx_hash` equal to the
buyer's on-chain transfer hash.

Cross-checks:

- The platform payment log (agent burn-log JSONL) holds exactly one new
  `platform_payment` entry carrying the same `tx_hash` and amount.
- The treasury inflow log holds exactly one new `x402_payment` entry with
  `projected=false` and the same `tx_hash`.

### Attribution verification

```sql
SELECT event_id, genesis_allocation, amount_usdc, created_at
FROM rail_events
WHERE genesis_member = 1
ORDER BY created_at DESC;
```

and the admin summary:

```
GET /v1/ops/rail-revenue   (header X-Admin-Key: <ADMIN_PASSWORD>)
```

The member's payment must appear under `genesis.attributed_events` with
the correct `genesis_allocation` (the 5-digit allocation from their claim)
and the paid amount. A payment from a non-member must NOT appear there —
compare `genesis.attributed_transactions` against `transaction_count`.

## 7. Rollback procedure (halting new challenges)

Use when the funnel must be closed without an emergency (bad copy, wrong
pricing, attribution bug, deploy regression).

1. Stop new challenges: take the buy surface out of the public flow. The
   exact mechanism (feature flag, route toggle, or redeploy) is a
   **FOUNDER-GATED** owner decision to be confirmed at approval — do not
   invent one here. Verify: `POST /rail/checkout` no longer returns a 402
   challenge to the public.
2. Trip the treasury kill switch so any in-flight money path refuses
   provisioning (fail closed). Events already settled but not yet
   delivered stay non-delivered for operator retry after review. Clearing
   the halt later requires explicit owner instruction (**FOUNDER-GATED**).
3. Confirm no new `rail_events` rows are being created after the rollback
   timestamp (`ORDER BY created_at DESC`).
4. Existing cohort members keep their records — never delete
   `genesis_cohort` rows as a rollback step.
5. Verify in `GET /v1/ops/rail-revenue`: no new
   `genesis.attributed_events` after the rollback timestamp; totals stop
   growing.
6. Notify the alert contacts (section 9) that the funnel is closed and why.
7. File a follow-up: root cause + the fix branch before any re-flip.

Rollback is reversible: a re-flip follows section 3 from the top.

## 8. Kill-switch procedure (emergency halt)

Use when money is moving wrong: duplicate provisioning, attribution
corruption, suspected fraud, or a compromised key. The kill switch is
fail-closed — tripped means money paths refuse, not "try anyway".

Layer 1 — treasury/money halt (gates rail provisioning too: the settle
pipeline refuses at the money gate):

1. Trip the kill switch per
   `sincor2/agents/treasury_execution_agent.py::kill_switch_tripped`
   (default HALT file `data/TREASURY_EXEC_HALT`; missing, unreadable, or
   malformed state counts as tripped).
2. Verify the halt with a safe probe: a rail settle attempt must return
   `money_gate_denied` — use a test challenge; never invent a real payment
   to test with.
3. Notify alert contacts immediately (section 9).

Layer 2 — outbound comms halt (stops all sends/publishes):

1. Set `SINCOR_COMMS_KILL_SWITCH` to a truthy value (`1`/`true`) in the
   Railway environment and redeploy (the live-toggle mechanism is
   **FOUNDER-GATED** — owner confirms the exact mechanism at approval).
   See `src/sincor2/comms_gating.py`.

Recovery from kill-switch:

1. Root-cause the emergency and land the fix on a branch; the owner
   merges (**FOUNDER-GATED**).
2. Clear the halt (remove the HALT file; unset `SINCOR_COMMS_KILL_SWITCH`
   if engaged) — only on explicit owner instruction (**FOUNDER-GATED**).
3. Re-run the section 3 verification (steps 2–5) before announcing
   all-clear.

## 9. Alert contacts

> Placeholders — **FOUNDER-GATED:** the owner fills in real names, roles,
> and channels (phone/SMS, email, paging) at approval time. Do not invent
> entries.

| Role | Name | Channel | Notes |
|------|------|---------|-------|
| Owner / final approver | TODO-owner | TODO-owner | Sole authority for gate flips and kill-switch recovery |
| On-call engineer | TODO-owner | TODO-owner | Executes sections 3, 5, 7, 8 |
| Comms / announcements | TODO-owner | TODO-owner | Sends funnel open/close notices |
| Compliance reviewer | TODO-owner | TODO-owner | Reviews attribution + copy before any flip |

Escalation order: on-call engineer → owner. The owner is the only person
who can authorize a re-flip after a kill-switch trip.

## 10. Open questions for the owner (must resolve before approval)

1. What is the exact production mechanism for opening/closing the buy
   surface (feature flag name, route toggle, or redeploy)?
2. What is the live-toggle mechanism for `SINCOR_COMMS_KILL_SWITCH`
   (env + redeploy, or runtime override)?
3. What is the production verification step for the treasury halt
   (section 8, layer 1, step 2)?
4. Coinbase Onramp: are the CDP keys installed, and through which
   env/secret path? **FOUNDER-GATED.**
5. Who fills each row of the alert-contacts table, and what is the
   paging channel for a 3 a.m. kill-switch trip?
6. Is a real-money smoke payment authorized for the gate-flip
   verification (section 3, step 4), or test-path only?
