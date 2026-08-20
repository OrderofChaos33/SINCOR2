# CI billing lock — stop the failure flood

**Symptom:** ~40 GitHub “Run failed” notifications per day.
**Root cause:** GitHub Actions minutes exhausted / billing locked. Jobs complete in ~3 seconds with **no logs** (HTTP 404 on log download). This is not a Python/Codacy finding.

Confirmed 20 Aug 2026: CI, Codacy Security Scan, CodeQL (“Push on main”), and onchain-ci all fail instantly on every push to `main`.

`security.yml` was already paused for the same reason.

## What we changed

| Workflow | Before | After |
|---|---|---|
| `ci.yml` | push + PR | **workflow_dispatch only** |
| `codacy.yml` | push + PR + weekly cron | **workflow_dispatch only** + `continue-on-error` |
| `onchain-ci.yml` | push/PR on `onchain/**` | **workflow_dispatch only** |
| `deploy-base.yml` | already dispatch | unchanged |
| `security.yml` | already dispatch | unchanged |
| CodeQL (GitHub default setup) | every push | **cannot pause from YAML** — disable in Settings |

## You must still disable CodeQL in the UI

CodeQL is `dynamic/github-code-scanning/codeql` (GitHub Advanced Security default setup). A workflow file cannot turn it off.

1. Open https://github.com/OrderofChaos33/SINCOR2/settings/security_analysis
2. Disable **CodeQL analysis** / default setup until billing is restored
3. Optional: Settings to Notifications to Actions — stop emailing failed workflows

## After billing is restored

1. Add a payment method / buy minutes: https://github.com/settings/billing
2. Restore triggers in `ci.yml` with path-ignore for docs and markdown
3. Codacy: weekly schedule only, not every push
4. onchain-ci: restore path filters so docs commits never run Foundry
5. Keep concurrency cancel-in-progress
6. Run CI Fixer Agent once via workflow_dispatch to prove minutes work

## Hard rule

Do not re-enable auto-triggers until a workflow_dispatch run actually produces logs (not a 3-second failure).
