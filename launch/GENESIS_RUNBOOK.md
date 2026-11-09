# GENESIS RUNBOOK — RAIL EDITION — READY FOR FOUNDER APPROVAL

> **STATUS: READY FOR FOUNDER APPROVAL — NOT YET APPROVED.**
> This runbook becomes authorized procedure only when the founder signs
> the approval line below. Until then, do not execute gate flips,
> rollbacks, or kill-switch trips from this document without the
> founder's explicit instruction.
>
> Items requiring the founder's direct action are marked **FOUNDER-GATED**.
> The founder is the sole operator for this launch (section 2).

**Approval**

| | |
|---|---|
| Approved by (founder signature) | ______________________________ |
| Name (print) | ______________________________ |
| Date | ______________________________ |

## 1. Scope — what launches Monday, November 9, 2026

Launch scope was decided by the founder on 2026-11-08: **Monday is a
controlled genesis beta**, not a broad public paid launch.

What launches:

1. **Free founding cohort.** A visitor claims a genesis allocation at
   `POST /api/genesis/claim` (page: `/genesis`). Claiming is free — no
   payment is taken at claim time. The claim creates a *pending* claim
   and emails a confirmation link to the address given; the member's
   wallet binds only when that link's token is confirmed
   (`src/sincor2/genesis_cohort.py`). The token is stored hashed,
   single-use, and expires in 1 hour. Confirmed members are persisted in
   `genesis_cohort.db` under the data dir (`data_dir()` →
   `SINCOR_DATA_DIR`, `/data` in production).
2. **Genesis SBT / airdrop eligibility.** Confirmed cohort membership is
   the eligibility record for the free genesis SBT and free airdrop.
   Distribution to confirmed members is a separate founder-gated step;
   it is not automated by this runbook and no distribution is claimed
   until the founder executes it.
3. **Wallet-native throughout.** Members use their own wallets. SINCOR
   creates no custodial accounts, collects no claim passwords (the claim
   form has none), and never asks for keys.
4. **Manual onboarding.** The founder personally reviews confirmed
   claims and onboards members by hand. There is no automated funnel
   beyond claim → email confirmation.
5. **Launch gate.** The homepage gate lifts on the launch date:
   `src/sincor2/mvp_blueprints/pages.py` serves the home page once the
   UTC date reaches `SINCOR_LAUNCH_DATE` (default `2026-11-09`).

### Paid products exist in code — and are GATED, not part of Monday

The rail sells two different product types. They are not the same thing,
and public copy must not blur them:

- **Packs — one-time purchases.** `POST /rail/checkout` with
  `{"pack_sku": ...}` mints a single x402 challenge; one USDC-on-Base
  payment settles it once and fulfills once (intake record + confirmation
  email + vertical-agent deployment job; digital SKUs fulfill as a
  token-gated download instead). Server-side prices from `PACK_SKUS`
  (`src/sincor2/rail_routes.py`): `credentialing_launch` $297.00,
  `chroma_compliance` $997.00, `sadas_execution` $2,997.00,
  `webbuilder_site` $497.00, plus the digital SKUs merged from
  `revenue_orchestrator.DIGITAL_SKUS`. A client-supplied price is never
  trusted; checkout refuses to mint when the x402 resource config drifts.
- **Subscriptions — recurring, per-period re-authorization.**
  `POST /rail/checkout` with `{"subscription_plan": ..., "payer_wallet":
  ...}` mints a challenge for **one** 30-day billing period
  (`PERIOD_DAYS = 30`). x402 has no pull payments: the server can never
  charge a subscriber, so each period is a fresh challenge the subscriber
  settles explicitly; settling extends the subscription exactly one
  period (`src/sincor2/rail_subscriptions.py`). Plans (server-side canon
  in `plan_gate.PLAN_PRICE_CENTS`): Starter $297.00/period, Professional
  $997.00/period, Enterprise $2,997.00/period. One subscription row per
  wallet; reminders carry a pay link and never charge.

**Broad paid subscriptions and pack sales stay GATED after Monday**
until every item in section 10 clears. Monday's cohort launch does not
depend on them, and no paid offer is announced as open until the founder
lifts that gate.

### Out of scope for this runbook

MoebiusX signing/trading, Polyclaw, deploys of any other branch, and any
treasury movement beyond the rail's normal settle path.

## 2. Roles and contacts — sole operator

The founder is the only operator. There is no on-call rotation, no
separate engineer, and no separate comms or compliance seat for this
launch; the founder holds every role below.

