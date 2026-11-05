# Changelog

All notable changes to SINCOR2 will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## Unreleased

_Nothing yet._

## [1.0.0] — 2026-11-05

First tagged release line. This entry summarizes what `main` actually
contains at the tag; status words are deliberate (merged ≠ live-wired,
PREPARED ≠ broadcast).

### Added
- **Human-in-the-loop control plane — Phase 1 escalation layer** (PR #610):
  Default-Deny `PolicyEngine` (hard-gate categories never auto-allow,
  unknown categories fail closed), `EscalationEnvelope` schema
  `escalation-envelope/1.0` with risk block (violations, capital exposure,
  recommended action, required signers), co-sign quorum with required-signer
  DENY veto, and an append-only hash-chained audit log.
- **Phase 2 signing + Merkle anchoring** (stack landing, PR #615):
  Ed25519 audit-record signing over canonical record bytes (keys only from
  `SINCOR_ESCALATION_SIGNING_KEY_FILE`, 0600, refuses overwrite; no signer
  configured = byte-for-byte Phase 1 behavior), SHA-256 Merkle epochs with
  inclusion proofs over a hash-chained anchor store. Anchor status is
  **PREPARED** — on-chain broadcast is founder-gated and not built; no root
  is claimed anchored.
- **Sanctions-screen wiring** into the escalation gate (detector hits
  stamped as policy rules) and a **ZK policy-proof spike** (circom/snarkjs,
  real verified proof on a 2-constraint slice; full circuit not yet written).
- **Cross-agent checkpointing + KYA telemetry** (PR #615): event-sourced
  workflow state with checkpoints, leases, and idempotent steps; board and
  bounty pool on checkpointed durable state; KYA telemetry wired into the
  live execution loop.
- **MoebiusX session-account prototype** (PR #614): capped, expiring,
  revocable session-key policy engine for a founder-owned smart account,
  with fork-sim proofs.
- **Bounty pool safety net**: conservation invariant tests plus a 30-seed
  randomized operation fuzz (`tests/pytest/test_pool_invariants.py`), and a
  pre-commit hook running the smoke suite + secret scan.
- **x402 buyer path**: 402-challenge handling, EIP-712 authorization
  signing, retry-with-payment; replay-auth fixtures refreshed to the
  locked W-103/W-115 standard (PR #620) and EIP-191 buyer delivery
  recovery (PR #621).
- Root `ARCHITECTURE.md` with high-level system overview and data flow;
  expanded directory structure (`verticals/`, `marketplace/`, `core/`,
  `dae/`, `docs/`); A2A compliance scaffolding and Agent Card support
  (18 live skills, 7 archetypes).

### Changed
- **Site truth pass** (PR #615): fabricated marketing claims, token
  price/floor displays, profit promises on non-live products, guarantee
  claims, and dead templates removed from the public site; README facts
  refreshed with control-plane and custody sections.
- README now carries live CI badges for the workflows that exist in
  `.github/workflows/` (PR checks, CI, onchain-ci, Security Checks,
  CodeQL) and a "Verify a receipt" section.

### Fixed
- Checkpointed persistence: mirror-failure double-spend, wall-clock board
  freshness, and snapshot concurrency (PR #615 stack).
- Pool ledger reconciliation replacing a tautological invariant check.
- Wren hardening round (PRs #618–#624): admin information-leak reduction,
  legacy crypto checkout/verify routes retired, checkout griefing caps,
  log sanitization, idempotent challenge retry-bind, the #600
  already-ran kickoff gate, intake guard, and alert/dispatch hygiene.

### Security
- CI/security workflow hardening (gitleaks, pip-audit, least-privilege
  workflow permissions); OFAC SDN snapshot re-stamps (#608, #616).

## [2026-06-07]
- Large-scale addition of missing production-oriented components via Copilot-assisted development
- Initial population of vertical packs scaffolding (healthcare, dental, compliance, trading, lead_gen)
