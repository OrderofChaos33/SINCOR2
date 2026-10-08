# SINCOR2 Agent Rules Audit

**Auditor:** Worker 1 (agent governance build)
**Date:** 2026-10-08
**Base:** `b0f5f92` (branch `xioix/agent-governance-system`)
**Scope:** Every agent-related rule, policy, gate, constraint, rate limit, auth check, approval flow, kill switch, and circuit breaker in `src/` (Python + onchain Solidity), excluding demo-only UI copy.

**Legend:**
- **enforcement:** `hard` = code blocks the action; `soft` = advisory / logging / in-memory only
- **fail-mode:** `closed` = action blocked on check failure/error; `open` = action proceeds on check failure/error (or gate is opt-in/unwired)
- **CRITICAL** = fails OPEN on a **money path** (payments, transfers, staking, slashing, payouts, treasury) or **safety path** (external comms, credential access, contract calls). These are marked in a dedicated table below the domain tables.

**Method:** source-read every rule listed below; verified auth decorators, env-default values, and exception paths by reading the code, not comments. Test coverage checked via `grep` in `tests/pytest` (coverage counts: shadow_monitor 4, boundary 18, a2a_rate_limits 7, kill_switch 1, treasury_execution 0, compliance_guardrails 0, treasury_policy 1, auto_heal 1, p26_killswitch 0, hitl 0, safety_locks 0, ofac 1, p24_policy 0, outreach 2).

---

## 1. CRITICAL fail-open findings

These require founder decisions before launch. All five are real code paths verified in this tree.

| # | Name | Location | Fails open how |
|---|------|----------|----------------|
| C1 | **Unauthenticated kill-switch clear on trading path** | `src/sincor2/blueprints/monitoring.py:174` + `src/sincor2/bankroll.py:135-136` | `POST /api/polyclaw/clear-dry-runs` carries **no auth decorator** (the only `@jwt_required` in that blueprint is `optional=True` on a GET). The handler calls `br.clear_kill_switch()` unconditionally — "if it was tripped only by stale state" is a comment, not a condition. Any network caller can re-arm trading after a kill-switch trip. **Safety path: trading exposure kill switch.** |
| C2 | **`ComplianceGuard.sol` fail-open with no oracle set** | `onchain/src/ComplianceGuard.sol:40-47` | `isAllowed()` returns `true` whenever `oracleEnabled` is false or the oracle address is zero — screening is "blocklist-only until oracleEnabled". The fail-closed flip was **ratified 2026-09-29 but is not implemented in this tree** (P20 branch still unmerged). Deployed without a guardian-configured oracle, the contract is a rubber stamp. **Safety path: sanctions screening.** |
| C3 | **First-time agent registration accepts unverified wallet claims by default** | `src/sincor2/a2a_inbound_ext.py:163-168,302` | `SINCOR_REGISTRATION_PROOF_REQUIRED` defaults **unset → unverified claims accepted**, marked `identity="unverified"`. An attacker can register arbitrary agent_ids with arbitrary wallet claims; nothing on the money path (stake deposit, bids) re-checks that this base binds identity — w51's fail-closed stake-deposit identity binding is **not in this tree** (unmerged). **Money-path adjacent: auction stake ledger identity.** |
| C4 | **`EffectBoundary` (would-pay policy gate) is not instantiated anywhere in production** | `src/sincor2/shadow_monitor/effect_boundary.py` (whole module) | The `ShadowEffectBoundary` policy evaluation is fail-closed by design (broken policy → `blocked_policy`, unknown risk tier → deny, kill switch → block would-pay), but the only `EffectBoundary(` construction is inside the module's own factory method. No money path (settlement, payouts, stake) routes through it. A fail-closed gate that nothing calls is **fail-open in practice**. |
| C5 | **Treasury execution is live-by-default; kill switch is file-local and self-clearing-adjacent** | `src/sincor2/agents/treasury_execution_agent.py:56,68-78` | `EXECUTE_LIVE` defaults `"1"` (HOLD lifted by founder). Live mode needs key + no kill switch — correct — but the kill-switch mechanism is a local file any process with write access can delete (`clear_kill_switch()`), and `SAFETY_OVERRIDE=true` bypasses the production on-chain-writes lock globally. Combined with C1 (HTTP kill-switch clear on the sibling trading path), the money path has **two unauthenticated kill-switch resets**. |

---

## 2. Auth & identity

