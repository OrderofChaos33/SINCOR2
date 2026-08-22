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
| CodeQL **default setup** | **not-configured** |

Proof: commit `03dad38` (`docs/ACTIONS.md`) did **not** enqueue any workflow run — including default CodeQL. The “Run failed” mail stops here.

Repo-level Actions off is the lever that worked. `PUT .../code-scanning/default-setup` returned 404 for the connected GitHub App, and `PUT .../actions/workflows/{id}/disable` returned 422 for the dynamic CodeQL workflow.

## Restore (when billing is fixed)

1. [GitHub billing](https://github.com/settings/billing) — add a payment method / Actions minutes.
2. Confirm CodeQL default setup is still off: [Code security](https://github.com/OrderofChaos33/SINCOR2/settings/security_analysis).
3. Re-enable Actions: repo **Settings → Actions → General → Allow all actions**.
4. Put CI back on push by restoring `on: [push, pull_request]` in `.github/workflows/ci.yml` (and Codacy/security/onchain if you want them).
5. Re-enable `.github/workflows/codeql.yml` only if you want *advanced* CodeQL, not default setup.

Do not re-enable Actions until step 2 is confirmed. If default setup comes back on, the first push will fail again.
