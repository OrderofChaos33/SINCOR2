# Security Policy

SINCOR2 operates a live agent marketplace and payment surfaces on Base.
We take security reports seriously and appreciate responsible disclosure.

## Reporting a vulnerability

We take security seriously. If you discover a security vulnerability, please report it privately — do not open a public issue for it. Either route reaches the founder:

1. **GitHub Security Advisories:** repository → **Security** tab → **Report a vulnerability**, or
2. **Email:** court@getsincor.com (preferred) with as much detail as possible (affected component, reproduction steps, impact).

Fixes for confirmed vulnerabilities land before any public disclosure discussion.

Please include:

- A description of the issue and its impact
- The component involved (API endpoint, contract, SDK, workflow)
- Reproduction steps or a proof of concept
- Any suggested remediation

We aim to acknowledge receipt within **48 hours** and provide an initial
assessment within **7 days**. Please give us a reasonable window to
remediate before any public disclosure; we will credit reporters who
follow this policy (unless anonymity is requested).

## Supported versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | ✅ Active support  |
| main    | ✅ Active support  |

## Scope

**In scope**

- The SINCOR2 platform at getsincor.com (A2A marketplace APIs, Agent Card
  discovery, x402 surfaces, plan/checkout flows)
- This repository: `src/sincor2/`, `marketplace/`, `contracts/`,
  `onchain/`, `liveness/`, GitHub Actions workflows
- The `sincor-x402` Python SDK (PyPI)
- Smart contracts listed as deployed in `CANONICAL_ADDRESSES.md` /
  `contracts/CONTRACTS_MANIFEST.md`

**Out of scope**

- Third-party services and chains (Base itself, Coinbase CDP, Railway,
  Resend, Polymarket) — report those to their operators
- Social engineering, phishing, or physical attacks
- Denial-of-service testing against production
- Findings in contracts that are **not deployed** (source-only code) —
  still welcome as code review, but they carry no live-funds impact and
  will be triaged as such

## What is NOT authorized

Testing must never touch other people's assets or data. Specifically:

- **No testing against production funds** — do not move, escrow, bid with,
  or attempt to withdraw funds belonging to the platform, its treasury
  (`0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`), or any user/agent
  account, beyond the minimum read-only probing needed to confirm an
  issue exists.
- **No testing against production users or agents** — no bidding wars,
  griefing, sybil floods, or reputation manipulation against live
  marketplace participants.
- No exfiltration of data beyond the minimum needed to demonstrate the
  vulnerability; no persistence, no lateral movement.
- No automated scanning at rates that degrade the service.

If you need a funded environment to validate a finding, ask us first —
we can point you at testnet (Base Sepolia) or a staged surface instead.

## Safe harbor

We will not pursue legal action against researchers who, in good faith:

- Report findings promptly through the channels above
- Stay within the scope and authorization limits in this policy
- Avoid privacy violations, service degradation, and destruction of data
- Give us reasonable time to remediate before disclosure

## Platform security practices

- Secret scanning in CI (gitleaks, `.gitleaks.toml`) and in the repo
  pre-commit hook; secrets live in environment variables / the deployment
  platform, never in git — see `.env.example` for the variable surface
- Sanctions screening (OFAC) fail-closed at task intake
- Anti-gaming guards on the money path: self-bid and anonymous-poster
  refusals, sealed commit–reveal bidding, merit-gated high-value tasks
- Session-key execution is least-privilege by design: capped, expiring,
  target-restricted delegated authority; the owner key never sits in the
  agent runtime
- Fee-conversion execution on-chain remains **disarmed** until separately
  authorized

## Supported versions

| Version | Supported |
|---|---|
| `main` (deployed platform) | ✅ |
| `sincor-x402` latest on PyPI | ✅ |
| Older branches / tags | ❌ |
