# PR Pack Index — SINCOR Complete Build-Out (Wave 44)

**Base for all branches:** `main` at `fd96801`. **Human-gated:** open each PR from its branch, merge in the numbered order below. Nothing has been pushed; the user opens the PRs with a fresh one-shot PAT.

**Merge corrections ledger:** `docs/ops/MERGE_CORRECTIONS.md` (w42, C1–C9) + driver-state C10. Read both before merging anything.

**DO NOT MERGE:** `buildout-33-docs-index-refresh` (superseded by w40, C2) and `buildout-39-integration-dryrun` (scratch — never push/merge).

## Merge order (41 PRs)

### P1 housekeeping (code) — merge first
| # | PR body | Wave | One-line |
|---|---------|------|----------|
| 1 | `p1-housekeeping-stale-strings.md` | w01 | Correct stale fee-policy (5%, no burn) and agent-count (48) strings |
| 2 | `p1-mislabeled-files.md` | w04 | Fix mislabeled canonical paths + broken import |
| 3 | `p1-burn-stats-retire.md` | w03 | Retire burn-stats route, end burn narrative |
| 4 | `p1-fabricated-claims.md` | w02 | Remove fabricated public claims (CertiK score, live-on-Base pricing) |

### P2 issuance (code)
| # | PR body | Wave | One-line |
|---|---------|------|----------|
| 5 | `buildout-05-p24-bridge.md` | w05 | P24 issuance bridge: exact calldata, eth_call dry-run, caller-side signing |
| 6 | `buildout-08-issuance-route.md` | w08 | Agent-triggered P24 issuance route + `issuance` rate tier — merge BEFORE #18 |
| 7 | `buildout-11-issuance-skill.md` | w11 | P24 issuance agent skill wired to the w08 route |
| 8 | `buildout-12-p24-wiring-tests.md` | w12 | P24 full-chain + Foundry access-control tests; ZeroCreator contract guard (C10 with w05) |

