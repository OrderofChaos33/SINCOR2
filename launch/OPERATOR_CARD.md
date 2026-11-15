# OPERATOR CARD — GENESIS LAUNCH (Monday, Nov 9, 2026)

One-page quick reference. Full procedure: `launch/GENESIS_RUNBOOK.md`
(status: READY FOR FOUNDER APPROVAL — signature is the founder's tap).
**Sole operator: Courtney (founder). Every role. No escalation above him.**

## What is launching

- **Free founding cohort** — claim at `/genesis`
  (`POST /api/genesis/claim`), email confirmation link binds the wallet
  (token hashed, single-use, 1-hour expiry). Claiming costs nothing.
- **Wallet-native, manual onboarding** — founder reviews and contacts
  confirmed members personally. Genesis SBT / airdrop eligibility =
  confirmed membership; distribution is a separate founder-gated step.
- **Paid is GATED** — packs (one-time, from $297) and subscriptions
  (recurring 30-day periods, from $297/period) do not open until all 7
  gates in runbook section 10 clear. Do not announce them open.

## Before the gate lifts (runbook §3)

- [ ] Dry run: claim with the **founder's own email**, confirm from the
      emailed link, member row exists in `genesis_cohort.db`
- [ ] `GET /.well-known/agent-card.json` → **42 skills**
- [ ] `SINCOR_LAUNCH_DATE` unset or `2026-11-09`; gate lifts on UTC date
- [ ] Railway `harmonious-nourishment` / `web`: ACTIVE, Serverless OFF
- [ ] Liveness passes exiting 0 against getsincor.com
- [ ] Cohort count + board/pool counters **written down** (see warning)

## ⚠ The one warning that matters

**No Railway volume yet — `/data` is ephemeral.** Support ticket ref
**7cc5feea** pending. **Any deploy — including a rollback redeploy —
wipes board/pool counters AND `genesis_cohort.db`.** No unnecessary
deploys during launch week. Record counts before any deploy, always.

## Halt levers (all founder-only)

| Lever | How | Effect |
|---|---|---|
| Manual stop | Stop announcing / onboarding | Stops inflow socially — there is **no claim feature flag** in the code |
| Comms kill switch | `SINCOR_COMMS_KILL_SWITCH=1` in Railway env (applies on redeploy; gate itself is checked live every call) | Gated outbound sends refuse |
| Treasury kill switch | HALT file, default `data/TREASURY_EXEC_HALT` (env `TREASURY_EXEC_HALT_FILE`) | Rail settles refuse: `money_gate_denied`, fail-closed |
| Rollback | Railway → `web` → Deployments → previous known-good → **Redeploy** | Returns last good code — **and wipes `/data`** (see warning) |

## Verify a halt / recovery

- Halt worked: no new `rail_events` after the timestamp
  (`GET /v1/ops/rail-revenue`, header `X-Admin-Key: <ADMIN_PASSWORD>`);
  probe settle returns `money_gate_denied`.
- Recovery: fix on a branch → founder merges → clear halts **only on
  founder instruction** → re-run the §3 checklist in full → re-announce.
- Volume lands (ticket reply): attach at `/data` → one controlled
  deploy → counts must persist + `SINCOR_REVENUE_DB` under `/data`.
  Only then does the no-deploy rule retire.

## Known-open (accepted for Monday, gates paid)

- `/api/price/official` still serves the $0.15 / $150M FDV claim —
  purge built locally, not deployed. Never cite it.
- Coinbase Onramp **not configured** — rail is USDC-on-Base only;
  no card/bank copy until verified.
- Sanctions snapshot stale (`Verdict.STALE` warnings) — refresh built
  locally, not deployed. Never weaken screening to go green.
