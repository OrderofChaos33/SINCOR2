# GitHub Actions — paused until billing is restored

## What was happening

Every push to `main` showed **Run failed**.

Jobs died in 1–3 seconds with **no logs**. That is not a test failure. GitHub Actions billing/minutes are locked, so runners never start.

Workflows that auto-fired on push:

- `CI` (`.github/workflows/ci.yml`)
- `Codacy Security Scan`
- `Security Checks` / `onchain-ci`
- **CodeQL default setup** (`dynamic/github-code-scanning/codeql`) — GitHub-managed, not our YAML

YAML was already switched to `workflow_dispatch` for CI / Codacy / security / onchain. That stopped those. **Default CodeQL kept firing** because it is configured under Settings → Code security, not under `.github/workflows`.

Adding `.github/workflows/codeql.yml` with `if: false` only skipped the *file* workflow. Default setup is a separate dynamic workflow and YAML cannot disable it.

## What is true now (2026-08-22)

| Control | State |
|---|---|
| Repository Actions (`actions/permissions`) | **disabled** |
| File-based CodeQL (`.github/workflows/codeql.yml`) | **disabled_manually** |
| CI / Codacy / Security / onchain-ci / deploy-base | manual only (`workflow_dispatch`) |
| CodeQL **default setup** | still `configured` in Settings (API cannot clear it) |

With Actions disabled at the repo, default CodeQL cannot enqueue new failed runs. That is what stops the “Run failed” mail.

The default-setup API (`PUT .../code-scanning/default-setup`) returns 404 for the connected GitHub App, and `PUT .../actions/workflows/{id}/disable` returns 422 for the dynamic CodeQL workflow. Repo-level Actions off is the lever that actually works.

## Restore (when billing is fixed)

1. [GitHub billing](https://github.com/settings/billing) — add a payment method / Actions minutes.
2. Re-enable Actions: repo **Settings → Actions → General → Allow all actions**.
3. **Before the next push**, disable CodeQL default setup: [Code security](https://github.com/OrderofChaos33/SINCOR2/settings/security_analysis) → CodeQL → Disable. If you skip this, the first push after re-enable will fail again.
4. Put CI back on push by restoring `on: [push, pull_request]` in `.github/workflows/ci.yml` (and Codacy/security/onchain if you want them).
5. Re-enable `.github/workflows/codeql.yml` only if you want *advanced* CodeQL, not default setup.

Do not re-enable Actions until step 3 is done.