| Role | Person | Channel | Notes |
|------|--------|---------|-------|
| Owner / final approver | Courtney (founder) | Founder's phone (on file) | Sole authority for gate flips, halts, and kill-switch recovery |
| Operator (executes sections 3–9) | Courtney (founder) | Founder's phone (on file) | Executes launch, monitoring, rollback |
| Comms / announcements | Courtney (founder) | Founder's own accounts | Sends launch open/close notices personally |
| Compliance reviewer | Courtney (founder) | — | Reviews copy and attribution before any flip |

Escalation order: there is no escalation above the founder. If the
founder is unreachable, the correct action is **halt and wait**
(section 6), never improvise authority.

## 3. Pre-launch checklist

All boxes must be checked before the launch gate lifts. Items marked
KNOWN-OPEN are real, named, and accepted for the free cohort launch —
they still gate the *paid* launch (section 10).

- [ ] **Genesis claim dry run on the founder's own email — FOUNDER-GATED.**
      From `/genesis`, submit a claim with the founder's own email →
      expect `{"ok": true, "status": "pending_confirmation"}` and the
      neutral "check your email" message → open the emailed confirmation
      link within 1 hour → confirm the wallet binds and the member row
      exists in `genesis_cohort.db`. Use only an email the founder
      controls. (This is the true end-to-end claim proof; nothing else
      substitutes for it.)
