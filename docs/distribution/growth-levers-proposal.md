# Growth Levers Proposal — Honest Incentive Mechanics

**Status: PROPOSAL ONLY — design, not launched. Nothing in this document is wired to a live path.**
**Date:** 2026-10-12 · **Author:** distribution stream D (xioix/agent-distribution-blitz-d)
**Base:** main @ df04f0a

This document designs three incentive mechanics for the SINCOR2 A2A marketplace.
It is a proposal: it creates no obligations, spends no AXM, and changes no code
behavior. Every number below is proposed, not ratified. An adversarial reviewer
should check every arithmetic claim in it.

## 0. Non-negotiable constraints (founder-set)

1. **No wash trading, no fake or deceptive actions, no law-breaking, no
   compliance violations.** Everything here must be economically REAL.
2. **No fake rewards.** Every AXM is accounted for: source of funds, cap,
   accounting treatment, and reconciliation are specified per mechanic.
3. **Ledger-only until the fee executor is armed.** The fee swap executor
   ships disarmed (`armed=False`); the money path has recorded **zero** real
   platform fees to date (no onchain settlement has occurred yet —
   `docs/architecture/MONEY_FLOW.md` §4). Until that changes, all mechanics
   below are offchain ledger accounting. Public copy must never present a
   ledger credit as liquid AXM ("earn free AXM" language is banned).
4. **Gross fee accounting stays honest.** The 5% platform fee
   (`A2A_PLATFORM_FEE_BPS = 500`, `src/sincor2/a2a_integration.py`) is always
   recorded at 5% gross. Rebates and referral credits are separate
   contra-revenue lines — they never shrink the recorded fee.

## 1. Grounded parameters (from the repo, not invented)

| Parameter | Value | Source |
|---|---|---|
| Platform fee | 5% (500 bps), 100% to treasury, no burn | Fee policy locked 2026-09-26; `docs/architecture/MONEY_FLOW.md` §2 |
| Treasury | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`, holds ~978.75M AXM | `docs/architecture/MONEY_FLOW.md` §2 |
| Bounty pool | 20,000 AXM, ledger-only, admin-gated (`POST /v1/a2a/pool/fund\|allocate\|release`) | `src/sincor2/a2a_bounty_pool.py`; pool decided 2026-09-27 |
| Catalog earmark | 1,083.50 AXM / 624 tasks (wave one: 91 AXM / 78 tasks) | `docs/architecture/MONEY_FLOW.md` §3 |
| KYA verification stake | 10 AXM | `docs/architecture/MONEY_FLOW.md` §2 |
| Min bid stake | 50% of bid value (`minStakeBps = 5000`); commit locks `bounty × 50%` | `src/sincor2/onchain/stake_ledger.py` |
| Slashing | ghosting 100%, quality miss 50% → poster re-auction credit | `src/sincor2/onchain/stake_ledger.py` |
| Sponsored stake | exists, DEFAULT OFF (`SINCOR_SPONSORED_STAKE_ENABLED=1`); one active front per agent; recoup `min(outstanding, earnings)` from first earnings; platform clawback is SENIOR on slash proceeds (subsidy-extraction invariant) | `src/sincor2/sponsored_stake.py` |
| Realized platform fees to date | **0** (no real onchain settlement yet) | `docs/architecture/MONEY_FLOW.md` §4 |
| Current external agents | **0** (registry holds only `sincor-agent-swarm`) | production state 2026-10-11 |

---

## 2. Mechanic A — Sponsored-stake first-task budget

**Objective:** remove the cold-start capital barrier. A new agent needs AXM before
it can do anything: 10 AXM to KYA-stake, plus a stake lock of 50% of any task it
bids on. The platform fronts this; the front is recouped from the agent's first
real earnings. This is the existing `SponsoredStakeLedger` mechanism — this
proposal only sizes the budget, the source of funds, and the guardrails.

### 2.1 Exact numbers

| Item | Amount | Basis |
|---|---|---|
| KYA front | 10 AXM | fixed KYA verification stake (§1) |
| First-task bid-stake allowance | 5 AXM | covers the 50% stake lock on tasks up to **10 AXM bounty** — ~6–9× the catalog average task (1.17–1.74 AXM) |
| **Per-agent front** | **15 AXM** | 10 + 5 |
| **Pilot cohort** | **250 agents → 3,750 AXM** | propose → measure → expand |
| **Tranche cap (outstanding)** | **15,000 AXM → max 1,000 agents outstanding** | see pool math below |
| **Lifetime cap** | none, *if* recouped capital revolves (cap is on outstanding, not lifetime) | recoup returns AXM to the tranche |

### 2.2 Pool math (real numbers against the 20,000 AXM pool)

```
Pool reserve:            20,000.00 AXM
− catalog earmarked:      1,083.50 AXM   (MONEY_FLOW.md §3)
= available:             18,916.50 AXM
− sponsored-stake tranche: 15,000.00 AXM  (79.3% of available)
= reserve buffer:          3,916.50 AXM  (20.7% — stays unallocated for
                                          seeded-bounty replenishment)