| name | location | protects-what | enforcement | fail-mode | bypass-path | test-coverage | grade |
|------|----------|---------------|-------------|-----------|-------------|---------------|-------|
| `verify_wallet_proof` (EIP-191, mandatory claim match, freshness window) | `src/sincor2/a2a_identity.py:72` | Identity forgery: spoofed agent_id/wallet on heartbeats, registration, transfers | hard (returns None → callers raise) | closed | None code-level; replay outside `IDENTITY_MAX_SKEW_MS` window fails. Key theft is out of scope. | partial | adequate |
| `recover_signer` raises on any failure | `src/sincor2/a2a_identity.py:54` | Malformed-signature confusion; eth_account absence fails closed | hard | closed | None | partial | adequate |
| Heartbeat requires EIP-191 proof (`HeartbeatAuthError` → 401) | `src/sincor2/a2a_inbound_ext.py:325` | Liveness spoofing / fake-death attacks | hard | closed | None code-level; heartbeat is also IP-keyed in rate limiter so bucket poisoning fails | yes | adequate |
| Re-registration requires proof of control by current wallet | `src/sincor2/a2a_inbound_ext.py:336-343` | Agent-identity hijack via record update | hard | closed | None; owner wallet rotation only via owner-signed transfer | partial | adequate |
| Wallet transfer requires current-owner EIP-191 signature | `src/sincor2/a2a_inbound_ext.py:442-455` | Identity theft / agent takeover | hard | closed | None code-level | partial | adequate |
| Reserved platform agent_ids cannot be registered/overwritten via API | `src/sincor2/a2a_inbound_ext.py:315-319` | Platform-identity impersonation | hard | closed | `_internal_reputation` seeding path is server-side only | no | adequate |
| Dispute route: adjudicator EIP-191 signature required | `src/sincor2/a2a_inbound_market.py:1371-1414` | Fake dispute rulings / adjudicator impersonation | hard (400/403) | closed | Single-key adjudicator (by design for demo; documented) | partial | adequate |
| `check_stream_auth`: operator token (hmac compare_digest) or known agent_id | `src/sincor2/a2a_rate_limits.py:407` | Unauthorized SSE stream reads (leak of auction/task data) | hard (401) | closed | Token compare is constant-time; agent_id must already be registered — but see R3: registration itself is weak by default | partial | needs-work |
| `allow_hmac_bids` gate (default deny per PR #254) | `src/sincor2/contract_net.py` core; `src/sincor2/blueprints/contract_net.py:33,51` | Weak HMAC demo signatures on the money path (bids lock stake) | hard at core... | closed at core | **Bypass:** the shipped `/api/contract-net` blueprint hardcodes `allow_hmac_bids=True` ("demo-only" comment). If that blueprint is mounted in prod, the gate is bypassed by route. | partial | needs-work |
| KYA `list_agents` consults `live_statuses()` — revoked agents excluded immediately | `src/sincor2/a2a_inbound_ext.py:520` | Stale-revocation exploitation (revoked agent still bidding) | hard | closed | Depends on revocation source being fresh; heartbeat TTL is the freshness bound | partial | adequate |
| Registration proof OPTIONAL by default (see C3) | `src/sincor2/a2a_inbound_ext.py:163` | — | soft (marks `unverified`) | **open** | Set `SINCOR_REGISTRATION_PROOF_REQUIRED=1` — not set by default | partial | needs-work |
| ADMIN routes: credential-gated + `admin` rate tier | `src/sincor2/a2a_rate_limits.py` policy table | Admin brute-force (sponsored-stake, recovery) | hard | closed | Keys are env-provided; no rotation policy found | no | needs-work |

---

## 3. Rate limiting

| name | location | protects-what | enforcement | fail-mode | bypass-path | test-coverage | grade |
|------|----------|---------------|-------------|-----------|-------------|---------------|-------|
| `A2A_RATE_POLICIES` per-abuse-class tiers (register 5/h+20/d, bid 30/m+300/h, settle 10/m+100/h, heartbeat 20/m+300/h, stream 6/m+60/h, admin 30/m+200/h, issuance 5/h+20/d) | `src/sincor2/a2a_rate_limits.py:60-98` | Sybil registration, bid/commit ledger spam, quote scraping, settlement flooding | hard (429 via `before_request`) | closed | Documented in module docstring: rotating **both** identity and IP evades tiers; IP rotation alone evades IP-keyed tiers. Cost-raiser, not auth. | yes (7) | adequate |
| `ENDPOINT_POLICY` coverage map — every mutating/high-value route has an explicit tier | `src/sincor2/a2a_rate_limits.py:100-174` | Unrated-route gaps | hard | closed | Route added later without a policy entry gets **no limiting** — reviewer checklist, no code assert | yes | needs-work |
| Composite `agent\|ip` bucket key (`a2a_caller_key`) | `src/sincor2/a2a_rate_limits.py:330` | Cross-caller bucket poisoning (spoofer exhausts victim's bid/heartbeat bucket) | hard | closed | See above: identity+IP rotation | yes | adequate |
| Heartbeat token-authed route is IP-keyed (identity rotation can't evade brute-force backstop) | `src/sincor2/a2a_rate_limits.py` docstring + `check_stream_auth` | Heartbeat token brute-force | hard | closed | IP rotation still works | yes | adequate |
| In-memory `RateLimitStore` (durable store = "wave 15", unbuilt) | `src/sincor2/a2a_rate_limits.py:340+` | Multi-process/restart limit evasion | hard (per process) | **open across restarts/workers** | Restart the app or spread traffic across workers to reset buckets | yes | needs-work |

---

## 4. Policy evaluation

| name | location | protects-what | enforcement | fail-mode | bypass-path | test-coverage | grade |
|------|----------|---------------|-------------|-----------|-------------|---------------|-------|
| `GuardrailsEngine._enforce` — strict raises `GuardrailBlock`, non-strict warns only | `src/sincor2/compliance_guardrails.py:236` | FTC/compliance violations in agent-published content, PII storage, payment credentials, crypto promotion, AI disclosure | hard iff strict | strict **iff** `IS_PRODUCTION` | Bypass: `FLASK_ENV=development` (or any non-prod value) → warn-only everywhere. Default env is `"production"` (strict), which is the right default. | none found | adequate |
| `require_guardrails` Flask decorator → 403 `guardrail_block` | `src/sincor2/compliance_guardrails.py:610` | Publish-path content violations | hard | closed | Only protects routes it's applied to; coverage is opt-in | no | needs-work |
| P24 `no_price_talk` `require_clean` — phrase deny-list, raises `PolicyViolation` | `src/sincor2/defi/p24/policy.py:98` | Securities-fraud-shaped token metadata (guaranteed returns, 100x, price predictions) | hard | closed | Phrase-list evasion via paraphrase ("guaranteed 20x", unicode tricks); ruleset versioned for auditability | none found | needs-work |
| P24 screener `DeferredScreener` — denies everything until founder pins screener | `src/sincor2/defi/p24/screener.py:113` | Unscreened token issuance | hard | closed | `configure_screener()` is an importable global — any code path can install a permissive screener; no auth on the config seam | partial | needs-work |
| P20 `DecisionEngine` — provider outage → FAIL, never degrades to PASS | `src/sincor2/defi/compliance_automation.py:380+` | Sanctions/KYC/geo screening bypass during oracle outage | hard | closed | None at Python layer; onchain `ComplianceGuard.sol` is still fail-open (C2) — **inconsistent fail modes across the stack** | partial | adequate (py) / broken (sol) |
| OFAC updater: download/parse failure raises, snapshot untouched | `src/sincor2/defi/ofac_sdn_updater.py:56-78` | Poisoned or partial sanctions list | hard | closed | 48h publication window: snapshot silently goes stale — screen runs against old data with no freshness fail-closed on the *screening path itself* (only the updater is fail-closed) | partial | needs-work |
| Underwriting `Policy` pure checks (mandate_live, kill, ttl, amount, payee, skill) | `src/sincor2/underwriting/policy.py:7` | Overspend, expired mandates, unapproved payees/skills | hard *if caller enforces* | closed | Pure functions — enforcement depends on callers; `toa_adapter` kill-switch handling is **score-based** (`score -= 0.5`, still actionable) not a hard block | partial | needs-work |
| `treasury_policy.convert_before_treasury_if_needed` — fail-closed (`converted=False` until executor armed) | `src/sincor2/treasury_policy.py:144` | Believing fees were converted when they weren't (fee accounting fiction) | hard (advisory signal) | closed | Caller could ignore `converted=False` and record inflow anyway — signal only; also `converted=True` requires verifying ConversionLedger/onchain receipts | partial | adequate |
| `is_conversion_executor_armed` — default False, set only by arming ceremony | `src/sincor2/treasury_policy.py:124` | Premature fee-swap execution with unpinned pool | hard | closed | `SINCOR_FEE_EXECUTOR_ARMED=true` env — settable by anyone with env control; no ceremony attestation checked in code | partial | adequate |
| DeFi lifecycle `gates.evaluate` — stage promotion gates | `src/sincor2/defi/gates.py:352` | Shipping unproven products (working-products-only rule) | hard | closed | Manual gates by design | partial | adequate |
| `require_cash_floor` (P25) | `src/sincor2/defi/p25/guards.py:26` | Over-allocation / no cash reserve | hard (raise) | closed | `floor` param overridable by caller | partial | adequate |
| `require_allowlisted` venues + `check_min_out` (dex aggregator) | `src/sincor2/defi/dex_aggregator.py:398,501` | Sandwich/MEV via rogue venue; slippage theft | hard (revert/raise) | closed | Allowlist content is config — who maintains it is the trust root | partial | adequate |
| `PriceOracle.require_all` — undeclared price need raises `OracleError` | `src/sincor2/defi/price_oracle.py:533` | Trading on unset/stale oracle (fail-open pricing) | hard | closed | `strict` flag is caller-chosen | partial | adequate |
| P26 ranker `gate_stage_evidence` | `src/sincor2/defi/p26/ranker.py:56` | Evidence-free stage promotion | hard | closed | partial | adequate |

---

## 5. Approval flows (human / quorum / multi-sig)

| name | location | protects-what | enforcement | fail-mode | bypass-path | test-coverage | grade |
|------|----------|---------------|-------------|-----------|-------------|---------------|-------|
| `HITLProtocol.escalate` / `claim_request` / `resolve_request` | `src/sincor2/hitl_protocol.py:62-128` | Unsupervised high-risk agent actions | **soft** — in-memory dict, "TODO: notify via webhook/SSE" | **open** (no notification = no human ever sees it; nothing blocks the agent from proceeding — no hard gate coupling escalate→block) | Agent can simply not call `escalate()`; no caller enforces it on any money path found | none found | needs-work |
| `cast_governance_vote` | `src/sincor2/hitl_protocol.py:130` | Unilateral policy changes | soft (append-only list, no quorum logic, no enforcement of outcome) | open | Votes are recorded but nothing tallies or enforces them | no | needs-work |
| Quorum / multi-sig for treasury execution | — (not found) | Single-operator treasury drain | **absent** | open | N/A — no quorum exists; treasury agent is single-key by design | — | needs-work |
| Adjudicator rotation (`rotateAdjudicator` onchain) | contracts (per memory 2026-09-29) | Compromised single-key adjudicator | hard (onchain) | closed | Custody is founder's; rotation is manual | — | adequate |

**Gap:** no approval flow gates any money path in code. `disputes` and `transfer_agent_record` are single-signature (adjudicator / owner wallet). Treasury execution is single-key + kill switch. HITL exists as a recording structure with no blocking semantics.

---

## 6. Kill switches

| name | location | protects-what | enforcement | fail-mode | bypass-path | test-coverage | grade |
|------|----------|---------------|-------------|-----------|-------------|---------------|-------|
| Treasury exec `kill_switch_tripped()` → `run_cycle` refuses all action, audits `blocked_kill_switch` | `src/sincor2/agents/treasury_execution_agent.py:68,203-207` | Runaway treasury allocation | hard | closed | `clear_kill_switch()` deletes a local file — anyone/anything with filesystem write access re-arms. `TREASURY_EXEC_HALT_FILE` env can point the check at a different path than the tripper used (split-brain). | none found | needs-work |
| `safety_locks.onchain_writes_allowed()` — prod blocks onchain writes unless `SAFETY_OVERRIDE=true` | `src/sincor2/safety_locks.py:16` | Accidental fund movement / secret exposure in prod | hard | closed | `SAFETY_OVERRIDE=true` env — single env var disables all production locks | none found | adequate |
| `assert_production_safety()` startup warnings | `src/sincor2/safety_locks.py:23` | Dangerous prod config (EXECUTE_LIVE=1, forwarder key present) | **soft (warn only)** | open | Warnings logged; app continues. `EXECUTE_LIVE=1` in prod is *warned*, not blocked | none found | needs-work |
| Shadow `KillSwitch` — engaged blocks `would_pay` proposal recording | `src/sincor2/shadow_monitor/boundary.py:211-249` | Shadow-mode leakage into real payments | hard (raises `ShadowBoundaryViolation`) | closed | Starts **disengaged**; `disengage()` is unauthenticated; no remote/operator tripwire wired; only blocks *recording*, not live effects (live is structurally unreachable anyway) | yes (boundary 18) | needs-work |
| EffectBoundary kill switch → `blocked_policy` for would-pay kinds | `src/sincor2/shadow_monitor/effect_boundary.py:469` | Value-moving effects under kill | hard by design | **open in practice** (C4 — not wired to any production path) | N/A | none found | broken |
| Bankroll kill switch (sqlite-persisted) | `src/sincor2/bankroll.py:175-182` | Trading beyond risk limits | hard | closed | **C1/C2-adjacent:** auto-cleared by `clear-dry-runs` flow (`bankroll.py:135-136`, `monitoring.py:195`) over unauthenticated HTTP | partial | broken |
| P26 kill switch — advisory `KILL_TICK` records only, `executed` always False | `src/sincor2/defi/p26/killswitch.py:48` | Negative-ROI protocol allocation | **soft (advisory)** | open | "Execution (stopping allocation) is the operator's path" — nobody's path in code | none found | needs-work |

---

## 7. Circuit breakers

| name | location | protects-what | enforcement | fail-mode | bypass-path | test-coverage | grade |
|------|----------|---------------|-------------|-----------|-------------|---------------|-------|
| `CircuitBreaker` — opens after N consecutive failures, half-open probe, fail-fast `CircuitOpenError` | `src/sincor2/resilience/auto_heal.py:272-360` | Cascading failures / hammering dead endpoints | hard | closed | `reset()` is an unauthenticated in-process call — fine as library, dangerous if exposed via admin API without auth | partial (1) | adequate |
| `AutoHealCoordinator` — breaker → retry w/ backoff → classify → optional `recovery` callable; re-raises after exhaustion | `src/sincor2/resilience/auto_heal.py:510` | Unhandled transient failures | hard | closed | `recovery` callable is domain-supplied and arbitrary (could re-register agents, flush queues) — no allowlist on what recovery may do | partial | needs-work |
| `RetryPolicy` bounded backoff | `src/sincor2/resilience/auto_heal.py:374` | Retry storms | hard | closed | Caller-chosen params | partial | adequate |
| `PersistentErrorTracker` | `src/sincor2/resilience/auto_heal.py:134` | Silent failure recurrence | soft (observability) | n/a | — | partial | adequate |

---

## 8. Shadow boundary (external-comms & credential safety)

| name | location | protects-what | enforcement | fail-mode | bypass-path | test-coverage | grade |
|------|----------|---------------|-------------|-----------|-------------|---------------|-------|
| `ShadowBoundary.execute()` always raises in shadow; only `propose()` records | `src/sincor2/shadow_monitor/boundary.py:357` | Real outbound side effects (email, social, CRM, trade, transfer, contract call) during shadow phase | hard | closed | Construct `ShadowBoundary(shadow=False)` — the flag is a constructor arg, no env/operator lock. Any code path can instantiate a live boundary. | yes (18) | needs-work |
| `check_effective_mode` — raises if not shadow or any adapter unwrapped | `src/sincor2/shadow_monitor/boundary.py:525` | Drifted/unwrapped adapters, accidental live config | hard (raises) | closed | Only runs if called — no startup/boot enforcement found | partial | needs-work |
| `ShadowCredentials.validate()` rejects key-like values; scope must be `read-only` | `src/sincor2/shadow_monitor/boundary.py:190` | Secret leakage into shadow adapters | hard | closed | Regex-based; a novel secret format could pass — deliberately broad, acceptable | yes | adequate |
| `ACTUAL_SIDE_EFFECTS` invariant counter | `src/sincor2/shadow_monitor/boundary.py:82` | Undetected real effects | soft (observability) | n/a | — | yes | adequate |
| `emit_heartbeat` exposes effective mode | `src/sincor2/shadow_monitor/boundary.py:498` | Silent mode drift | soft | n/a | — | yes | adequate |

---

## 9. Money-path execution guards (treasury / fees / staking)

| name | location | protects-what | enforcement | fail-mode | bypass-path | test-coverage | grade |
|------|----------|---------------|-------------|-----------|-------------|---------------|-------|
| `TreasuryExecutionAgent.is_live_capable()` = EXECUTE_LIVE ∧ key ∧ ¬kill_switch | `src/sincor2/agents/treasury_execution_agent.py:178` | Unintended live broadcast | hard | closed | **EXECUTE_LIVE defaults ON** (`os.getenv("EXECUTE_LIVE","1")`) — a leaked key alone arms live mode; key resolution tries 3 env aliases | none found | needs-work |
| Hard daily cap $150 / single-tx $110 / min-capital $20 | `src/sincor2/agents/treasury_execution_agent.py:53-55,210-231` | Oversized treasury moves | hard | closed | Env-overridable (operator-controlled); fine | none found | adequate |
| `WHITELIST_TARGETS` contract whitelist | `src/sincor2/agents/treasury_execution_agent.py:48` | Rogue contract interaction | soft | **open** | Whitelist is **defined but never enforced** in `run_cycle`: non-vault strategies get target `"morpho_or_equivalent"` (a free-form string). Dead control. | none found | broken |
| Default mode = INTENT_QUEUE (no broadcast) until live-capable | `src/sincor2/agents/treasury_execution_agent.py:248-280` | Accidental broadcast | hard | closed | See `is_live_capable` bypass | none found | adequate |
| Private key never logged | `src/sincor2/agents/treasury_execution_agent.py` docstring + `_resolve_key` | Key exposure in logs | hard (by omission) | n/a | `status()` returns `key_present` bool only — good | none found | adequate |
| Stake-deposit identity binding (w51, fail-closed EIP-191) | — | Stake ledger identity spoofing | — | — | **Not in this tree** (buildout-51 unmerged at base b0f5f92). Gap vs. C3. | — | broken (missing) |
| Slash proceeds → poster re-auction fund; adjudicator-only slashing (per PR #261-264) | `stake_ledger.py` (referenced; file not present in this tree listing — verify in later base) | Slash theft / unauthorized slashing | — | — | Could not verify in this worktree | — | unverified |

---

## 10. External comms (outreach)

| name | location | protects-what | enforcement | fail-mode | bypass-path | test-coverage | grade |
|------|----------|---------------|-------------|-----------|-------------|---------------|-------|
| `send_outreach_email` runs `guardrails.check_email_send` before send; block → no send | `src/sincor2/outreach_engine.py:155-184` | Spam/abusive bulk email | hard | closed | Broad `except Exception` around the whole send just logs — safe direction | partial (2) | adequate |
| `OUTREACH_DAILY_LIMIT` (default 40) | `src/sincor2/outreach_engine.py:46` | Runaway bulk mailing | hard | closed | Env-overridable | partial | adequate |
| `_autonomous_on()` — autonomous outreach **defaults ON** (`AUTONOMOUS_AGENTS` unset → `"true"`) | `src/sincor2/outreach_engine.py:22` | Unsupervised external email | hard default-on | **open by default** | Unset env = autonomous mass email active. Safety path: external comms should be opt-in. | partial | needs-work |
| `_guess_email` / `_scrape_emails` — guessed addresses | `src/sincor2/outreach_engine.py:115-135` | Emailing wrong recipients (spam/consent) | none | open | No consent or bounce handling visible | no | needs-work |

---

## Summary counts

| Grade | Count |
|-------|-------|
| adequate | 29 |
| needs-work | 22 |
| broken | 5 |
| unverified | 1 |
| **Total rules audited** | **57** |

**CRITICAL fail-open findings: 5** — C1 (unauthenticated kill-switch clear via `POST /api/polyclaw/clear-dry-runs`), C2 (`ComplianceGuard.sol` fail-open without oracle; ratified flip not implemented), C3 (registration accepts unverified wallet claims by default + w51 stake binding missing), C4 (`EffectBoundary` unwired to any production money path), C5 (treasury live-by-default + file-local kill switch + `SAFETY_OVERRIDE` single-var bypass).

**Broken (non-critical but dead controls):** `WHITELIST_TARGETS` never enforced in `run_cycle`; P26 kill switch advisory-only with no execution path; w51 stake-deposit identity binding absent from this base.

**Recommended founder decisions before launch:**
1. Auth-gate or remove `POST /api/polyclaw/clear-dry-runs`; never auto-clear a kill switch without explicit operator action.
2. Implement the ratified P20 fail-closed flip in `ComplianceGuard.sol` (or document that the Python oracle is the enforcement point and the contract is not deployed).
3. Set `SINCOR_REGISTRATION_PROOF_REQUIRED=1` in production, or merge w51's stake-deposit identity binding and document which layer owns identity on the money path.
4. Either wire `EffectBoundary` into settlement/payout paths or delete it — an unwired fail-closed gate is a false sense of security.
5. Flip `EXECUTE_LIVE` default to off and require the arming ceremony to set it; audit who can set `SAFETY_OVERRIDE`.
6. Decide: is HITL blocking or recording? If blocking, couple `escalate()` to a hard gate on money paths; if recording, say so.
