# Pre-Push Secret Sweep — Wave 43 Verification Report

**Branch:** `xioix/buildout-43-secret-sweep` · **Base:** `fd96801` · **Date:** 2026-09-30
**Scope:** all 41 push-ready branches (from `driver-state.json` `push_ready` array).
**Method:** no `gitleaks` binary available; used a purpose-built Python scanner over
`git diff fd96801...<branch>` added lines, with 18 secret patterns (PEM keys, AWS keys,
Stripe live keys, GitHub PATs, JWTs, OpenAI/Anthropic keys, 64-hex secrets, mnemonics,
password/API-key/secret/token assignments, plus targeted checks for
`BILLING_FORWARDER_PRIVATE_KEY` / `ADMIN_PASSWORD` / `SECRET_KEY` **values**).
Also scanned added filenames for `.env*`, `credentials*`, `*secret*`, `*.pem`, `*.key`.

## Verdict: ALL 41 BRANCHES CLEAN — no real secrets committed

12 pattern matches were found across 3 branches. Every one was manually verified
in context and is a **well-known public constant**, not a secret:

| Branch | File | Match | Verified as |
|---|---|---|---|
| `xioix/buildout-06-caller-ownership` | `src/sincor2/a2a_integration.py:2217` | `0xFFFF…4141` (`_SECP256K1_N`) | secp256k1 curve order — public ECDSA malleability-guard constant |
| `xioix/buildout-27-stake-slash-design` | `contracts/StakeSlashManager.sol:46-48` | `0xFFFF…4141`, `0x7FFF…B20A0` (`HALF_N`) | secp256k1 curve order and half-order — public malleability-guard constants (commented as such; the half-order was the self-review catch noted in the wave-27 report) |
| `xioix/buildout-27-stake-slash-design` | `src/sincor2/onchain/stake_bridge.py:175` | `0xFFFF…4141` | same public curve-order constant (signature `s` bound check) |
| `xioix/buildout-27-stake-slash-design` | `tests/pytest/test_stake_slash_bridge.py` | `0xFFFF…4141` | same public constant in test vectors |
| `xioix/buildout-25-fork-sim-harness` | `fork_sim/run_fork_sims.py:64-65` | `0xddf2…b3ef` (`TRANSFER_TOPIC`) | keccak256("Transfer(address,address,uint256)") — public ERC-20 event topic |

No PEM blocks, no AWS/Stripe/GitHub/OpenAI/Anthropic keys, no JWTs, no mnemonics,
no password/API-key/token **values**, no `BILLING_FORWARDER_PRIVATE_KEY` value,
no `ADMIN_PASSWORD` value, no `SECRET_KEY` value, and no credential filenames
(`.env`, `credentials*`, `*.pem`, `*.key`, wallet JSON) were added on any branch.

Env-var **names** (e.g. `ADMIN_PASSWORD`, `AGENT_HEARTBEAT_TOKEN`) are referenced
in code and docs as configuration — that is expected and fine; no values ship.

## Per-branch results (all clean)

- `xioix/p1-housekeeping-stale-strings` — clean
- `xioix/p1-fabricated-claims` — clean
- `xioix/p1-mislabeled-files` — clean
- `xioix/p1-burn-stats-retire` — clean
- `xioix/buildout-05-p24-bridge` — clean
- `xioix/buildout-07-registration-proof` — clean
- `xioix/buildout-08-issuance-route` — clean
- `xioix/buildout-09-heartbeat-auth` — clean
- `xioix/buildout-10-settlement-proofs` — clean
- `xioix/buildout-11-issuance-skill` — clean
- `xioix/buildout-12-p24-wiring-tests` — clean
- `xioix/buildout-13-payment-amounts` — clean
- `xioix/buildout-06-caller-ownership` — clean (2 public-constant matches, verified)
- `xioix/buildout-14-write-idempotency` — clean
- `xioix/buildout-17-error-envelopes-admin` — clean
- `xioix/buildout-16-rate-limits-sse` — clean
- `xioix/buildout-15-durable-state` — clean
- `xioix/buildout-20-defi-invariant-ledger` — clean
- `xioix/buildout-18-caller-quotas` — clean
- `xioix/buildout-19-axm-fee-listener` — clean
- `xioix/buildout-22-money-path-copy` — clean
- `xioix/buildout-23-p1-cleanup` — clean
- `xioix/buildout-21-defi-fuzz-suites` — clean
- `xioix/buildout-24-reputation-identity` — clean
- `xioix/buildout-26-ledger-hygiene` — clean
- `xioix/buildout-28-audit-artifacts` — clean
- `xioix/buildout-25-fork-sim-harness` — clean (2 public-constant matches, verified)
- `xioix/buildout-29-merge-recon` — clean
- `xioix/buildout-31-merge-supplement` — clean
- `xioix/buildout-30-shared-store-adopt` — clean
- `xioix/buildout-33-docs-index-refresh` — clean
- `xioix/buildout-27-stake-slash-design` — clean (8 public-constant matches, verified)
- `xioix/buildout-32-first-registration` — clean
- `xioix/buildout-34-burn-cleanup` — clean
- `xioix/buildout-36-deploy-checklist` — clean
- `xioix/buildout-37-tx-replay-design` — clean
- `xioix/buildout-35-task-decomp` — clean
- `xioix/buildout-38-screener-interface` — clean
- `xioix/buildout-40-docs-index-2` — clean
- `xioix/buildout-41-untracked-files` — clean
- `xioix/buildout-42-merge-corrections` — clean

## Blocked branches

None. No branch requires a fix wave for secrets.

## Note for the merge pass

The scanner flags 64-hex values by shape; the three flagged values are public
cryptographic constants that legitimately appear in ECDSA/ABI code. Any future
secret sweep over the merged tree will re-flag them — they are safe to ignore
(whitelist: `FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141`,
`7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0`,
`ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef`).

Scanner script (run evidence): `/tmp/secretsweep/scan.py`, findings at
`/tmp/secretsweep/findings.json`.