- [ ] **Agent card live at 42 skills.**
      `GET https://getsincor.com/.well-known/agent-card.json` returns the
      canonical card (served by `src/sincor2/a2a_integration.py`) and the
      skills list counts **42** (catalog 18 → 42, PR #630).
- [ ] **Launch date correct.** Production `SINCOR_LAUNCH_DATE` is unset
      (default `2026-11-09`) or explicitly `2026-11-09`; the homepage
      gate behavior matches section 1, item 5.
- [ ] **Service healthy and always-on.** Railway project
      `harmonious-nourishment`, service `web` (getsincor.com): latest
      deployment ACTIVE, Serverless **disabled** (always-on, 1 replica),
      `/health` or equivalent status returns OK.
- [ ] **No unnecessary deploys.** Until the Railway volume lands
      (KNOWN-OPEN below), **any deploy wipes the board/pool counters and
      every other `/data` file, including `genesis_cohort.db`**. Record
      the current cohort count and pool/board counters before any
      deploy, and treat a launch-week deploy as a data-loss event, not
      a routine step.
- [ ] **Liveness loop running.** The marketplace liveness runner (2-minute
      cron against https://getsincor.com) is completing passes with
      exit 0; recent commits/reveals or rotation-cap rejections are both
      evidence the loop and the marketplace are alive. A nonzero exit or
      exception is a stop-and-investigate before launch.
- [ ] **Public copy confirmed — FOUNDER-GATED.** `/genesis` and the
      homepage state the scope in section 1 exactly: free cohort,
      wallet-native, manual onboarding; no paid offer presented as open;
      working-products-only rule.
- [ ] **Rail prices agree.** `config/x402_pricing.yaml` `rail-pack-<sku>`
      and `rail-sub-<plan>` amounts agree with `PACK_SKUS` / `SUB_PLANS`
      (checkout fails closed on drift — it will not mint a mis-priced
      challenge). Checked now so the later paid gate is not blocked on it.

KNOWN-OPEN items (accepted for Monday's free cohort; each one gates paid):

- [ ] **KNOWN-OPEN — `/api/price/official` price claim.** The endpoint
      (`src/sincor2/app.py`) still serves the $0.15 / $150M FDV claim and
      the same claim class appears in other modules. The purge is built
      on a local branch and is **not merged or deployed**. Do not cite
      this endpoint or figure in launch copy.
- [ ] **KNOWN-OPEN — no Railway volume.** No volume is mounted; `/data`
      is ephemeral container storage. Railway support ticket ref
      **7cc5feea** (public Central Station thread, filed 2026-11-08) is
      awaiting Railway's response. Consequence: section 3's no-deploy
      rule and section 7's rollback warning.
- [ ] **KNOWN-OPEN — Coinbase Onramp not configured.** The rail accepts
      **only USDC on Base**; the buy surface reports
      `onramp_configured: false` (`src/sincor2/blueprints/rail_pay.py`,
      from `sincor2.onramp.is_configured()`). Monday's cohort needs no
      onramp (claims are free, members are wallet-native). Paid launch
      copy must not advertise card/bank payment until the onramp is
      configured and verified. **FOUNDER-GATED:** CDP keys are created by
      the founder and installed through the secure env flow — never
      pasted into chat.
- [ ] **KNOWN-OPEN — sanctions snapshot staleness.** Paid-path sanctions
      screening warns `Verdict.STALE` against the vendored snapshot; a
      refreshed snapshot is built on a local branch, not deployed. This
      gates paid (section 10), not the free claim path.

## 4. Launch-day procedure (Monday, November 9, 2026)

Execute in order; stop on any failure and go to section 6 or 7.

1. Re-run section 3 top to bottom. Any unchecked box that is not a
   named KNOWN-OPEN item stops the launch.
2. Confirm the gate lifted: `GET https://getsincor.com/` serves the
   home page (not the genesis gate page) once the UTC date reaches the
   launch date.
3. Confirm `/genesis` loads and the claim form renders with no password
   field.
4. Watch the first real claims: each successful claim returns the
   neutral pending-confirmation response; members appear in
   `genesis_cohort.db` only after they confirm from their email link.
   A claim that binds without an email confirmation is a halt event
   (section 6) — that binding path is exactly what the confirmation
   flow exists to prevent.
5. Onboard manually: the founder reviews confirmed members and contacts
   them personally. No bulk automated outreach on launch day.
6. Announce from the founder's own accounts, in the founder's words,
   matching section 1 scope exactly. **FOUNDER-GATED** (the founder
   posts; nothing is auto-posted).
7. Log the day in section 11's table as events happen.

## 5. Launch-day monitoring

Checked by the founder at least hourly on launch day:

- **Cohort growth:** confirmed-member count in `genesis_cohort.db`
  rising only via email-confirmed claims; pending claims expiring
  unconfirmed after 1 hour is normal.
- **Marketplace activity:** liveness passes exit 0; board counters
  (tasks / settled / pool) consistent with the recorded pre-launch
  numbers — a sudden reset to near-zero means a deploy happened and
  `/data` was wiped (section 7 warning).
- **Rail summary (watch-only on Monday):**
  `GET /v1/ops/rail-revenue` (header `X-Admin-Key: <ADMIN_PASSWORD>`).
  No paid campaign runs Monday, so new `rail_events` rows are
  unexpected; any that appear get verified per section 10's annex
  before being treated as revenue.
- **Errors:** Railway deployment logs for `web` show no new 500s on
  `/genesis`, `/api/genesis/claim`, or the confirm path.
- **Support thread:** Railway ticket ref 7cc5feea checked for a reply;
  a reply that enables the volume attach changes section 3's no-deploy
  posture only after the attach is verified (section 9).

## 6. Halt procedure (pause without emergency)

Use when the launch must pause but money is not moving wrong: bad copy,
a claim-flow defect, attribution errors, or a deploy regression.

1. **Stop the inflow manually.** The founder stops announcing and stops
   onboarding. There is **no dedicated claim feature flag in the code on
   this commit** — this runbook does not invent one. New claims are
   stopped mechanically only by the rollback in section 7 (redeploy of
   the previous deployment) or by a founder-approved fix deploy, and
   both are deploy events with the `/data` warning attached.
2. **Trip the comms kill switch** to stop gated outbound sends:
   `SINCOR_COMMS_KILL_SWITCH` truthy (`1`/`true`). The gate is checked
   live on every call and never cached
   (`src/sincor2/comms_gating.py`); setting it in the Railway
   environment takes effect on the redeploy Railway runs for the env
   change. **FOUNDER-GATED.**
3. **Trip the treasury kill switch** (section 8, layer 1) if any paid
   path might be touched. Fail closed is the default posture.
4. **Preserve state.** Never delete `genesis_cohort` rows as a halt
   step. Record the cohort count, the board/pool counters, and the
   halt timestamp before touching anything else.
5. **Verify the halt:** the claim page/announcement surface the founder
   controls no longer invites claims; `GET /v1/ops/rail-revenue` shows
   no new events after the halt timestamp.
6. **Say so plainly.** The founder posts the pause and the reason from
   his own accounts. Silence is not a halt state.

## 7. Rollback procedure (Railway redeploy of the previous deployment)

Use to return the service to the last known-good deployment. This is
the only rollback mechanism on the current stack — there is no
database snapshot restore, because there is no volume yet.

> **⚠ DATA WARNING.** `/data` is ephemeral (no volume; ticket ref
> 7cc5feea pending). **A rollback redeploy wipes `/data` exactly like
> any other deploy**: board/pool counters reset, and `genesis_cohort.db`
> is lost with them. Before rolling back, record the cohort count and
> counters (section 6, step 4) and accept the loss in writing in
> section 11 — or do not roll back. Never roll back casually.

1. Open Railway → project `harmonious-nourishment` → service `web` →
   Deployments. **FOUNDER-GATED** (deploys are the founder's taps).
2. Identify the last known-good deployment (the ACTIVE deployment from
   before the bad change) and select **Redeploy** on it.
3. Wait for the new deployment to reach ACTIVE; Serverless must still
   show **disabled** (always-on). If Serverless shows enabled, fix that
   setting before proceeding — scale-to-zero makes the wipe worse.
4. Verify: `/health` OK, `GET /` and `/genesis` serve the expected
   pages, the agent card returns 42 skills.
5. Verify state with open eyes: board/pool counters and cohort count
   will read as fresh/wiped. Record the post-rollback numbers in
   section 11 next to the pre-rollback ones.
6. Re-run the section 3 dry run (founder email) before re-announcing
   anything.
7. File the follow-up: root cause + fix branch before any re-launch.

## 8. Kill-switch procedure (emergency halt)

Use when money is moving wrong: duplicate provisioning, attribution
corruption, suspected fraud, or a compromised key. Tripped means money
paths refuse — fail closed, never "try anyway".

Layer 1 — treasury/money halt (the rail settle pipeline refuses at the
money gate when tripped):

1. Trip the kill switch per
   `src/sincor2/agents/treasury_execution_agent.py::kill_switch_tripped`.
   The HALT file defaults to `data/TREASURY_EXEC_HALT` (override:
   `TREASURY_EXEC_HALT_FILE`). Fail-closed by construction: a missing,
   unreadable, malformed, or marker-less halt state counts as tripped.
2. Verify with a safe probe: a rail settle through the test harness
   (no real funds) must return
   `{"ok": false, "error": "money_gate_denied"}` and leave the event at
   `fulfillment_status='gate_denied'` with a
   `pack_fulfillment_gate_denied` alert in the ledger.
3. The founder notifies anyone affected personally and immediately.
   There is no separate alert list (section 2).

Layer 2 — outbound comms halt: trip `SINCOR_COMMS_KILL_SWITCH` per
section 6, step 2.

Layer 3 — service halt: if the first two layers are not enough, roll
back per section 7, accepting the `/data` warning, or take the service
down in Railway. **FOUNDER-GATED.**

## 9. Recovery and re-opening

1. Root-cause the halt and land the fix on a branch; the founder
   reviews and merges. **FOUNDER-GATED.**
2. Clear halts **only on the founder's explicit instruction**: remove
   the treasury HALT state; unset `SINCOR_COMMS_KILL_SWITCH` if engaged.
   **FOUNDER-GATED.**
3. If a deploy is part of recovery, the `/data` warning applies in
   full: record counts first, expect a wipe, verify after.
4. If the Railway volume reply has arrived (ticket ref 7cc5feea):
   attach the volume at `/data` to service `web`, confirm the mount in
   the deployment, then run the survival test — one controlled deploy,
   after which the cohort count and board/pool counters must persist
   unchanged, and verify `SINCOR_REVENUE_DB` resolves under `/data`.
   Only a passed survival test retires the no-deploy rule.
5. Re-run section 3 in full, then section 4 from the top, before
   announcing all-clear.

## 10. Paid-launch gates (post-genesis — all must clear)

Broad paid subscriptions/packs open only when **every** item below is
verified, in this order. Until then the rail stays watch-only.

1. **Cancellation fix merged and deployed.** Cancel-mid-period must
   honor the Terms: access until period end, renewal stopped,
   outstanding renewal challenges voided (the W-106 protection). The
   fix is built on a local branch (`cancel_pending` behavior); it is
   not live until merged and deployed.
2. **OFAC refresh deployed.** The refreshed sanctions snapshot replaces
   the stale vendored one in production and paid-path screening runs
   without `Verdict.STALE` warnings. Never weaken sanctions enforcement
   to get tests green.
3. **Revenue-ledger `/data` path verified.** With a volume mounted,
   `SINCOR_REVENUE_DB` resolves under `/data` in production (the code
   default is the relative `data/revenue/revenue_ledger.db` when the env
   is unset — a mounted volume alone does not prove the ledger lands on
   it) and a test event survives a redeploy.
4. **First complete paid purchase, end to end.** One founder-authorized
   real purchase through the full rail — checkout → USDC payment on
   Base → settle → `finalized=1`, `fulfillment_status='delivered'`,
   verified per the annex below. **FOUNDER-GATED** (real money; the
   founder authorizes or runs it).
5. **This runbook approved and the rehearsal in section 12 performed**
   (approval = the signature block at the top; rehearsal = actually
   done and logged, not just read).
6. **Release evidence for the exact release SHA** being deployed
   exists and names its test numbers.
7. **Messaging consistency.** README and public surfaces agree with the
   live catalog (42 skills) and with section 1's pack/subscription
   descriptions; the "Rug-Proof" wording is replaced with narrow,
   verifiable claims; the `/api/price/official` claim (KNOWN-OPEN,
   section 3) is resolved, not just flagged.

### Annex — first paid purchase verification (gate 4 detail)

Money-gate drill first (non-production, no real funds): trip the
treasury kill switch (section 8, layer 1) → a test settle must return
`money_gate_denied`, leave the event `gate_denied`, and record the
alert → clear the halt only on the founder's instruction → re-run and
expect delivery.

Gate-flip smoke, founder-authorized: `POST /rail/checkout`
`{"pack_sku": "credentialing_launch"}` → HTTP 402 with `challenge_id`
and `amount_usdc: 297.0` → pay in USDC on Base from a wallet →
`POST /rail/settle` with `challenge_id` and `tx_hash` → the event lands
in `rail_events` with `finalized=1` and
`fulfillment_status='delivered'`.

Verify in the rail ledger (`$SINCOR_REVENUE_DB`):

```sql
SELECT event_id, pack_sku, payer_wallet, tx_hash, amount_usdc,
       finalized, fulfillment_status,
       genesis_member, genesis_allocation, created_at
FROM rail_events
ORDER BY created_at DESC
LIMIT 5;
```

Expected: `amount_usdc` matches the pack price, `tx_hash` equals the
buyer's on-chain transfer hash, exactly one matching
`platform_payment` entry in the platform payment log and exactly one
`x402_payment` entry with `projected=false` in the treasury inflow log.

Attribution: if the buyer is a confirmed genesis member, the payment
must appear under `genesis.attributed_events` in
`GET /v1/ops/rail-revenue` with the member's correct allocation; a
non-member payment must NOT appear there. Tagging records *who* paid;
it never changes the amount.

Buyer path, for support answers: buyer connects their own wallet →
picks a pack or plan → the page calls `/rail/checkout` (price is
server-derived; the page never sends one) → buyer pays the exact
challenge amount in USDC on Base to the treasury → the page calls
`/rail/settle` → fulfillment follows (pack: section 1; subscription:
one period extended). Wallet-native buyers without email get the intake
link in the settle response and must keep it — no email copy exists.

## 11. Launch log

| Time (CST) | Event | Numbers / evidence | By |
|---|---|---|---|
| | Pre-launch counts recorded (cohort / tasks / settled / pool) | | Founder |
| | Gate lifted (section 4 complete) | | Founder |
| | First confirmed member | | Founder |
| | Halt / rollback (if any) — cause + pre/post numbers | | Founder |
| | Railway ticket 7cc5feea reply (if any) + action taken | | Founder |

## 12. Rehearsal checklist (tabletop — ~15 minutes)

**This rehearsal has NOT been performed yet.** It is a paid-launch gate
(section 10, item 5) and a confidence check for Monday. It is a tabletop
walkthrough: the founder talks through each step against the live
screens and names the exact button, file, or command — no real halt is
tripped and no deploy is made during the rehearsal.

Minutes 0–3 — Orient:
- [ ] Open this runbook, Railway (project `harmonious-nourishment`,
      service `web`, Deployments list), and getsincor.com in tabs.
- [ ] Read section 1 aloud: state what is launching and what is gated.
- [ ] Point at the approval block: is it signed? (If not, name that as
      the open item, not a surprise.)

Minutes 3–6 — Verify the checks:
- [ ] Walk section 3 item by item; for each, say where the evidence
      lives (which page, response, or DB).
- [ ] Name the three KNOWN-OPEN items and why each is accepted for
      Monday but gates paid.

Minutes 6–10 — Halt and rollback, talked through:
- [ ] Talk section 6 in order. Say out loud: "There is no claim feature
      flag" — and name the mechanical halt options that do exist.
- [ ] Talk section 7 in order. Find the previous deployment's Redeploy
      action without clicking it.
- [ ] Recite the `/data` warning from memory: what is wiped, what gets
      recorded first, ticket ref **7cc5feea**.

Minutes 10–13 — Kill switch, talked through:
- [ ] Name the treasury HALT file's default path and the env override.
- [ ] Name the expected probe result: `money_gate_denied`,
      `fulfillment_status='gate_denied'`.
- [ ] Name the comms kill-switch variable and the fact that the gate is
      checked live on every call.

Minutes 13–15 — Recovery and close:
- [ ] Talk section 9 in order, including the volume survival test.
- [ ] Name the seven paid gates in section 10 without looking.
- [ ] Log the rehearsal here when actually done:
      date __________ · start ______ · end ______ · gaps found:
      ____________________________________________