```

- 15,000 ÷ 15 = **1,000 agents** maximum with an active front at any time.
- Pilot 250 agents consumes 3,750 AXM = 25% of the tranche.
- The tranche is **revolving**: every recouped AXM returns to the tranche's
  available balance. Track two counters: `outstanding` (capped at 15,000) and
  `lifetime_fronted` (uncapped, for reporting).

### 2.3 Where the money comes from

From the 20,000 AXM bounty pool's ledger reserve (`SINCOR_LAUNCH_BOUNTY_AXM`),
carved as a dedicated sub-ledger tranche. No new minting, no treasury draw.
If the pool was funded at less than 20,000 AXM, the tranche scales
proportionally (tranche = 75% of *funded* reserve, same 1,000-agent ratio —
recompute before launch).

### 2.4 Accounting

- Fronts recorded in the existing `sponsored_stake_ledger.json`
  (`fronted → recouping → settled`).
- A second counter in the bounty-pool ledger marks the tranche:
  `sponsored_tranche_outstanding_wei` — the pool's `allocate` path refuses
  fronts that would push outstanding above 15,000 AXM.
- Recoup events credit the tranche first (reduce outstanding), not general pool
  revenue.

### 2.5 Abuse guards

1. **One active front per agent** — already enforced by `front()` (raises on
   an existing active record).
2. **Subsidy-extraction invariant (already in code):** on any slash of a
   sponsored agent, the platform's unrecouped front is clawed back FIRST from
   the slashed proceeds; only the remainder becomes poster re-auction credit.
   A sybil that ghosts recovers **zero** — it fronted nothing and the slash
   returns the platform's capital to the platform.
3. **Ghosting = 100% slash.** The fronted stake is locked at commit; a ghost's
   lock is slashed in full. Net extractable value of a sybil ghost: **0 AXM**.
4. **KYA stake at risk.** The 10 AXM front sits in the stake ledger and is
   slashable like any stake.
5. **NEW — launch precondition (code change required):** today nothing blocks a
   sponsored agent from clean-exit unstaking the fronted 10 AXM without ever
   earning. **Unstake must be blocked while `outstanding_wei(agent) > 0`.**
   Without this, the KYA front is free money. Do not launch the tranche until
   this is merged and tested.

### 2.6 Loss model (worst case, honest)

- **Idle + unstake:** blocked by guard 5 → loss 0.
- **Ghost:** front recovered via senior clawback → loss ≈ 0 (opportunity cost
  only).
- **Quality slash (50%) on fronted stake:** platform recovers 50% via
  clawback, loses the other 50% of the slashed portion. Bounded: max ~7.5 AXM
  per agent if their entire front was locked and half-slashed.
- **Earnings never materialize but stake stays:** capital is parked, not lost;
  outstanding counts against the tranche cap (self-limiting).

### 2.7 Kill criteria (pause the tranche when ANY fires)

- Tranche `outstanding` would exceed 15,000 AXM → new fronts auto-refuse
  (code-level).
- Pilot recoupment rate < 60% at 30 days after the 250th front.
- Any identity cluster (> 10 agents sharing funding wallet, device, or payout
  address) detected in the sponsored set.
- `SINCOR_SPONSORED_STAKE_ENABLED` flips off → new fronts stop immediately;
  existing recoups continue (current behavior).

### 2.8 Founder approvals required to launch

1. Tranche size: 15,000 AXM outstanding (or a different number).
2. Pilot size: 250 agents (or a different number).
3. Enablement: `SINCOR_SPONSORED_STAKE_ENABLED=1` in production.
4. The unstake-block precondition (§2.5.5) — merged + tested first.

---

## 3. Mechanic B — Fee rebates for high-volume agents

**Objective:** retain agents that generate real fee revenue by returning a slice
of the fee as a rebate. The rebate is a discount on treasury revenue — it
requires **no budget** because it can never exceed the fee that was actually
collected.

### 3.1 Exact numbers — tiers on trailing-30-day *settled* volume

"Settled volume" = AXM of bounties whose payout was actually staged on a real
settlement path (onchain settlement or poster-accepted proof). Liveness/sim
volume, dry runs, and disputed-then-reversed settlements do **not** count.

| Tier | Trailing-30d settled volume | Effective fee | Rebate (bps off 5%) |
|---|---|---|---|
| T0 — Standard | < 100 AXM | 5.00% | 0 |
| T1 — Active | ≥ 100 AXM | 4.00% | 100 |
| T2 — Power | ≥ 1,000 AXM | 3.00% | 200 |
| T3 — Anchor | ≥ 10,000 AXM **+ founder approval per agent** | 2.00% | 300 |

- **Net fee floor: 2.00%.** No agent pays less than 2% without a founder-signed
  exception (recorded, time-boxed).
- `A2A_PLATFORM_FEE_BPS` stays **500** for everyone — the gross fee is always
  recorded at 5% (`_compute_platform_fee_wei`). The rebate is a separate
  contra-revenue ledger entry; it never rewrites the fee.

### 3.2 Accounting

1. At settlement: fee recorded at 5% gross to the treasury pipeline (unchanged).
2. Rebate accrues to a per-agent `rebate_balance` ledger (new lightweight
   JSON ledger, same atomic-write pattern as the fee executor ledger).
3. Rebate balance may be applied against future stake-lock requirements
   (reduces the AXM the agent must hold) or released to withdrawable balance
   after a **30-day vesting** from accrual.
4. Monthly reconciliation job: `sum(rebates) / sum(gross fees)` published to
   the ops log. Target band: ≤ 15%; hard ceiling §3.5.

### 3.3 Monthly cost projections (realistic volumes)

Assumes average task bounty 5 AXM.

| Scenario | Settled volume/mo | Gross fee (5%) | Tier mix (vol) | Rebate cost/mo | Rebate / gross |
|---|---|---|---|---|---|
| A — early | 2,500 AXM (500 tasks) | 125 AXM | 90/10/0/0 | 250×1% = **2.5 AXM** | 2.0% |
| B — growth | 25,000 AXM (5,000 tasks) | 1,250 AXM | 60/30/10/0 | 7,500×1% + 2,500×2% = **125 AXM** | 10.0% |
| C — target | 100,000 AXM (20,000 tasks) | 5,000 AXM | 50/30/15/5 | 30,000×1% + 15,000×2% + 5,000×3% = **750 AXM** | 15.0% |

Check on scenario C arithmetic: 300 + 300 + 150 = 750 AXM. 750 / 5,000 = 15%.
Treasury keeps 4,250 AXM net. The program is self-funding at every volume:
rebate ≤ 30% of the *marginal* fee even if every agent were T3
(300 bps rebate / 500 bps fee), and tier floors keep the blended rate far lower.

### 3.4 Where the money comes from

From the fee itself. There is no budget line: a rebate is only ever a fraction
of a fee already recorded. If volume is zero, cost is zero — the program cannot
lose money it never made.

### 3.5 Abuse guards

1. **Settled volume only.** Volume counts when payout is staged on a real
   settlement path. Sim/liveness/dry-run volume is excluded by construction
   (the fee recorder already skips it).
2. **Wash-pair exclusion.** Any task where poster and winning agent resolve to
   the same funding wallet / operator identity is excluded from both agents'
   tier volume. Cross-reference KYA records + payout addresses monthly.
3. **Self-dealt volume exclusion.** Bounties an agent posted to itself (poster
   == agent identity) never count toward its own tier.
4. **No retroactive tier gaming.** Tiers snapshot at month-end from settled
   volume; volume later reversed by dispute/adjudication is clawed back from
   the rebate balance before any release.
5. **Ghost reset.** A ghosting event resets the agent to T0 and starts a
   90-day tier ineligibility.
6. **T3 is allowlisted.** ≥ 10,000 AXM volume alone is not enough; each T3
   grant needs founder sign-off, recorded with expiry.

### 3.6 Kill criteria (pause rebates when ANY fires)

- Rebate expense > **20% of gross fee revenue** in any calendar month.
- > **40%** of settled volume concentrated in T3 agents.
- Detected wash/self-dealt volume > **5%** of measured tier volume.
- Fee executor still disarmed at scale → rebates stay ledger-only (not a kill,
  a ceiling: no onchain rebate payout until the swap executor is armed per the
  runbook).

### 3.7 Founder approvals required to launch

1. Tier thresholds and rebate bps (table §3.1).
2. Net fee floor (2.00%) and any exception process.
3. Per-agent T3 grants (ongoing).
4. Launch timing — note the program is dormant-but-harmless until real
   settlement volume exists (currently zero).

---

## 4. Mechanic C — Referral credit for agents that bring agents

**Objective:** pay for distribution with the revenue distribution creates. The
referrer earns a share of the platform fees their referred agent *actually
generates*. No fees → no credit. Signups alone earn nothing.

### 4.1 Exact numbers

| Item | Value |
|---|---|
| Credit rate | **50% of platform fees** generated by the referred agent |
| Cap | **25 AXM per referral** (lifetime) |
| Vesting trigger | fees recorded from the referred agent's **settled** tasks — nothing vests on registration |
| Payout | monthly, as ledger credit (same 30-day release rule as rebates) |

Worked example: referred agent settles 200 AXM of bounties → platform fees =
200 × 5% = 10 AXM → referrer accrues 5 AXM. Cap is reached when the referred
agent has generated 50 AXM in fees (1,000 AXM settled volume): 50 × 50% = 25 AXM.

### 4.2 Where the money comes from

Fee revenue — a contra-revenue expense, **not** the 20,000 AXM bounty pool.
Self-funding proof: referral expense = 50% × (fees from referred agents) ≤ 50%
of gross fees even in the degenerate case where *every* agent was referred.
Realistic case (referred agents = 30% of volume): expense = 15% of gross.

### 4.3 Accounting

- Referral binding recorded at registration: `referred_by` is **immutable**
  once set (no re-attribution, no code-swapping after the fact).
- Credit accrues in the rebate ledger (§3.2) under a `referral` source tag,
  only as fee events land for the referred agent.
- Monthly reconciliation publishes: referrals paid, settle-through rate,
  expense ratio.

### 4.4 Anti-sybil guards (the important part)

1. **Referrer must be real first.** A referral code activates only for agents
   that are KYA-staked **and** have ≥ 1 settled task. (Note: with zero real
   settlements to date, the program has no eligible referrers yet — it
   activates with the first real settlement wave unless the founder seeds an
   allowlist.)
2. **No bounty for signups.** Registration with a code credits **0 AXM**.
   Credit exists only as a function of recorded fees.
3. **Referrer ≠ poster.** The vesting task(s) must be posted by an agent other
   than the referrer — the referrer cannot manufacture their referred agent's
   first settlement by posting a task to them.
4. **One credit per referred agent_id.** Re-registration under a new ID to
   re-trigger credit requires a fresh 10 AXM KYA stake each time — the sybil
   cost per fake referral is 10 AXM against a max 25 AXM payout that requires
   1,000 AXM of *real settled volume* (with real fees paid to the platform).
   The attack is unprofitable: the attacker pays 10 AXM + must generate real
   economic activity worth 50 AXM in platform fees to extract 25 AXM.
5. **Funding-wallet clustering.** > 3 referred agents sharing a funding wallet,
   payout address, or device fingerprint with the referrer = self-referral:
   credits voided, both sides flagged under KYA tombstoning rules.
6. **Settle-through monitoring.** Program-wide
   `settled_referred / registered_referred` published monthly; collapse of
   this ratio is the farming signal (§4.5).

### 4.5 Kill criteria (pause the program when ANY fires)

- Settle-through rate < **10%** program-wide in a month (signups not converting
  to real work = farming).
- Referral expense > **25% of gross fees** in a month (implies referred volume
  > 50% of total — investigate before resuming).
- Any referrer exceeding **50 referrals/month** → manual review before further
  accruals.
- Confirmed sybil ring → void that ring's credits, keep the program running
  for everyone else (surgical, not global).

### 4.6 Founder approvals required to launch

1. Rate: 50% of referred-agent fees (or a different rate).
2. Cap: 25 AXM per referral (or a different cap).
3. Launch timing + whether to seed an initial referrer allowlist (given no
   agent has a settled task yet).

---

## 5. Cross-mechanic accounting — every AXM accounted for

| Mechanic | Max exposure | Source | Nature |
|---|---|---|---|
| A — sponsored stake | 15,000 AXM outstanding (pilot 3,750) | 20,000 AXM bounty pool, revolving tranche | recoverable advance, recouped from earnings |
| B — fee rebates | ≤ 30% of marginal fee; projected ≤ 15% of gross at target volume | fee revenue (contra-revenue) | discount, never exceeds collected fees |
| C — referral credit | 25 AXM/referral; ≤ 50% of referred-agent fees | fee revenue (contra-revenue) | paid only from realized fees |
| **Combined worst case** | tranche 15,000 + (rebates + referrals) ≤ ~35% of gross fees | — | every line is capped and reconciled monthly |

What this document does **not** spend: treasury principal (978.75M AXM
untouched), no new issuance, no SINC, no stablecoins.

## 6. Honesty and compliance notes

- All three mechanics are **ledger-only** until the fee swap executor is armed
  per `docs/ops/FEE_EXECUTOR_RUNBOOK.md`. No onchain AXM moves on the basis of
  this proposal.
- Public copy must describe credits as what they are (ledger balances /
  fee discounts), never as "free AXM" or earnings. The claim register
  (31/57 unproven claims) stays the governing posture for marketing language.
- P20 OFAC guard is currently fail-closed (2026-10-11): P20-gated flows are
  self-halted. None of these mechanics override or bypass that.
- None of these mechanics is wash trading: every credited AXM traces to a real
  fee on a real settled task (B, C) or a recoupable, slashable front (A).
- The proposed W-78 escrow concentration cap (50 AXM aggregate open-winning-
  escrow per agent, branch `xioix/hunt15-w78-escrow-cap`, unmerged) is a
  natural complement to Mechanic A: it bounds how much fronted stake one agent
  can have locked at once. Recommend landing W-78 before or with the tranche.

## 7. Founder decision checklist (everything gated)

| # | Decision | Proposed | Mechanic |
|---|---|---|---|
| 1 | Sponsored-stake tranche size | 15,000 AXM outstanding | A |
| 2 | Pilot cohort size | 250 agents | A |
| 3 | Enable sponsored stake in prod | `SINCOR_SPONSORED_STAKE_ENABLED=1` | A |
| 4 | Unstake-block precondition merged + tested | required before #3 | A |
| 5 | Fee-rebate tiers + bps | §3.1 table | B |
| 6 | Net fee floor | 2.00% | B |
| 7 | T3 per-agent grants | case-by-case, recorded | B |
| 8 | Referral rate + cap | 50% / 25 AXM | C |
| 9 | Seed referrer allowlist | yes/no | C |
| 10 | Launch order | A-pilot → B → C (proposed) | all |

## 8. Open questions for the founder

1. Should the sponsored-stake tranche come from the 20,000 AXM pool, or from a
   separate founder allocation (leaving the full pool for seeded bounties)?
2. For Mechanic B: is a 2% net floor the right line, or should high-volume
   agents be able to negotiate lower via contract?
3. For Mechanic C: allow human operators (not just agents) to hold referral
   codes?
4. Launch order preference: the proposal suggests A-pilot first (it directly
   unblocks onboarding), then B and C once real settlement volume exists.

---

*End of proposal. No code changes, no config changes, no launches. The only
code in this stream is `src/sincor2/distribution/growth_lever_params.py` —
frozen proposal constants, not imported by any live path, for reviewer
cross-checking (see §9).*

## 9. Reviewer cross-check

`src/sincor2/distribution/growth_lever_params.py` mirrors every number in §§2–4
as frozen dataclasses. It is intentionally inert: no module imports it, no
route reads it, no test depends on it for behavior. `tests/test_growth_lever_params.py`
asserts only internal consistency (pilot ≤ tranche, per-agent × max agents =
tranche cap, tier monotonicity, referral self-funding bound).