### P3 A2A hardening (code)
| # | PR body | Wave | One-line |
|---|---------|------|----------|
| 9 | `buildout-07-registration-proof.md` | w07 | Signed re-registration (unsigned → 403); founder decision open vs w32 grace |
| 10 | `buildout-09-heartbeat-auth.md` | w09 | Heartbeat + side-channel auth (deploy: AGENT_HEARTBEAT_TOKEN) |
| 11 | `buildout-06-caller-ownership.md` | w06 | Strict EIP-191 create-auth; cancel/read restricted to owner |
| 12 | `buildout-46-w13-testcompat.md` | w46 | Payment amounts from chain, never caller claims (replaces w13) |
| 13 | `buildout-10-settlement-proofs.md` | w10 | Adjudicator-signed settlement receipts (C9: retire inline PaymentVerifier) |
| 14 | `buildout-14-write-idempotency.md` | w14 | Idempotency keys on money-adjacent writes (quota consume inside execution) |
| 15 | `buildout-47-w18-testcompat.md` | w47 | Free quota keyed on verified identity (replaces w18; C3 dedup with #16) |
| 16 | `buildout-45-w24-fixes.md` | w45 | Reputation keyed on verified identity + leaderboard INT64-overflow fix (replaces w24) |
| 17 | `buildout-17-error-envelopes-admin.md` | w17 | JSON error envelopes + admin credential unification (deploy: ADMIN_PASSWORD) |
| 18 | `buildout-48-w16-testcompat.md` | w48 | Rate-limit rewrite + SSE auth/throttle (replaces w16; re-add w08 issuance tier after) |
| 19 | `buildout-15-durable-state.md` | w15 | Durable shared state (Redis + local fallback); port hunks onto #18 (C4) |

### P4 DeFi proof ledger (code) — entry-ID concatenation, never text-merge (C6)
| # | PR body | Wave | One-line |
|---|---------|------|----------|
| 20 | `buildout-20-defi-invariant-ledger.md` | w20 | Invariant-test evidence recorder + 19 ledger entries |
| 21 | `buildout-21-defi-fuzz-suites.md` | w21 | Fuzz suites for 7 products + 7 ledger entries |
| 22 | `buildout-25-fork-sim-harness.md` | w25 | Fork-sim harness (live Base mainnet, read-only) |
| 23 | `buildout-26-ledger-hygiene.md` | w26 | 3 stale entries superseded by entry-ID; SKU canon doc — AFTER #20–#22 |
| 24 | `buildout-28-audit-artifacts.md` | w28 | Internal audit-report entries (+26); not third-party audits |

### P5 money path (code)
| # | PR body | Wave | One-line |
|---|---------|------|----------|
| 25 | `buildout-19-axm-fee-listener.md` | w19 | Fail-closed treasury policy + fee listener; executor stays DISARMED |
| 26 | `buildout-22-money-path-copy.md` | w22 | Honest money-path copy (after w02; take w22's side on axiom.html) |
| 27 | `buildout-23-p1-cleanup.md` | w23 | Doc index + canonical paths + DEMO_SECRET removal (C1: drop root foundry.lock/.gitmodules) |

### Late code waves
| # | PR body | Wave | One-line |
|---|---------|------|----------|
| 28 | `buildout-30-shared-store-adopt.md` | w30 | Shared-state adoption guide + merge-time adapter wiring (C4) |
| 29 | `buildout-32-first-registration.md` | w32 | First-registration squatting control (C3 dedup; founder decision open) |
| 30 | `buildout-34-burn-cleanup.md` | w34 | Remove inert AGENT_BURN_AUTO dead path |
| 31 | `buildout-38-screener-interface.md` | w38 | P24 content screener, fail-closed default (C5 with w08; custody decisions open) |

### Docs / design / verification — order flexible, merge late
| # | PR body | Wave | One-line |
|---|---------|------|----------|
| 32 | `buildout-27-stake-slash-design.md` | w27 | Stake/slash contract design, dry-run only (no broadcast) |
| 33 | `buildout-29-merge-recon.md` | w29 | MERGE_PLAN.md (25-branch order + conflict map) |
| 34 | `buildout-31-merge-supplement.md` | w31 | Merge-plan supplement (fork-sim + audit branches) |
| 35 | `buildout-35-task-decomp.md` | w35 | 1000-task swarm decomposition (planning artifact) |
| 36 | `buildout-36-deploy-checklist.md` | w36 | Production deploy checklist (43 checks; stage env BEFORE any deploy) |
| 37 | `buildout-37-tx-replay-design.md` | w37 | Payment replay-prevention design (C8: do not pre-decide A/B/C) |
| 38 | `buildout-40-docs-index-2.md` | w40 | Docs index #2 — SUPERSEDES w33 (C2) |
| 39 | `buildout-41-untracked-files.md` | w41 | Untracked-file resolution record (feeds C1) |
| 40 | `buildout-42-merge-corrections.md` | w42 | Merge corrections ledger C1–C9 — merge BEFORE the code waves ideally |
| 41 | `buildout-43-secret-sweep.md` | w43 | Secret-sweep report (41/41 clean) — merge LAST, for the record |

## Key merge-time corrections (see bodies for full text)
- **C1** (#27): drop w23's root `foundry.lock` + `.gitmodules`.
- **C2** (#38): take w40's `docs/README.md`; never merge w33.
- **C3** (#15/#16/#29): single shared EIP-191 helper set.
- **C4** (#18/#19/#28): `SharedStateRateLimitStore` adapter; preserve issuance tier, wall clock, fail-closed 503, scoped clears, settle idempotency.
- **C5** (#6/#31): w38 + w08 pre-screen coexist; `register` is authority.
- **C6** (#20–#24): proof ledger by entry-ID concatenation; JSON-validate every edit.
- **C8** (#37): replay design A/B/C undecided; Phase B gated on C3 + relayer allowlist.
- **C9** (#12/#13): retire inline `PaymentVerifier` for canonical `payment_verifier.py`.
- **C10** (#5/#8): add `ZeroCreator` to w05's embedded ABI at the exact compiled position.

## Blocked founder decisions (do not pre-decide in any PR)
- Live-block release for 19 DeFi products (item 13).
- `$0.15` vs `$1.50` price-floor single source of truth (item 31).
- w07 strict re-registration vs w32 unsigned grandfathered grace (kept strict in scratch).
- P24 screener identity/custody, `P24_SCREENER` posture, relayer/forwarder allowlist.
- Fee-executor arming (item 32); Sepolia broadcast authorizations.
