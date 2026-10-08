# SINCOR2 Guardrail Catalog

Every action in the [Action Catalog](ACTION_CATALOG.md) with its guardrails: pre-conditions, policy checks, approval, rate limits, audit, post-conditions, failure handling, and reversibility.

Machine-readable source of truth: `src/sincor2/governance/guardrails.py` (`get_guardrail`, `guardrails_by_risk`, `validate_coverage`, `summary`). `validate_coverage()` is enforced programmatically: **zero gaps, zero orphans**.

## Summary

| Risk tier | Actions | Approval | Audit |
|---|---|---|---|
| **critical** | 24 | 21 human, 3 quorum | DecisionEvent required |
| **high** | 38 | 38 human | DecisionEvent required |
| **medium** | 32 | 32 auto | DecisionEvent required |
| **low** | 17 | 17 auto | optional |
| **total** | **111** | | |

**Coverage:** 111/111 actions have guardrails (validated: 111 entries, 0 gaps, 0 orphans). **POLICY-MISSING flags:** 58 actions carry at least one `POLICY-MISSING` marker — a policy check the codebase does not yet implement as a dedicated module. Each marker names what is needed.

### Approval counts (high + critical)

| Tier | human | quorum | total |
|---|---|---|---|
| critical | 21 | 3 | 24 |
| high | 38 | 0 | 38 |

All 24 critical and all 38 high actions carry human or quorum approval. For autonomous protocol mechanics (auction commit/reveal/close, stake deposit) the human is the agent's human owner / the task poster acting through pre-authorized standing approval envelopes; every execution logs a DecisionEvent with `human_disposition="not_reviewed"` for post-hoc review, and anything outside the envelope requires fresh interactive approval.

### Failure handling (universal)

Every guardrail is **fail-closed**: any failed pre-condition or policy check blocks the action before side effects (no partial effects), returns 403/429, raises an alert via `sincor2.shadow_monitor.alerting`, and logs a DecisionEvent with `policy_result="deny"`.

### Audit standard

Audit uses the `DecisionEvent` schema from `src/sincor2/shadow_monitor/events.py` (append-only, hash-chained JSONL store): `proposed_action`, `risk_tier`, `policy_result`, `policy_reason_codes`, `human_disposition`, `outcome_evidence`, `blocked_in_live_mode`. High-cardinality identifiers stay in log lines; customer identifiers are redacted via `events.redact()`.

### Pre-condition vocabulary

`agent_registered` · `kya_verified` · `kya_heartbeat_fresh` · `eip191_proof_valid` · `stake_sufficient` · `pool_balance_sufficient` · `pool_allocation_valid` · `agent_not_killed` · `admin_key_present` · `operator_key_present` · `adjudicator_signature_valid` · `provider_signature_valid` · `stripe_sig_valid` · `paypal_sig_valid` · `session_owner_verified` · `api_key_valid` · `x402_access_token_valid` · `idempotency_key_present` · `deadline_passed`/`deadline_not_passed` · `commit_phase_open` · `reveal_phase_open` · `auction_window_closed` · `hold_approved` · `hold_fresh` · `quorum_reached` · `executive_key_present` · `exec_live_armed` · `killswitch_clear` · `ofac_screen_clean` · `content_screen_clean` · `p24_live_allowed` · `task_has_no_bids` · `allocation_released` · `double_claim_guard_pass` · `sender_is_poster_or_admin` · `owner_signature_valid` · `cooldown_elapsed` · `rate_limit_ok` · `not_in_shadow_mode` · `standing_approval_envelope_ok`

### Real policy references

- `sincor2.a2a_identity.verify_wallet_proof` — EIP-191 identity proof (fail-closed)
- `sincor2.a2a_rate_limits.*` — sliding-window enforcement (`A2A_RATE_POLICIES`: `register`, `bid`, `quote`, `dispute`, `issuance`, `read`, `settle`, `task_write`, `task_msg`, `heartbeat`, `stream`, `admin`)
- `sincor2.shadow_monitor.effect_boundary.ShadowEffectBoundary.evaluate` / `default_shadow_policy` — shadow effect gating
- `sincor2.compliance_guardrails.GuardrailsEngine.check_email_send` / `check_content` / `check_pii_storage` — comms & content screening
- `sincor2.defi.p24.policy.require_clean` — P24 content screening
- `sincor2.defi.ofac_sdn.screen_wallet_address` — sanctions screening
- `sincor2.treasury_policy.TreasuryPolicy` — fee conversion policy
- `sincor2.defi.treasury_dao.Hold/Reviewer/Executor` — treasury review lifecycle
- `sincor2.defi.gates.next_stage` — DeFi strategy stage gates

---

## CRITICAL actions

Moves money, sends external comms, changes auth/permissions, or touches treasury. **Human approval REQUIRED** (quorum for treasury movements).

| Action | Domain | Approval | Rate limits | Reversible |
|---|---|---|---|---|
| `crypto_verify_payment` | payments | human | PROPOSED: money_verify | **irreversible** |
| `defi_execute_strategy` | defi | human | PROPOSED: defi_live | **irreversible** |
| `effect_contract_call` | defi | human | PROPOSED: shadow_effect | **irreversible** |
| `effect_email_send` | payments | human | PROPOSED: shadow_effect | **irreversible** |
| `effect_payment_transfer` | payments | human | PROPOSED: shadow_effect | **irreversible** |
| `effect_social_post` | communications | human | PROPOSED: shadow_effect | **irreversible** |
| `effect_trade_swap` | defi | human | PROPOSED: shadow_effect | **irreversible** |
| `file_dispute` | marketplace | human | dispute | reversible-with-cost |
| `killswitch` | admin | human | admin | reversible |
| `kya_revoke` | identity | human | PROPOSED: kya_admin | reversible |
| `launch_review_approve_post` | communications | human | PROPOSED: publish | **irreversible** |
| `payment_webhook` | payments | human | PROPOSED: webhook | reversible |
| `paypal_create_order` | payments | human | PROPOSED: checkout | reversible |
| `paypal_webhook` | payments | human | PROPOSED: webhook | reversible |
| `run_outreach_cycle` | communications | human | PROPOSED: outreach_cycle | **irreversible** |
| `send_email` | communications | human | PROPOSED: email | **irreversible** |
| `send_outreach_email` | communications | human | PROPOSED: outreach | **irreversible** |
| `stripe_create_checkout` | payments | human | PROPOSED: checkout | reversible |
| `stripe_webhook` | payments | human | PROPOSED: webhook | reversible |
| `treasury_dao_claim_fees` | payments | quorum | PROPOSED: treasury_live | **irreversible** |
| `treasury_dao_execute` | payments | quorum | PROPOSED: treasury_live | **irreversible** |
| `treasury_intent_allocate_live` | payments | quorum | PROPOSED: treasury_live | **irreversible** |
| `uw_revoke_agent` | identity | human | PROPOSED: kya_admin | reversible |
| `x402_verify_payment` | payments | human | PROPOSED: money_verify | **irreversible** |

### `crypto_verify_payment`

Entry point: `src/sincor2/mvp_blueprints/billing.py:904` · HTTP: `POST /api/crypto/verify-payment` · Auth: `['none']`

**Approval:** human — Verification math may run automatically, but granting value (provisioning) requires human approval: mis-verification is the money-path risk. Human disposition recorded on the DecisionEvent.

**Pre-conditions:** not_in_shadow_mode, rate_limit_ok, idempotency_key_present

**Policy checks:**<br>- onchain payment verification against the claimed tx_hash (mvp_blueprints/billing.py)<br>- sincor2.defi.ofac_sdn.screen_wallet_address (payer screening)<br>- POLICY-MISSING: mis-verification circuit breaker (re-check count, confirmation depth policy) — currently ad hoc

**Rate limits:**<br>`PROPOSED: money_verify`<br>**per_ip**: 10/min<br>**per_tenant**: 100/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; claimed tx_hash, chain, confirmations, screened payer, provisioned entitlements, human_disposition

**Post-conditions:**<br>- onchain payment confirmed at required depth<br>- access provisioned exactly once (idempotency key)

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; failed verification NEVER provisions access

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A — provisioning is effectively final. Mitigation is pre-action: human approval + confirmation-depth verification before grant.

### `defi_execute_strategy`

Entry point: `src/sincor2/defi/lending_optimizer.py:655` · HTTP: `n/a (library)` · Auth: `['operator']`

**Approval:** human — Human operator approves the live strategy execution: scan/plan ticks are automated (defi_swarm_tick), but EXECUTION moving real funds onchain requires human approval per strategy run.

**Pre-conditions:** operator_key_present, exec_live_armed, killswitch_clear, ofac_screen_clean, not_in_shadow_mode, rate_limit_ok, standing_approval_envelope_ok

**Policy checks:**<br>- sincor2.defi.gates.next_stage (strategy lifecycle gates: spec, implementation, unit tests, invariant tests, fork sim)<br>- sincor2.defi.ofac_sdn.screen_wallet_address (protocol counterparty)<br>- sincor2.shadow_monitor.effect_boundary.default_shadow_policy (trade.swap/contract.call would-pay path)

**Rate limits:**<br>`PROPOSED: defi_live`<br>**per_operator**: 10/hour<br>**per_tenant**: 50/day

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; strategy id, protocol, calldata hash, amounts, gate evidence, chain receipt, human_disposition

**Post-conditions:**<br>- chain receipt confirmed<br>- strategy state reflects execution; no duplicate execution

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; any gate or screen failure keeps the strategy in plan-only mode; nothing broadcast

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A — onchain fund movement. Mitigation: stage gates + human approval + kill switch blocking new executions.

### `effect_contract_call`

Entry point: `src/sincor2/shadow_monitor/effect_boundary.py:92` · HTTP: `n/a (shadow intent)` · Auth: `['shadow policy']`

**Approval:** human — Shadow intent (WOULD_PAY). Live promotion requires human approval.

**Pre-conditions:** not_in_shadow_mode

**Policy checks:**<br>- sincor2.shadow_monitor.effect_boundary.ShadowEffectBoundary.evaluate (contract.call -> WOULD_PAY; deny in shadow)

**Rate limits:**<br>`PROPOSED: shadow_effect`<br>**per_agent**: 50/day

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; effect type contract.call, intent hash, blocked_in_live_mode=true

**Post-conditions:**<br>- intent recorded with receipt; no live side effect

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A. Shadow never executes; live needs human approval.

### `effect_email_send`

Entry point: `src/sincor2/shadow_monitor/effect_boundary.py:92` · HTTP: `n/a (shadow intent)` · Auth: `['shadow policy']`

**Approval:** human — Shadow-boundary intent: may only be PROPOSED/recorded in shadow mode — never executed live without a separately deployed executor. Live promotion requires human approval.

**Pre-conditions:** not_in_shadow_mode

**Policy checks:**<br>- sincor2.shadow_monitor.effect_boundary.ShadowEffectBoundary.evaluate (default_shadow_policy: email.send -> deny in shadow; needs_approval for live promotion)

**Rate limits:**<br>`PROPOSED: shadow_effect`<br>**per_agent**: 100/day<br>**note**: proposals only; execution is gated separately

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; effect type email.send, intent hash, blocked_in_live_mode=true while in shadow

**Post-conditions:**<br>- intent recorded with receipt (proposed, not executed)<br>- no live side effect performed

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; ShadowBoundaryViolation on any live-execution attempt

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A at the effect level. Guardrail: shadow mode never executes; live promotion needs a separately deployed executor + human approval.

### `effect_payment_transfer`

Entry point: `src/sincor2/shadow_monitor/effect_boundary.py:92` · HTTP: `n/a (shadow intent)` · Auth: `['shadow policy']`

**Approval:** human — Shadow intent (WOULD_PAY). Live promotion requires human approval AND would inherit the treasury quorum path for real funds.

**Pre-conditions:** not_in_shadow_mode

**Policy checks:**<br>- sincor2.shadow_monitor.effect_boundary.ShadowEffectBoundary.evaluate (payment.transfer -> WOULD_PAY kill-switch priority; deny in shadow)<br>- sincor2.defi.ofac_sdn.screen_wallet_address (destination, on live promotion)

**Rate limits:**<br>`PROPOSED: shadow_effect`<br>**per_agent**: 50/day

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; effect type payment.transfer, intent hash, amount, destination (redacted), blocked_in_live_mode=true

**Post-conditions:**<br>- intent recorded with receipt; no live side effect

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; kill switch blocks would-pay intents first

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A. Shadow never executes; live promotion inherits money-path quorum.

### `effect_social_post`

Entry point: `src/sincor2/shadow_monitor/effect_boundary.py:92` · HTTP: `n/a (shadow intent)` · Auth: `['shadow policy']`

**Approval:** human — Shadow intent; live promotion requires human approval + separately deployed executor.

**Pre-conditions:** not_in_shadow_mode

**Policy checks:**<br>- sincor2.shadow_monitor.effect_boundary.ShadowEffectBoundary.evaluate (social.post -> deny in shadow; needs_approval for live)

**Rate limits:**<br>`PROPOSED: shadow_effect`<br>**per_agent**: 100/day

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; effect type social.post, intent hash, blocked_in_live_mode=true

**Post-conditions:**<br>- intent recorded with receipt; no live side effect

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A. Shadow never executes; live needs human approval.

### `effect_trade_swap`

Entry point: `src/sincor2/shadow_monitor/effect_boundary.py:92` · HTTP: `n/a (shadow intent)` · Auth: `['shadow policy']`

**Approval:** human — Shadow intent (WOULD_PAY). Live promotion requires human approval.

**Pre-conditions:** not_in_shadow_mode

**Policy checks:**<br>- sincor2.shadow_monitor.effect_boundary.ShadowEffectBoundary.evaluate (trade.swap -> WOULD_PAY; deny in shadow)

**Rate limits:**<br>`PROPOSED: shadow_effect`<br>**per_agent**: 50/day

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; effect type trade.swap, intent hash, blocked_in_live_mode=true

**Post-conditions:**<br>- intent recorded with receipt; no live side effect

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A. Shadow never executes; live needs human approval.

### `file_dispute`

Entry point: `src/sincor2/a2a_inbound_market.py:1361` · HTTP: `POST /v1/a2a/disputes` · Auth: `['adjudicator (EIP-191 secp256k1)']`

**Approval:** human — The adjudicator's EIP-191 signature IS the human approval: the adjudicator is the designated human keyholder (Sepolia demo address 0x6AF997C5ba8db8153C95dd837b5555Ef5867cE3f).

**Pre-conditions:** adjudicator_signature_valid, eip191_proof_valid, agent_registered, auction_window_closed, rate_limit_ok

**Policy checks:**<br>- sincor2.a2a_identity.verify_wallet_proof (adjudicator EIP-191 secp256k1)<br>- sincor2.a2a_rate_limits.dispute (5/hour + 20/day per IP backstop)<br>- POLICY-MISSING: slashing-amount policy (ghosting 100% / quality 50% slash ratios currently live in a2a_inbound_market constants, no dedicated policy module)

**Rate limits:**<br>`dispute`<br>**windows**: 5/hour + 20/day per IP<br>**note**: adjudicator-signed already; tier is a backstop (a2a_rate_limits.A2A_RATE_POLICIES['dispute'])

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; upheld flag, slash amount, slashed agent_id, adjudicator wallet, signature digest

**Post-conditions:**<br>- dispute record persisted with adjudicator signature<br>- upheld=true -> 50% of winner stake slashed to poster re-auction credit + agent reputation halved<br>- upheld=false -> winner stake released

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible-with-cost

**Reversibility plan:** Slash reversal requires a second adjudicator ruling crediting the slashed stake back; reputation half-loss is recomputed, not refunded in kind — plan recorded before execution.

### `killswitch`

Entry point: `src/sincor2/blueprints/command_center.py:461` · HTTP: `POST /api/command-center/killswitch/<agent_id>` · Auth: `['none visible at route level']`

**Approval:** human — Human operator kills or reinstates an agent. AUTH GAP: guardrail REQUIRES the admin gate to be implemented before this action is exposed.

**Pre-conditions:** admin_key_present, rate_limit_ok, not_in_shadow_mode

**Policy checks:**<br>- POLICY-MISSING: kill-switch auth policy — AUTH GAP: no route-level gate found in this tree; must be admin-gated before exposure (see docs/governance/ACTION_CATALOG.md)

**Rate limits:**<br>`admin`<br>**windows**: 30/min + 200/hour<br>**note**: a2a_rate_limits admin tier (brute-force backstop); the admin credential is the real gate

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; target agent_id, kill/reinstate, admin identity, reason

**Post-conditions:**<br>- agent status flipped in registry<br>- fleet state confirms kill/reinstate (no stale liveness)

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; unauthenticated calls denied; kill state never half-applied

**Reversibility:** reversible

**Reversibility plan:** Reinstate via the same endpoint with human approval; reinstate is audited identically.

### `kya_revoke`

Entry point: `src/sincor2/kya_blueprint.py:106` · HTTP: `POST /api/kya/revoke` · Auth: `['none']`

**Approval:** human — Human admin revokes a KYA identity; agent is excluded from discovery immediately. AUTH GAP: guardrail REQUIRES admin gating first.

**Pre-conditions:** admin_key_present, agent_registered, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: revocation policy (who may revoke which identity, appeal/cool-down) — AUTH GAP: no route-level auth visible; must be admin-gated before exposure

**Rate limits:**<br>`PROPOSED: kya_admin`<br>**per_admin**: 60/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; revoked agent_id, reason, admin identity, human_disposition

**Post-conditions:**<br>- identity marked revoked<br>- list_agents() excludes the agent immediately (live_statuses consulted)

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; unauthenticated revocation denied outright

**Reversibility:** reversible

**Reversibility plan:** Re-verify via kya_verify with human approval; revocation history retained in audit log.

### `launch_review_approve_post`

Entry point: `src/sincor2/mvp_blueprints/launch.py:100` · HTTP: `POST /api/launch/review/<draft_id>` · Auth: `['admin']`

**Approval:** human — Admin approval AND public posting in one action: the admin's review click is the human approval. Public comms cannot be unsent.

**Pre-conditions:** admin_key_present, content_screen_clean, not_in_shadow_mode, rate_limit_ok

**Policy checks:**<br>- sincor2.compliance_guardrails.GuardrailsEngine.check_content (authenticity, claims, crypto-promotion)<br>- sincor2.shadow_monitor.effect_boundary.default_shadow_policy (social.post -> needs_approval)

**Rate limits:**<br>`PROPOSED: publish`<br>**per_admin**: 20/day

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; draft id, channel, posted content hash, human_disposition=accepted

**Post-conditions:**<br>- post published (provider post id returned)<br>- draft marked posted; no double-post

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; draft stays unposted on any check failure

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A — public post. Mitigation: draft review separate from posting; human approval gate before the post call.

### `payment_webhook`

Entry point: `src/sincor2/mvp_blueprints/billing.py:265` · HTTP: `POST /api/payment/webhook` · Auth: `['provider_sig']`

**Approval:** human — Provider signature gates intake; human approval required for value grants above the standing threshold.

**Pre-conditions:** provider_signature_valid, not_in_shadow_mode, rate_limit_ok

**Policy checks:**<br>- provider signature verification for the configured provider<br>- POLICY-MISSING: generic provisioning policy (provider -> entitlement mapping, thresholds)

**Rate limits:**<br>`PROPOSED: webhook`<br>**per_ip**: 60/min

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; provider, event id, type, human_disposition

**Post-conditions:**<br>- event id recorded (replay-safe)<br>- provisioning applied exactly once

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Provider-side refund/void; access revoked.

### `paypal_create_order`

Entry point: `src/sincor2/blueprints/payments.py:41` · HTTP: `POST /api/payments/paypal/create-order` · Auth: `['none']`

**Approval:** human — The initiating customer is the human approver; order creation is customer-initiated only, no auto-capture.

**Pre-conditions:** rate_limit_ok, not_in_shadow_mode

**Policy checks:**<br>- POLICY-MISSING: paypal order-intent policy (amount caps, currency allowlist) — no dedicated policy module

**Rate limits:**<br>`PROPOSED: checkout`<br>**per_ip**: 20/min<br>**per_tenant**: 200/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; paypal order id, amount, currency

**Post-conditions:**<br>- PayPal order created (provider-side), idempotency-keyed

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Order voided before capture via PayPal API.

### `paypal_webhook`

Entry point: `src/sincor2/mvp_blueprints/billing.py:1168` · HTTP: `POST /api/paypal/webhook` · Auth: `['paypal_sig']`

**Approval:** human — PayPal's signature gates intake; value-granting requires human approval above the standing auto-provision threshold; below threshold auto-provision with post-hoc review queue.

**Pre-conditions:** paypal_sig_valid, not_in_shadow_mode, rate_limit_ok

**Policy checks:**<br>- PayPal IPN/webhook signature verification (mvp_blueprints/billing.py)<br>- POLICY-MISSING: provisioning policy (event-type -> entitlement mapping, value thresholds for human disposition)

**Rate limits:**<br>`PROPOSED: webhook`<br>**per_ip**: 60/min

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; paypal event id, event type, idempotency key, human_disposition

**Post-conditions:**<br>- event id recorded (replay-safe)<br>- provisioning applied exactly once

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Reverse via PayPal refund; access revoked.

### `run_outreach_cycle`

Entry point: `src/sincor2/mvp_blueprints/ops.py:204` · HTTP: `POST /api/outreach/run` · Auth: `['admin']`

**Approval:** human — Admin manually triggers one full outreach cycle; the admin's action IS the human approval for the cycle. Each individual send still passes check_email_send.

**Pre-conditions:** admin_key_present, rate_limit_ok, content_screen_clean, not_in_shadow_mode

**Policy checks:**<br>- sincor2.compliance_guardrails.GuardrailsEngine.check_email_send (per recipient)<br>- sincor2.compliance_guardrails.GuardrailsEngine.check_content

**Rate limits:**<br>`PROPOSED: outreach_cycle`<br>**per_admin**: 4/day<br>**note**: one cycle may send many emails; per-email tier applies too

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; cycle id, recipient count, per-send policy results, human_disposition=accepted (admin trigger)

**Post-conditions:**<br>- cycle completed; per-recipient outcomes recorded<br>- failed sends retried or dead-lettered, never silently dropped

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; cycle aborts on policy failure; already-sent emails are logged, unsent remainder is blocked

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A — delivered emails cannot be recalled. Mitigation: admin trigger + per-send screening.

### `send_email`

Entry point: `src/sincor2/email_sender.py:200` · HTTP: `n/a (library)` · Auth: `['operator']`

**Approval:** human — Generic email primitive used by signup/billing/outreach: each send path must carry its own human approval (customer signup, billing trigger, or operator-confirmed outreach).

**Pre-conditions:** operator_key_present, rate_limit_ok, content_screen_clean, not_in_shadow_mode

**Policy checks:**<br>- sincor2.compliance_guardrails.GuardrailsEngine.check_email_send<br>- sincor2.compliance_guardrails.GuardrailsEngine.check_content

**Rate limits:**<br>`PROPOSED: email`<br>**per_agent**: 100/day<br>**per_tenant**: 2000/day

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; recipient (redacted), purpose, template, provider message id, human_disposition

**Post-conditions:**<br>- provider accepted the message<br>- audit record redacted of raw PII

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A — cannot be unsent. Mitigation: per-path human approval + screening.

### `send_outreach_email`

Entry point: `src/sincor2/outreach_engine.py:155` · HTTP: `n/a (engine method)` · Auth: `['operator (autonomous toggle)']`

**Approval:** human — Human approval required: cold-outreach email to third parties is irreversible once delivered. Autonomous toggle must itself be human-armed.

**Pre-conditions:** operator_key_present, rate_limit_ok, content_screen_clean, not_in_shadow_mode

**Policy checks:**<br>- sincor2.compliance_guardrails.GuardrailsEngine.check_email_send<br>- sincor2.compliance_guardrails.GuardrailsEngine.check_content (authenticity, claims, competitor-comparison, crypto-promotion)<br>- sincor2.shadow_monitor.effect_boundary.default_shadow_policy (email.send -> kill-switch priority path)

**Rate limits:**<br>`PROPOSED: outreach`<br>**per_agent**: 50/day<br>**per_tenant**: 500/day<br>**note**: backstops the background outreach engine

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; recipient (redacted via shadow_monitor/events.redact), template id, campaign, provider message id, human_disposition

**Post-conditions:**<br>- provider accepted the message (message id returned)<br>- no raw PII in the audit record (redact() applied)

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; send aborted before provider handoff; queued message dropped, alert raised

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A — email cannot be unsent. Mitigation: human approval + content screening + recipient allowlist before send.

### `stripe_create_checkout`

Entry point: `src/sincor2/stripe_routes.py:96` · HTTP: `POST /api/stripe/checkout` · Auth: `['none']`

**Approval:** human — The initiating customer is the human approver: a checkout session is never created except from a customer-initiated request; the session URL is never auto-charged.

**Pre-conditions:** rate_limit_ok, not_in_shadow_mode

**Policy checks:**<br>- POLICY-MISSING: checkout-intent policy (amount caps, currency allowlist, product mapping to /signup?plan=<product>) — no dedicated policy module; route-level checks only

**Rate limits:**<br>`PROPOSED: checkout`<br>**per_ip**: 20/min<br>**per_tenant**: 200/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; stripe session id, amount, currency, product, success/cancel URLs

**Post-conditions:**<br>- Stripe session object created (provider-side)<br>- session id stored with idempotency key; no charge yet

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Session expires unused; Stripe dashboard expire/cancel before completion.

### `stripe_webhook`

Entry point: `src/sincor2/stripe_routes.py:141` · HTTP: `POST /api/stripe/webhook` · Auth: `['stripe_sig']`

**Approval:** human — Stripe's signature gates INTAKE (provider counterparty approval). Value-granting (provisioning access) requires human approval above the standing auto-provision threshold; below threshold, auto-provision logs DecisionEvent with human_disposition="not_reviewed" for review queue.

**Pre-conditions:** stripe_sig_valid, not_in_shadow_mode, rate_limit_ok

**Policy checks:**<br>- sincor2.stripe_routes webhook signature verification (Stripe-Signature header, endpoint secret)<br>- POLICY-MISSING: provisioning policy (what each event type is allowed to provision, and value thresholds requiring human disposition) — currently implicit in the handler

**Rate limits:**<br>`PROPOSED: webhook`<br>**per_ip**: 60/min<br>**note**: provider-signed; tier guards replay storms

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; stripe event id, event type, idempotency key, provisioned entitlements, human_disposition

**Post-conditions:**<br>- event id recorded (replay-safe)<br>- provisioning applied exactly once (idempotency key)

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Provisioned access revoked via refund/void flow; event replay rejected by idempotency key.

### `treasury_dao_claim_fees`

Entry point: `src/sincor2/defi/treasury_dao.py:267` · HTTP: `n/a (library)` · Auth: `['operator']`

**Approval:** quorum — QUORUM: claims accrued fees to the treasury; onchain, irreversible.

**Pre-conditions:** quorum_reached, executive_key_present, killswitch_clear, not_in_shadow_mode

**Policy checks:**<br>- sincor2.defi.treasury_dao.YieldLedger (accrued-fee accounting)<br>- sincor2.treasury_policy.TreasuryPolicy (conversion policy)

**Rate limits:**<br>`PROPOSED: treasury_live`<br>**per_operator**: 5/hour<br>**per_tenant**: 20/day

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; claimed amounts, destination, quorum signatures, chain receipt

**Post-conditions:**<br>- chain receipt confirmed<br>- treasury balance reflects claimed fees

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A — onchain claim. Mitigation: quorum + ledger reconciliation before claim.

### `treasury_dao_execute`

Entry point: `src/sincor2/defi/treasury_dao.py:235` · HTTP: `n/a (library)` · Auth: `['operator']`

**Approval:** quorum — QUORUM: executes an approved treasury hold onchain; moves real funds.

**Pre-conditions:** hold_approved, hold_fresh, quorum_reached, executive_key_present, killswitch_clear, not_in_shadow_mode

**Policy checks:**<br>- sincor2.defi.treasury_dao.Executor (raises UnreviewedHold, StaleHold, BroadcastForbidden, BandViolation)<br>- sincor2.treasury_policy.TreasuryPolicy.should_convert_before_treasury

**Rate limits:**<br>`PROPOSED: treasury_live`<br>**per_operator**: 5/hour<br>**per_tenant**: 20/day

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; hold id, review signatures, quorum signatures, chain receipt

**Post-conditions:**<br>- chain receipt confirmed<br>- hold marked executed; no double-execution

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; UnreviewedHold/StaleHold/Band violations abort with no broadcast

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A — onchain movement. Mitigation: quorum + review + band + freshness checks before broadcast.

### `treasury_intent_allocate_live`

Entry point: `src/sincor2/agents/treasury_execution_agent.py:322` · HTTP: `n/a (scheduler/agent invocation)` · Auth: `['operator']`

**Approval:** quorum — QUORUM required: live treasury allocation moves real funds onchain. Founder-gated deploy ceremony; onchain executor key from Secure Vault only.

**Pre-conditions:** exec_live_armed, executive_key_present, quorum_reached, killswitch_clear, hold_approved, hold_fresh, ofac_screen_clean, not_in_shadow_mode

**Policy checks:**<br>- sincor2.defi.treasury_dao.Hold/Reviewer/Executor lifecycle (approved, unreviewed, stale, band checks)<br>- sincor2.treasury_policy.TreasuryPolicy.should_convert_before_treasury (5% fee -> 100% USDC/WETH conversion, NO burn)<br>- sincor2.defi.ofac_sdn.screen_wallet_address (counterparty/protocol)

**Rate limits:**<br>`PROPOSED: treasury_live`<br>**per_operator**: 5/hour<br>**per_tenant**: 20/day<br>**note**: quorum acts as the hard gate; tier is a backstop

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; intent hash, protocol calldata, amounts, quorum signatures, executor key id (never the key), chain receipt

**Post-conditions:**<br>- chain receipt confirmed for the broadcast transaction<br>- intent hash matches executed calldata (canonical_intent_hash)

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; intent stays DRY-RUN; nothing broadcast without quorum + armed executor

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A — onchain fund movement. Mitigation: quorum, dry-run first, allocation bands, kill switch that blocks NEW intents.

### `uw_revoke_agent`

Entry point: `src/sincor2/underwriting/blueprint.py:109` · HTTP: `POST /v1/agents/<agent_id>/revoke` · Auth: `['none']`

**Approval:** human — Human admin revokes underwriting standing (auth/permissions change). Guardrail REQUIRES the admin gate first.

**Pre-conditions:** admin_key_present, agent_registered, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: underwriting revocation policy — AUTH GAP: no route-level auth visible; must be admin-gated before exposure

**Rate limits:**<br>`PROPOSED: kya_admin`<br>**per_admin**: 60/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; revoked agent_id, envelope ids affected, admin identity, human_disposition

**Post-conditions:**<br>- standing revoked; open envelopes flagged for review<br>- agent excluded from underwritten mandates

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Re-underwrite via uw_underwrite with human approval; full history in audit log.

### `x402_verify_payment`

Entry point: `src/sincor2/x402_payments.py:180` · HTTP: `POST /api/x402/verify (route: mvp_blueprints/billing.py:217)` · Auth: `['none']`

**Approval:** human — Payment math verified automatically; minting the access token (granting value) requires human approval or standing owner policy; DecisionEvent carries human_disposition.

**Pre-conditions:** not_in_shadow_mode, rate_limit_ok, idempotency_key_present

**Policy checks:**<br>- x402 onchain payment verification to the treasury address (x402_payments.py)<br>- sincor2.defi.ofac_sdn.screen_wallet_address (payer screening)<br>- POLICY-MISSING: access-token mint policy (token scope, TTL, revocation) — implicit in x402_payments.py

**Rate limits:**<br>`PROPOSED: money_verify`<br>**per_ip**: 10/min<br>**per_tenant**: 100/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; tx_hash, treasury address, amount, minted token scope/TTL, human_disposition

**Post-conditions:**<br>- onchain x402 payment confirmed to treasury<br>- access token minted once, scoped to the paid resource

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; no token minted on any check failure

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A — token grant is final. Mitigation: short TTL, single-resource scope, human approval before mint.

---

## HIGH actions

Writes external state or affects other agents' funds/reputation. **Human approval required** — interactive for operator/admin actions, standing owner/policy approval (with envelopes) for autonomous protocol mechanics.

| Action | Domain | Approval | Rate limits | Reversible |
|---|---|---|---|---|
| `cancel_subscription` | payments | human | PROPOSED: subscription | reversible-with-cost |
| `close_auction` | marketplace | human | task_write | **irreversible** |
| `commit_bid` | marketplace | human | bid | reversible-with-cost |
| `confirm_settlement` | marketplace | human | settle | **irreversible** |
| `crm_sync_on_cutover` | communications | human | PROPOSED: crm | reversible-with-cost |
| `crypto_create_checkout` | payments | human | PROPOSED: checkout | reversible |
| `effect_crm_delete` | communications | human | PROPOSED: shadow_effect | **irreversible** |
| `effect_crm_write` | communications | human | PROPOSED: shadow_effect | reversible |
| `effect_message_send` | communications | human | PROPOSED: shadow_effect | **irreversible** |
| `kernel_python_exec` | mcp | human | PROPOSED: kernel_exec | reversible |
| `kya_bind` | identity | human | PROPOSED: kya_bind | reversible |
| `kya_unstake_finalize` | identity | human | PROPOSED: kya_unstake | reversible-with-cost |
| `kya_unstake_request` | identity | human | PROPOSED: kya_unstake | reversible |
| `kya_verify` | identity | human | PROPOSED: kya_admin | reversible |
| `mcp_submit_bid` | mcp | human | PROPOSED: mcp_write | reversible-with-cost |
| `place_bid` | marketplace | human | bid | reversible-with-cost |
| `platform_create_checkout` | payments | human | PROPOSED: checkout | reversible |
| `platform_verify_payment` | payments | human | PROPOSED: money_verify | **irreversible** |
| `pool_allocate` | marketplace | human | bid | reversible-with-cost |
| `pool_fund` | marketplace | human | bid | reversible |
| `profile_delete` | admin | human | PROPOSED: account | **irreversible** |
| `quest_claim` | identity | human | PROPOSED: quest | reversible-with-cost |
| `record_task_outcome` | marketplace | human | PROPOSED: reputation_write | reversible |
| `recovery_sponsor` | identity | human | admin | reversible-with-cost |
| `reveal_bid` | marketplace | human | bid | reversible |
| `sinc_credits_purchase` | payments | human | PROPOSED: credits | reversible-with-cost |
| `sinc_credits_spend` | payments | human | PROPOSED: credits | reversible-with-cost |
| `sponsored_stake` | identity | human | admin | reversible-with-cost |
| `stake_deposit` | marketplace | human | bid | reversible-with-cost |
| `stripe_cancel_subscription` | payments | human | PROPOSED: subscription | reversible-with-cost |
| `stripe_create_portal` | payments | human | PROPOSED: portal | reversible |
| `transfer_agent_record` | marketplace | human | PROPOSED: transfer | reversible |
| `treasury_dao_propose` | payments | human | PROPOSED: treasury_dryrun | reversible |
| `treasury_dao_review` | payments | human | PROPOSED: treasury_review | reversible |
| `treasury_intent_allocate` | payments | human | PROPOSED: treasury_dryrun | reversible |
| `uw_revoke_envelope` | identity | human | PROPOSED: underwrite | reversible |
| `uw_settle_mandate` | identity | human | PROPOSED: underwrite | **irreversible** |
| `uw_underwrite` | identity | human | PROPOSED: underwrite | reversible |

### `cancel_subscription`

Entry point: `src/sincor2/mvp_blueprints/billing.py:1032` · HTTP: `POST /api/cancel-subscription` · Auth: `['session']`

**Approval:** human — The session owner (human) approves canceling their own subscription with the provider.

**Pre-conditions:** session_owner_verified, rate_limit_ok

**Policy checks:**<br>- session ownership of the subscription (mvp_blueprints/billing.py)

**Rate limits:**<br>`PROPOSED: subscription`<br>**per_ip**: 10/min

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; subscription id, provider, session id, human_disposition

**Post-conditions:**<br>- provider confirms cancellation<br>- local entitlements updated

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible-with-cost

**Reversibility plan:** Re-subscribe; cost is the lapsed window.

### `close_auction`

Entry point: `src/sincor2/a2a_inbound_market.py:1351` · HTTP: `POST /v1/a2a/tasks/<task_id>/close` · Auth: `['none (permissionless timeout)']`

**Approval:** human — Human approval inherited from the task poster at create_task time: auction parameters (deadlines, slash terms) are poster-approved; close_auction executes exactly those terms. Any deviation from the approved terms is denied.

**Pre-conditions:** deadline_passed, rate_limit_ok

**Policy checks:**<br>- reveal-deadline check (permissionless timeout() only after deadline; a2a_inbound_market.py)<br>- sincor2.a2a_rate_limits.task_write (20/min + 300/hour composite)

**Rate limits:**<br>`task_write`<br>**windows**: 20/min + 300/hour per agent\|ip composite key

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; task_id, winner, ghosted commits slashed, released bids, human_disposition

**Post-conditions:**<br>- ghosted commits slashed 100%; losing bids released; winner locked<br>- auction state = closed (no re-close)

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; pre-deadline calls rejected; slash failures abort the close with no partial state

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A — slashing is final. Mitigation: poster-approved terms + deadline enforcement before any slash.

### `commit_bid`

Entry point: `src/sincor2/a2a_inbound_market.py:1302` · HTTP: `POST /v1/a2a/bids/commit` · Auth: `['membership (registered agent)', 'KYA (fresh heartbeat required)']`

**Approval:** human — Human-owner standing approval within the bidding envelope; commit locks 50% of bounty (minStakeBps=5000). Ghosting (no reveal) slashes 100% — the owner accepts this term in the envelope.

**Pre-conditions:** agent_registered, kya_verified, kya_heartbeat_fresh, stake_sufficient, commit_phase_open, agent_not_killed, standing_approval_envelope_ok, rate_limit_ok

**Policy checks:**<br>- sincor2.a2a_identity.verify_wallet_proof (EIP-191)<br>- heartbeat freshness gate (KYA live_statuses)<br>- sincor2.a2a_rate_limits.bid (30/min + 300/hour composite keying)

**Rate limits:**<br>`bid`<br>**windows**: 30/min + 300/hour per agent\|ip composite key

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; task_id, commitment hash, locked stake, heartbeat freshness, human_disposition

**Post-conditions:**<br>- commitment stored; 50% of bounty locked as stake<br>- reveal window armed for this commitment

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; stale heartbeat or insufficient stake -> 403 before lock

**Reversibility:** reversible-with-cost

**Reversibility plan:** Valid reveal converts to a bid; no reveal -> 100% slash (final). Cost accepted in the standing approval envelope.

### `confirm_settlement`

Entry point: `src/sincor2/blueprints/marketplace.py:393` · HTTP: `POST /api/marketplace/settlement/confirm` · Auth: `['none']`

**Approval:** human — Human approval REQUIRED: confirms a payment against a settlement quote by tx_hash with NO onchain verification in this path. The human attests the payment; the record is marked attested-not-settled.

**Pre-conditions:** admin_key_present, idempotency_key_present, rate_limit_ok

**Policy checks:**<br>- settlement quote match (tx_hash against quote; blueprints/marketplace.py)<br>- POLICY-MISSING: onchain verification policy — this path does NOT verify onchain; records are attested, not settled. Human attestation required.<br>- sincor2.a2a_rate_limits.settle (10/min + 100/hour) where the route is mapped

**Rate limits:**<br>`settle`<br>**windows**: 10/min + 100/hour<br>**note**: money-path tier; human attestation is the real gate

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; quote id, tx_hash, amount, attesting human, human_disposition=accepted, attested_not_settled flag

**Post-conditions:**<br>- settlement recorded once (idempotency key)<br>- record flagged as attested, not onchain-settled

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; unverified claims never confirmed

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A — confirmation is final. Mitigation: human attestation + idempotency before confirm.

### `crm_sync_on_cutover`

Entry point: `src/sincor2/webbuilder_crm.py:94` · HTTP: `n/a (library)` · Auth: `['operator']`

**Approval:** human — Human operator approves the CRM cutover sync; notifies the project owner by email with contact counts.

**Pre-conditions:** operator_key_present, rate_limit_ok

**Policy checks:**<br>- sincor2.compliance_guardrails.GuardrailsEngine.check_email_send (owner notification email)

**Rate limits:**<br>`PROPOSED: crm`<br>**per_operator**: 10/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; contact counts, owner notified, human_disposition

**Post-conditions:**<br>- owner notified with counts<br>- CRM records consistent post-cutover

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; cutover blocked until owner notification succeeds

**Reversibility:** reversible-with-cost

**Reversibility plan:** Cutover rolled back from pre-cutover snapshot; cost is reconciliation.

### `crypto_create_checkout`

Entry point: `src/sincor2/mvp_blueprints/billing.py:840` · HTTP: `POST /api/crypto/checkout` · Auth: `['none']`

**Approval:** human — The initiating customer is the human approver; creates an onchain checkout intent only.

**Pre-conditions:** rate_limit_ok, idempotency_key_present, not_in_shadow_mode

**Policy checks:**<br>- POLICY-MISSING: crypto checkout-intent policy (accepted assets, address derivation, amount caps)

**Rate limits:**<br>`PROPOSED: checkout`<br>**per_ip**: 20/min<br>**per_tenant**: 200/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; intent id, asset, amount, deposit address, idempotency key

**Post-conditions:**<br>- intent created with deposit address; idempotency-keyed

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Intent expires; unclaimed intents swept by expiry job.

### `effect_crm_delete`

Entry point: `src/sincor2/shadow_monitor/effect_boundary.py:92` · HTTP: `n/a (shadow intent)` · Auth: `['shadow policy']`

**Approval:** human — Shadow intent; deletions are not restorable by the boundary — live promotion requires human approval with extra scrutiny.

**Pre-conditions:** not_in_shadow_mode

**Policy checks:**<br>- sincor2.shadow_monitor.effect_boundary.ShadowEffectBoundary.evaluate (crm.delete -> deny in shadow; needs_approval for live)

**Rate limits:**<br>`PROPOSED: shadow_effect`<br>**per_agent**: 50/day

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; effect type crm.delete, intent hash, blocked_in_live_mode=true

**Post-conditions:**<br>- intent recorded with receipt; no live side effect

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A — deletions are not restorable by the boundary. Mitigation: human approval + pre-delete export.

### `effect_crm_write`

Entry point: `src/sincor2/shadow_monitor/effect_boundary.py:92` · HTTP: `n/a (shadow intent)` · Auth: `['shadow policy']`

**Approval:** human — Shadow intent; live promotion requires human approval.

**Pre-conditions:** not_in_shadow_mode

**Policy checks:**<br>- sincor2.shadow_monitor.effect_boundary.ShadowEffectBoundary.evaluate (crm.write -> deny in shadow; needs_approval for live)

**Rate limits:**<br>`PROPOSED: shadow_effect`<br>**per_agent**: 100/day

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; effect type crm.write, intent hash, blocked_in_live_mode=true

**Post-conditions:**<br>- intent recorded with receipt; no live side effect

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** At the effect level: CRM writes are correctable; live promotion still needs human approval.

### `effect_message_send`

Entry point: `src/sincor2/shadow_monitor/effect_boundary.py:92` · HTTP: `n/a (shadow intent)` · Auth: `['shadow policy']`

**Approval:** human — Shadow intent; live promotion requires human approval + content screening.

**Pre-conditions:** not_in_shadow_mode

**Policy checks:**<br>- sincor2.shadow_monitor.effect_boundary.ShadowEffectBoundary.evaluate (message.send -> deny in shadow; needs_approval for live)<br>- sincor2.compliance_guardrails.GuardrailsEngine.check_content (on live promotion)

**Rate limits:**<br>`PROPOSED: shadow_effect`<br>**per_agent**: 100/day

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; effect type message.send, intent hash, blocked_in_live_mode=true

**Post-conditions:**<br>- intent recorded with receipt; no live side effect

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A — sent messages cannot be recalled. Mitigation: human approval before live send.

### `kernel_python_exec`

Entry point: `src/sincor2/agency_kernel_tools.py:87` · HTTP: `n/a (in-kernel tool)` · Auth: `['operator (kernel)']`

**Approval:** human — Human operator approves kernel code execution; sandbox escape is a host-level risk.

**Pre-conditions:** operator_key_present, rate_limit_ok, not_in_shadow_mode

**Policy checks:**<br>- kernel sandbox policy (agency_kernel_tools.py): arbitrary code runs inside the kernel sandbox<br>- POLICY-MISSING: code-execution policy (allowed imports, network/filesystem allowlists, execution timeouts) — sandbox escape = host risk

**Rate limits:**<br>`PROPOSED: kernel_exec`<br>**per_operator**: 60/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; code hash, allowed-import set, timeout, result summary, operator identity

**Post-conditions:**<br>- execution completed within sandbox and timeout<br>- no host filesystem/network access outside allowlists

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; sandbox violation kills the execution and alerts

**Reversibility:** reversible

**Reversibility plan:** Sandbox is ephemeral; host effects require separate review. Roll back any persisted artifacts.

### `kya_bind`

Entry point: `src/sincor2/kya_blueprint.py:40` · HTTP: `POST /api/kya/bind` · Auth: `['EIP-191']`

**Approval:** human — The wallet owner's EIP-191 signature IS the human approval: binds agent_id to a principal. Identity anchor.

**Pre-conditions:** eip191_proof_valid, agent_registered, rate_limit_ok

**Policy checks:**<br>- sincor2.a2a_identity.verify_wallet_proof (EIP-191 proof of wallet control; kya_blueprint.py)

**Rate limits:**<br>`PROPOSED: kya_bind`<br>**per_agent**: 10/hour<br>**per_ip**: 50/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; agent_id, principal, signature digest, human_disposition=accepted (owner-signed)

**Post-conditions:**<br>- binding recorded; lookups resolve agent_id -> principal

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; bad signature -> no binding

**Reversibility:** reversible

**Reversibility plan:** Re-bind with a new owner signature; binding history retained.

### `kya_unstake_finalize`

Entry point: `src/sincor2/kya_blueprint.py:128` · HTTP: `POST /api/kya/unstake/finalize` · Auth: `['none']`

**Approval:** human — The identity owner's EIP-191 signature IS the human approval; finalize releases identity stake.

**Pre-conditions:** owner_signature_valid, eip191_proof_valid, cooldown_elapsed, rate_limit_ok

**Policy checks:**<br>- sincor2.a2a_identity.verify_wallet_proof (owner proof)<br>- cool-down elapsed check (kya_blueprint.py)<br>- POLICY-MISSING: encumbrance policy (locked auction stake must block finalize) — verify inline coverage

**Rate limits:**<br>`PROPOSED: kya_unstake`<br>**per_agent**: 5/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; agent_id, released amount, cool-down evidence, signature digest

**Post-conditions:**<br>- stake released to the owner wallet<br>- identity stake balance zeroed

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; early finalize or encumbered stake -> deny

**Reversibility:** reversible-with-cost

**Reversibility plan:** Re-stake to restore standing; cost is the cool-down window and lost priority.

### `kya_unstake_request`

Entry point: `src/sincor2/kya_blueprint.py:116` · HTTP: `POST /api/kya/unstake/request` · Auth: `['none']`

**Approval:** human — The identity owner's EIP-191 signature IS the human approval to start the unstake flow.

**Pre-conditions:** owner_signature_valid, eip191_proof_valid, agent_registered, rate_limit_ok

**Policy checks:**<br>- sincor2.a2a_identity.verify_wallet_proof (owner proof)<br>- POLICY-MISSING: unstake-request policy (cool-down terms, encumbrance check against locked bids) — partially inline

**Rate limits:**<br>`PROPOSED: kya_unstake`<br>**per_agent**: 5/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; agent_id, stake amount, signature digest, human_disposition

**Post-conditions:**<br>- unstake request recorded; cool-down started<br>- stake not yet released

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Request canceled before finalize; no funds moved.

### `kya_verify`

Entry point: `src/sincor2/kya_blueprint.py:57` · HTTP: `POST /api/kya/verify` · Auth: `['none']`

**Approval:** human — Human admin verifies a KYA identity; trust decision with money-path consequences. Guardrail REQUIRES the admin gate first.

**Pre-conditions:** admin_key_present, agent_registered, rate_limit_ok

**Policy checks:**<br>- stake-tx evidence check where supplied (kya_blueprint.py)<br>- POLICY-MISSING: verification policy — AUTH GAP: no route-level auth visible; marks an identity verified. Must be admin-gated before exposure

**Rate limits:**<br>`PROPOSED: kya_admin`<br>**per_admin**: 60/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; agent_id, evidence, admin identity, human_disposition

**Post-conditions:**<br>- identity marked verified with evidence reference

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; unverifiable claims stay unverified

**Reversibility:** reversible

**Reversibility plan:** Revoke via kya_revoke; verification history retained.

### `mcp_submit_bid`

Entry point: `src/sincor2/mcp_server.py:340` · HTTP: `MCP tools/call submit_bid` · Auth: `['operator_confirm']`

**Approval:** human — Two-phase operator confirmation IS the human approval: default-deny, operator confirms the bid before submission.

**Pre-conditions:** operator_key_present, standing_approval_envelope_ok, rate_limit_ok, not_in_shadow_mode

**Policy checks:**<br>- MCP two-phase operator confirmation (mcp_server.py, default-deny)<br>- mode policy: auto / legacy / commit / reveal

**Rate limits:**<br>`PROPOSED: mcp_write`<br>**per_operator**: 60/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; task_id, mode, bid params, operator confirmation record, human_disposition=accepted

**Post-conditions:**<br>- bid submitted in the confirmed mode<br>- confirmation record linked to the bid

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; unconfirmed bids never submit (default-deny)

**Reversibility:** reversible-with-cost

**Reversibility plan:** Follows the bid lifecycle (release/lock); commit mode inherits ghosting-slash terms.

### `place_bid`

Entry point: `src/sincor2/a2a_inbound_market.py:1282` · HTTP: `POST /v1/a2a/bids (alias /api/v1/bids)` · Auth: `['membership (registered agent)']`

**Approval:** human — Human-owner standing approval: the owner pre-authorizes a bidding envelope (max bid, max stake at risk); bids inside the envelope execute autonomously with DecisionEvent audit and human_disposition="not_reviewed"; outside the envelope needs fresh interactive approval.

**Pre-conditions:** agent_registered, kya_verified, stake_sufficient, agent_not_killed, standing_approval_envelope_ok, rate_limit_ok

**Policy checks:**<br>- sincor2.a2a_identity.verify_wallet_proof (membership binding)<br>- sincor2.a2a_rate_limits.bid (30/min + 300/hour, composite agent\|ip keying)<br>- POLICY-MISSING: bid-value policy (max bid vs stake ratio, bounty sanity bounds) — currently inline in a2a_inbound_market.py

**Rate limits:**<br>`bid`<br>**windows**: 30/min + 300/hour per agent\|ip composite key<br>**note**: a2a_rate_limits.A2A_RATE_POLICIES['bid'] via before_request on the a2a blueprint

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; task_id, bid amount, stake locked, envelope bounds, human_disposition

**Post-conditions:**<br>- bid recorded; losing-bid release path armed on close<br>- stake lock reflected in the stake ledger

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; bid rejected before any stake lock; no partial ledger row

**Reversibility:** reversible-with-cost

**Reversibility plan:** Losing bids released on close_auction; winner's bid locked for the auction lifecycle; withdrawal rules apply — cost is the locked stake window.

### `platform_create_checkout`

Entry point: `src/sincor2/mvp_blueprints/billing.py:53` · HTTP: `POST /api/platform/checkout` · Auth: `['none']`

**Approval:** human — The initiating customer is the human approver; creates an offchain intent only — no charge.

**Pre-conditions:** rate_limit_ok, idempotency_key_present, not_in_shadow_mode

**Policy checks:**<br>- POLICY-MISSING: SINC/AXM checkout-quote policy (price source, quote TTL, amount caps) — idempotency-keyed offchain intent

**Rate limits:**<br>`PROPOSED: checkout`<br>**per_ip**: 20/min<br>**per_tenant**: 200/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; quote id, amount, currency, idempotency key

**Post-conditions:**<br>- quote created with TTL; idempotency-keyed<br>- no funds moved

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Quote expires unclaimed; cancel by idempotency key.

### `platform_verify_payment`

Entry point: `src/sincor2/mvp_blueprints/billing.py:76` · HTTP: `POST /api/platform/verify` · Auth: `['none']`

**Approval:** human — Human approval required: verifies an onchain payment claim and provisions the purchase; provisioning is effectively final.

**Pre-conditions:** not_in_shadow_mode, rate_limit_ok, idempotency_key_present

**Policy checks:**<br>- onchain payment claim verification (mvp_blueprints/billing.py)<br>- sincor2.defi.ofac_sdn.screen_wallet_address (payer)<br>- POLICY-MISSING: confirmation-depth policy — ad hoc in handler

**Rate limits:**<br>`PROPOSED: money_verify`<br>**per_ip**: 10/min<br>**per_tenant**: 100/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; tx_hash, confirmations, provisioned entitlements, human_disposition

**Post-conditions:**<br>- payment verified at required depth<br>- purchase provisioned exactly once

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; failed verification never provisions

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A — provisioning final. Mitigation: human approval + depth verification pre-grant.

### `pool_allocate`

Entry point: `src/sincor2/a2a_inbound_market.py:1790` · HTTP: `POST /v1/a2a/pool/allocate` · Auth: `['admin']`

**Approval:** human — Admin reserves pool funds for a task; the admin's action is the human approval.

**Pre-conditions:** admin_key_present, pool_balance_sufficient, rate_limit_ok

**Policy checks:**<br>- available-balance check (amount leaves available balance; a2a_inbound_market.py)<br>- sincor2.a2a_rate_limits.bid

**Rate limits:**<br>`bid`<br>**windows**: 30/min + 300/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; task_id, amount, pool balance after, admin identity

**Post-conditions:**<br>- allocation reserved; available balance reduced<br>- allocation linked to the task

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; insufficient balance -> deny, no partial allocation

**Reversibility:** reversible-with-cost

**Reversibility plan:** Release via pool_release returns funds to available balance; cost is the reservation window.

### `pool_fund`

Entry point: `src/sincor2/a2a_inbound_market.py:1769` · HTTP: `POST /v1/a2a/pool/fund` · Auth: `['admin']`

**Approval:** human — Admin moves configured reserve into the spendable launch-bounty pool (ledger-only, no onchain movement). The admin's action is the human approval.

**Pre-conditions:** admin_key_present, rate_limit_ok

**Policy checks:**<br>- reserve configuration check (configured reserve amount; a2a_inbound_market.py)<br>- sincor2.a2a_rate_limits.bid (admin writes in the cheap-write abuse class)

**Rate limits:**<br>`bid`<br>**windows**: 30/min + 300/hour<br>**note**: ENDPOINT_POLICY maps POST /v1/a2a/pool/fund -> bid

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; amount, source reserve, pool balance after, admin identity

**Post-conditions:**<br>- pool balance increased by the funded amount<br>- reserve ledger debited equally (no creation of funds)

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Unspent funds return via pool_release; pool ledger is fully reconcilable.

### `profile_delete`

Entry point: `src/sincor2/mvp_blueprints/auth.py:315` · HTTP: `DELETE /api/profile/delete` · Auth: `['session']`

**Approval:** human — The profile owner (human, via session) approves deleting their own data. Irreversible: confirm explicitly.

**Pre-conditions:** session_owner_verified, rate_limit_ok

**Policy checks:**<br>- session ownership (mvp_blueprints/auth.py)<br>- POLICY-MISSING: deletion policy (grace period, data-retention exceptions for audit/finance records) — currently inline

**Rate limits:**<br>`PROPOSED: account`<br>**per_ip**: 10/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; user id (redacted), scope deleted, retention exceptions, human_disposition=accepted

**Post-conditions:**<br>- profile/data deleted except legal-retention records<br>- session invalidated

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A — deletion final. Mitigation: explicit human confirmation + grace period before purge.

### `quest_claim`

Entry point: `src/sincor2/kya/blueprint.py:150` · HTTP: `POST /v1/quest/claim` · Auth: `['none']`

**Approval:** human — The claiming identity owner (EIP-191) is the human approver; value-adjacent claim with double-claim guard.

**Pre-conditions:** kya_verified, owner_signature_valid, double_claim_guard_pass, rate_limit_ok

**Policy checks:**<br>- double-claim protection (kya/blueprint.py)<br>- POLICY-MISSING: quest-eligibility policy (campaign rules, per-identity caps, sybil checks) — partially inline

**Rate limits:**<br>`PROPOSED: quest`<br>**per_agent**: 10/hour<br>**per_ip**: 50/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; quest id, agent_id, reward, claim nonce, human_disposition

**Post-conditions:**<br>- reward credited once (claim nonce consumed)<br>- repeat claim with same nonce denied

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; duplicate or ineligible claim denied

**Reversibility:** reversible-with-cost

**Reversibility plan:** Fraudulent claim clawed back via dispute/slash path; cost is investigation.

### `record_task_outcome`

Entry point: `src/sincor2/blueprints/marketplace.py:217` · HTTP: `POST /api/marketplace/tasks/<task_reference>/outcome` · Auth: `['none']`

**Approval:** human — Human admin/operator approval required: writes reputation affecting other agents' routing. Guardrail REQUIRES the auth gate first.

**Pre-conditions:** admin_key_present, agent_registered, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: reputation-write policy — AUTH GAP: no route-level auth visible; writes trust scores affecting other agents' routing priority. Must be admin/operator-gated before exposure

**Rate limits:**<br>`PROPOSED: reputation_write`<br>**per_admin**: 100/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; task_reference, agent_id, trust score delta, evidence, admin identity, human_disposition

**Post-conditions:**<br>- trust score recorded with evidence reference<br>- routing priority recompute reflects the write

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; unauthenticated outcome posts denied

**Reversibility:** reversible

**Reversibility plan:** Correcting outcome overwrites with new evidence; history retained in audit log.

### `recovery_sponsor`

Entry point: `src/sincor2/recovery.py:462` · HTTP: `POST /v1/a2a/admin/recovery/sponsor` · Auth: `['admin']`

**Approval:** human — Human admin fronts recovery stake for an honestly-bankrupt agent; eligibility is computed, approval is human.

**Pre-conditions:** admin_key_present, agent_registered, rate_limit_ok

**Policy checks:**<br>- bankruptcy eligibility as a pure function of evidence + wallet history (tiered C; recovery.py)<br>- sincor2.a2a_rate_limits.admin

**Rate limits:**<br>`admin`<br>**windows**: 30/min + 200/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; agent_id, evidence refs, tier, amount, admin identity, human_disposition

**Post-conditions:**<br>- recovery stake credited; agent re-enabled per tiered-C ladder

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; ineligible agents denied even with admin key (eligibility is a pure function)

**Reversibility:** reversible-with-cost

**Reversibility plan:** Recovery terms enforced; re-offense follows the fixed escalation ladder.

### `reveal_bid`

Entry point: `src/sincor2/a2a_inbound_market.py:1326` · HTTP: `POST /v1/a2a/bids/reveal` · Auth: `['membership (registered agent)']`

**Approval:** human — Human approval inherited from the commit_bid standing approval: reveal is the idempotent completion of an already-approved commitment.

**Pre-conditions:** agent_registered, reveal_phase_open, rate_limit_ok

**Policy checks:**<br>- commitment recomputed in constant time (a2a_inbound_market.py)<br>- sincor2.a2a_rate_limits.bid

**Rate limits:**<br>`bid`<br>**windows**: 30/min + 300/hour per agent\|ip composite key

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; task_id, commitment hash, reveal validity, human_disposition

**Post-conditions:**<br>- only valid reveals become bids (idempotent)<br>- invalid reveals rejected without state change

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; mismatched commitment rejected; no bid created

**Reversibility:** reversible

**Reversibility plan:** Revealed bid follows the normal bid lifecycle (release on loss, lock on win).

### `sinc_credits_purchase`

Entry point: `src/sincor2/blueprints/sinc.py:178` · HTTP: `POST /api/sinc/credits/purchase` · Auth: `['none']`

**Approval:** human — The purchasing human approves; credits a SINC purchase to their wallet.

**Pre-conditions:** rate_limit_ok, idempotency_key_present, not_in_shadow_mode

**Policy checks:**<br>- POLICY-MISSING: SINC credit-purchase policy (price source, per-wallet caps, wallet binding) — currently inline in blueprints/sinc.py

**Rate limits:**<br>`PROPOSED: credits`<br>**per_ip**: 20/min<br>**per_tenant**: 200/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; wallet, amount, price, idempotency key, human_disposition

**Post-conditions:**<br>- credits credited exactly once (idempotency key)<br>- wallet balance reflects purchase

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible-with-cost

**Reversibility plan:** Refund path reverses the credit; cost is settlement fees if any.

### `sinc_credits_spend`

Entry point: `src/sincor2/blueprints/sinc.py:239` · HTTP: `POST /api/sinc/credits/spend` · Auth: `['api_key']`

**Approval:** human — The api_key holder (tenant operator, human) approves each spend by signing the request; key possession is the approval instrument.

**Pre-conditions:** api_key_valid, pool_balance_sufficient, rate_limit_ok

**Policy checks:**<br>- api_key ownership + balance check (blueprints/sinc.py)<br>- POLICY-MISSING: spend policy (per-key daily caps, purpose codes) — currently inline

**Rate limits:**<br>`PROPOSED: credits`<br>**per_agent**: 100/min<br>**per_tenant**: 1000/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; key id, amount, purpose, balance after

**Post-conditions:**<br>- credits debited; balance never negative<br>- spend recorded against the key

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; insufficient balance -> deny; no partial debit

**Reversibility:** reversible-with-cost

**Reversibility plan:** Operator-initiated credit-back with audit; cost is reconciliation.

### `sponsored_stake`

Entry point: `src/sincor2/sponsored_stake.py:338` · HTTP: `POST /v1/a2a/admin/sponsored-stake` · Auth: `['admin']`

**Approval:** human — Human admin fronts an agent's first stake; the admin's action is the human approval.

**Pre-conditions:** admin_key_present, agent_registered, rate_limit_ok

**Policy checks:**<br>- genesis-cohort eligibility (opt-in mechanism; sponsored_stake.py)<br>- sincor2.a2a_rate_limits.admin (credential-gated backstop)

**Rate limits:**<br>`admin`<br>**windows**: 30/min + 200/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; agent_id, sponsored amount, cohort, admin identity, human_disposition

**Post-conditions:**<br>- stake credited to the agent's ledger<br>- sponsorship terms recorded (repayment/clawback)

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible-with-cost

**Reversibility plan:** Sponsorship recalled per terms; cost is the fronted-stake window.

### `stake_deposit`

Entry point: `src/sincor2/a2a_inbound_market.py:1564` · HTTP: `POST /v1/a2a/stake/deposit` · Auth: `['membership (registered agent)']`

**Approval:** human — Human-owner standing approval: self-service stake deposit to the offchain AXM ledger (Pool 1). Ledger-only accounting — no chain funds move.

**Pre-conditions:** agent_registered, eip191_proof_valid, idempotency_key_present, standing_approval_envelope_ok, rate_limit_ok

**Policy checks:**<br>- sincor2.a2a_identity.verify_wallet_proof (fail-closed EIP-191 identity binding on deposits)<br>- idempotency on tx_hash (a2a_inbound_market.py)<br>- sincor2.a2a_rate_limits.bid (same abuse class as bids)

**Rate limits:**<br>`bid`<br>**windows**: 30/min + 300/hour per agent\|ip composite key

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; agent_id, amount, tx_hash (idempotency), ledger balance after, human_disposition

**Post-conditions:**<br>- ledger credited exactly once per tx_hash<br>- stake balance queryable via GET /v1/a2a/stake/<agent_id>

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; identity-proof failure blocks before any ledger write

**Reversibility:** reversible-with-cost

**Reversibility plan:** Unstake via kya_unstake_request/finalize (cooldown applies); ledger adjustments audited.

### `stripe_cancel_subscription`

Entry point: `src/sincor2/stripe_routes.py:211` · HTTP: `POST /api/stripe/cancel/<subscription_id>` · Auth: `['none']`

**Approval:** human — The subscription owner (human) approves the cancel; ownership must be proven first (AUTH GAP remediation).

**Pre-conditions:** session_owner_verified, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: subscription-ownership policy — AUTH GAP: no route-level auth visible; callers must own the subscription. Guardrail REQUIRES ownership proof before exposure

**Rate limits:**<br>`PROPOSED: subscription`<br>**per_ip**: 10/min

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; subscription id, owner proof, human_disposition

**Post-conditions:**<br>- subscription canceled at provider<br>- local entitlement downgraded

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; unproven ownership -> deny

**Reversibility:** reversible-with-cost

**Reversibility plan:** Re-subscribe via checkout; cost is the lapsed-service window.

### `stripe_create_portal`

Entry point: `src/sincor2/stripe_routes.py:178` · HTTP: `POST /api/stripe/portal` · Auth: `['session']`

**Approval:** human — The session owner (human customer) is the approver: portal opens only for their own billing record.

**Pre-conditions:** session_owner_verified, rate_limit_ok

**Policy checks:**<br>- session ownership: portal is for the caller's own customer record (stripe_routes.py)<br>- POLICY-MISSING: customer-record binding policy (prove the session owns the Stripe customer id) — partially inline

**Rate limits:**<br>`PROPOSED: portal`<br>**per_ip**: 20/min

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; stripe customer id, session id

**Post-conditions:**<br>- portal session URL returned for the caller's customer only

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; cross-customer portal requests denied

**Reversibility:** reversible

**Reversibility plan:** Portal sessions expire; no state change.

### `transfer_agent_record`

Entry point: `src/sincor2/a2a_inbound_ext.py:608` · HTTP: `POST /v1/a2a/transfer` · Auth: `['EIP-191']`

**Approval:** human — The current owner's EIP-191 signature IS the human approval: identity transfer to a new owner wallet. Audited as an identity change.

**Pre-conditions:** owner_signature_valid, eip191_proof_valid, agent_registered, rate_limit_ok

**Policy checks:**<br>- sincor2.a2a_identity.verify_wallet_proof (current-owner EIP-191 signature; a2a_inbound_ext.py)<br>- POLICY-MISSING: transfer policy (cool-down after transfer, stake encumbrance check) — currently inline

**Rate limits:**<br>`PROPOSED: transfer`<br>**per_agent**: 5/hour<br>**per_ip**: 20/hour<br>**note**: identity-change surface; strict PROPOSED tier

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; agent_id, old wallet, new wallet, signature digest, human_disposition=accepted (owner-signed)

**Post-conditions:**<br>- record bound to the new owner wallet<br>- old wallet can no longer act for the agent

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; signature mismatch -> transfer denied; record untouched

**Reversibility:** reversible

**Reversibility plan:** New owner may transfer back with their own EIP-191 signature; both transfers audited.

### `treasury_dao_propose`

Entry point: `src/sincor2/defi/treasury_dao.py:285` · HTTP: `n/a (library)` · Auth: `['operator']`

**Approval:** human — Human operator proposes a treasury hold (allocation plan) for review; proposal is not execution.

**Pre-conditions:** operator_key_present, rate_limit_ok

**Policy checks:**<br>- sincor2.defi.treasury_dao.Hold (plan shape, allocation bands)

**Rate limits:**<br>`PROPOSED: treasury_dryrun`<br>**per_operator**: 20/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; hold id, plan, bands, proposer, human_disposition

**Post-conditions:**<br>- hold recorded in proposed state<br>- reviewers notified

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Proposal withdrawn before review; no onchain effect.

### `treasury_dao_review`

Entry point: `src/sincor2/defi/treasury_dao.py:177` · HTTP: `n/a (library)` · Auth: `['operator']`

**Approval:** human — The reviewer IS the human approval gate: approve/reject with reason is the decision before any broadcast.

**Pre-conditions:** operator_key_present, rate_limit_ok

**Policy checks:**<br>- sincor2.defi.treasury_dao.Reviewer (approve/reject with reason)

**Rate limits:**<br>`PROPOSED: treasury_review`<br>**per_operator**: 20/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; hold id, decision, reason, reviewer, human_disposition

**Post-conditions:**<br>- hold marked approved or rejected with reason<br>- only approved holds are executable

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; unreviewed holds are not executable (UnreviewedHold)

**Reversibility:** reversible

**Reversibility plan:** Review decision superseded by a later review before execution; audit retains both.

### `treasury_intent_allocate`

Entry point: `src/sincor2/agents/treasury_execution_agent.py:282` · HTTP: `n/a (scheduler/agent invocation)` · Auth: `['operator']`

**Approval:** human — Human operator queues a DRY-RUN allocation intent; no chain broadcast unless EXECUTE_LIVE is armed (which escalates to the quorum path of treasury_intent_allocate_live).

**Pre-conditions:** operator_key_present, quorum_reached, rate_limit_ok

**Policy checks:**<br>- sincor2.defi.treasury_dao.Hold (allocation plan review state)<br>- POLICY-MISSING: dry-run vs live promotion policy — EXECUTE_LIVE arming procedure (founder-gated ceremony) is documented but not a code gate in this tree

**Rate limits:**<br>`PROPOSED: treasury_dryrun`<br>**per_operator**: 20/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; intent hash, protocol, amounts, dry-run flag, human_disposition

**Post-conditions:**<br>- intent queued as DRY-RUN; no broadcast<br>- intent hash recorded for later promotion

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; unreviewed intents never promoted to live

**Reversibility:** reversible

**Reversibility plan:** Dry-run intents discarded; nothing onchain.

### `uw_revoke_envelope`

Entry point: `src/sincor2/underwriting/blueprint.py:87` · HTTP: `POST /v1/mandates/<envelope_id>/revoke` · Auth: `['none']`

**Approval:** human — Human admin revokes a mandate envelope; guardrail REQUIRES the admin gate first.

**Pre-conditions:** admin_key_present, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: envelope-revocation policy — AUTH GAP: no route-level auth visible. Must be admin-gated before exposure

**Rate limits:**<br>`PROPOSED: underwrite`<br>**per_admin**: 60/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; envelope id, reason, admin identity, human_disposition

**Post-conditions:**<br>- envelope marked revoked; unsettled legs blocked

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Envelope re-issued via uw_underwrite with fresh human approval.

### `uw_settle_mandate`

Entry point: `src/sincor2/underwriting/blueprint.py:73` · HTTP: `POST /v1/mandates/<envelope_id>/settle` · Auth: `['none']`

**Approval:** human — Human approval required: settles an underwritten mandate (irreversible). Guardrail REQUIRES the admin gate first.

**Pre-conditions:** admin_key_present, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: mandate-settlement policy (settlement conditions, evidence) — AUTH GAP: no route-level auth visible. Must be admin-gated before exposure

**Rate limits:**<br>`PROPOSED: underwrite`<br>**per_admin**: 60/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; envelope id, settlement evidence, admin identity, human_disposition

**Post-conditions:**<br>- mandate marked settled with evidence<br>- no double-settlement (envelope state machine)

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A — settlement final. Mitigation: human approval + evidence before settle.

### `uw_underwrite`

Entry point: `src/sincor2/underwriting/blueprint.py:65` · HTTP: `POST /v1/mandates/underwrite` · Auth: `['none']`

**Approval:** human — Human underwriter approves/denies the mandate; the decision gates money movement. Guardrail REQUIRES the admin gate first.

**Pre-conditions:** admin_key_present, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: underwriting policy (allow/deny criteria, evidence requirements) — AUTH GAP: no route-level auth visible; decision gates money movement. Must be admin-gated before exposure

**Rate limits:**<br>`PROPOSED: underwrite`<br>**per_admin**: 60/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; envelope id, decision, evidence, underwriter identity, human_disposition

**Post-conditions:**<br>- mandate recorded as underwritten (allow) or denied with reason

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; unauthenticated underwrite denied; ambiguous mandates stay denied

**Reversibility:** reversible

**Reversibility plan:** Decision superseded before settlement; uw_revoke_envelope after settlement review.

---

## MEDIUM actions

Writes local state with cross-agent visibility. Auto with policy check + audit log.

| Action | Domain | Approval | Rate limits | Reversible |
|---|---|---|---|---|
| `airdrop_register` | identity | auto | register | reversible |
| `auth_login` | admin | auto | PROPOSED: login | reversible |
| `clear_polyclaw_dry_runs` | admin | auto | admin | reversible |
| `content_generate` | admin | auto | PROPOSED: content | reversible |
| `create_task` | marketplace | auto | task_write | reversible |
| `crm_record_contact` | communications | auto | PROPOSED: crm | reversible |
| `defi_swarm_submit` | defi | auto | PROPOSED: defi_tick | reversible |
| `defi_swarm_tick` | defi | auto | PROPOSED: defi_tick | reversible |
| `delete_task` | marketplace | auto | task_write | reversible-with-cost |
| `grade_task` | admin | auto | PROPOSED: admin_write | reversible |
| `issue_creator_token` | marketplace | auto | issuance | reversible-with-cost |
| `kernel_claude_reason` | mcp | auto | PROPOSED: kernel_llm | reversible |
| `kya_list` | identity | auto | PROPOSED: kya_admin | reversible |
| `mcp_register_agent` | mcp | auto | PROPOSED: mcp_write | reversible |
| `memory_ingest` | admin | auto | PROPOSED: memory | reversible |
| `memory_run` | admin | auto | PROPOSED: memory | reversible |
| `onboarding_submit` | admin | auto | PROPOSED: account | reversible |
| `partner_status_update` | communications | auto | PROPOSED: admin_write | reversible |
| `pool_release` | marketplace | auto | bid | reversible |
| `quest_seed` | identity | auto | PROPOSED: quest | reversible |
| `register_agent_a2a` | marketplace | auto | register | reversible |
| `register_agent_marketplace` | marketplace | auto | register | reversible |
| `registry_probe` | admin | auto | PROPOSED: probe | reversible |
| `sadas_publish` | identity | auto | PROPOSED: kya_admin | reversible |
| `send_welcome_email` | communications | auto | PROPOSED: email | **irreversible** |
| `stake_sinc_reputation` | marketplace | auto | PROPOSED: reputation_stake | reversible |
| `submit_proof` | marketplace | auto | task_write | reversible |
| `submit_task_sync` | marketplace | auto | task_msg | reversible |
| `unstake_sinc_reputation` | marketplace | auto | PROPOSED: reputation_stake | reversible |
| `user_signup` | admin | auto | register | reversible |
| `uw_register_agent` | identity | auto | register | reversible |
| `x402_execute_paid_resource` | payments | auto | PROPOSED: paid_api | reversible |

### `airdrop_register`

Entry point: `src/sincor2/mvp_blueprints/billing.py:820` · HTTP: `POST /api/airdrop/register` · Auth: `['none']`

**Approval:** auto — Auto: registers a wallet for the SIN airdrop; Sybil defenses are the open policy item.

**Pre-conditions:** rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: airdrop-registration policy (wallet binding, Sybil defenses: proof-of-personhood / stake / allowlist) — Sybil risk is the main concern<br>- sincor2.a2a_rate_limits.register (Sybil-surface tier)

**Rate limits:**<br>`register`<br>**windows**: 5/hour + 20/day per IP

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; wallet (redacted), registration ref

**Post-conditions:**<br>- wallet registered once (deduped)

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; duplicate or Sybil-flagged registrations denied/quarantined

**Reversibility:** reversible

**Reversibility plan:** Registration removed before distribution; post-distribution handled by clawback policy.

### `auth_login`

Entry point: `src/sincor2/mvp_blueprints/auth.py:26` · HTTP: `POST /api/auth/login` · Auth: `['none (credentials)']`

**Approval:** auto — Auto: issues a session/JWT on valid credentials.

**Pre-conditions:** rate_limit_ok

**Policy checks:**<br>- credential verification (mvp_blueprints/auth.py)<br>- POLICY-MISSING: login-attempt policy (lockout thresholds, breach-password screening) — partially inline

**Rate limits:**<br>`PROPOSED: login`<br>**per_ip**: 20/min<br>**note**: brute-force backstop; lockout policy is the open item

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; user ref, success/failure, ip (rate-limit key only)

**Post-conditions:**<br>- session/JWT issued on valid credentials only

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; bad credentials -> deny; lockout on repeated failure

**Reversibility:** reversible

**Reversibility plan:** Session revoked; password reset flow.

### `clear_polyclaw_dry_runs`

Entry point: `src/sincor2/blueprints/monitoring.py:174` · HTTP: `POST /api/polyclaw/clear-dry-runs` · Auth: `['admin']`

**Approval:** auto — Auto once admin-gated. Guardrail REQUIRES the admin gate: an unauthenticated caller must never re-arm trading.

**Pre-conditions:** admin_key_present, rate_limit_ok

**Policy checks:**<br>- sincor2.blueprints.monitoring._admin_gate (sincor2.auth_system.admin_required: JWT with role=admin; 403 otherwise; 503 fail-closed if the decorator is unavailable) — C1 remediated 2026-10-08

**Rate limits:**<br>`admin`<br>**windows**: 30/min + 200/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; cleared trades, released exposure, admin identity

**Post-conditions:**<br>- dry-run trades closed; exposure released

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; unauthenticated calls denied; kill switch stays tripped

**Reversibility:** reversible

**Reversibility plan:** Dry-run state is simulated; re-trip the kill switch if exposure recurs.

### `content_generate`

Entry point: `src/sincor2/mvp_blueprints/admin.py:69` · HTTP: `POST /admin/content/generate` · Auth: `['admin']`

**Approval:** auto — Auto for the admin: generates launch content drafts (NOT published — publishing is the separate human-gated launch_review_approve_post).

**Pre-conditions:** admin_key_present, rate_limit_ok

**Policy checks:**<br>- sincor2.compliance_guardrails.GuardrailsEngine.check_content (drafts, not published)

**Rate limits:**<br>`PROPOSED: content`<br>**per_admin**: 50/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; draft id, content hash, admin identity

**Post-conditions:**<br>- draft stored; not published

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Draft discarded; never reached the public.

### `create_task`

Entry point: `src/sincor2/a2a_inbound_market.py:1124` · HTTP: `POST /v1/a2a/tasks` · Auth: `['none (anonymous ok)', 'EIP-191 (optional poster attribution)']`

**Approval:** auto — Auto: task creation is the poster's own action; anonymous or EIP-191-attributed.

**Pre-conditions:** pool_allocation_valid, rate_limit_ok

**Policy checks:**<br>- sincor2.a2a_rate_limits.task_write (20/min + 300/hour composite agent\|ip keying)<br>- POLICY-MISSING: task-content policy (bounty bounds, description screening, poster attribution rules) — anonymous posts allowed by design

**Rate limits:**<br>`task_write`<br>**windows**: 20/min + 300/hour per agent\|ip composite key<br>**note**: ENDPOINT_POLICY maps POST /v1/a2a/tasks -> task_write

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; task_id, bounty, poster (or anonymous), auction params

**Post-conditions:**<br>- task listed; auction params (deadlines, slash terms) stored as the poster-approved terms for close_auction

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; invalid bounty/params rejected before listing

**Reversibility:** reversible

**Reversibility plan:** delete_task (admin) before bids arrive; after bids, the auction lifecycle governs.

### `crm_record_contact`

Entry point: `src/sincor2/webbuilder_crm.py:56` · HTTP: `n/a (library)` · Auth: `['operator']`

**Approval:** auto — Auto: records a CRM contact locally; PII stays in the local store (references in logs, never content).

**Pre-conditions:** operator_key_present, rate_limit_ok

**Policy checks:**<br>- sincor2.compliance_guardrails.GuardrailsEngine.check_pii_storage (local SQLite destination)

**Rate limits:**<br>`PROPOSED: crm`<br>**per_operator**: 200/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; contact ref (redacted), source

**Post-conditions:**<br>- contact persisted locally

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Contact record deleted/updated on request.

### `defi_swarm_submit`

Entry point: `src/sincor2/defi/engine.py:112` · HTTP: `n/a (scheduler/agent invocation)` · Auth: `['operator']`

**Approval:** auto — Auto: submits the swarm's ranked plan set (plans, not executions).

**Pre-conditions:** operator_key_present, rate_limit_ok

**Policy checks:**<br>- sincor2.defi.gates.next_stage<br>- POLICY-MISSING: plan-ranking policy (ranking weights, submission eligibility) — partially inline in defi/engine.py

**Rate limits:**<br>`PROPOSED: defi_tick`<br>**per_operator**: 60/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; plan set hash, ranking, operator identity

**Post-conditions:**<br>- plan set submitted for review/execution gating

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Plan set superseded by the next submission.

### `defi_swarm_tick`

Entry point: `src/sincor2/defi/engine.py:109` · HTTP: `n/a (scheduler/agent invocation)` · Auth: `['operator']`

**Approval:** auto — Auto: runs one strategy tick per protocol spec — scan/plan only, never execution.

**Pre-conditions:** operator_key_present, rate_limit_ok

**Policy checks:**<br>- sincor2.defi.gates.next_stage (strategy lifecycle gates)<br>- scan/plan actions only — no execution (defi/engine.py)

**Rate limits:**<br>`PROPOSED: defi_tick`<br>**per_operator**: 120/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; protocol, tick id, plan output hash

**Post-conditions:**<br>- tick completed; plans recorded, nothing executed

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; tick failure leaves prior plans intact

**Reversibility:** reversible

**Reversibility plan:** Plans are ephemeral; superseded next tick.

### `delete_task`

Entry point: `src/sincor2/a2a_inbound_market.py:1147` · HTTP: `DELETE /v1/a2a/tasks/<task_id>` · Auth: `['admin']`

**Approval:** auto — Auto for the admin: refused when bids exist (must use the auction lifecycle).

**Pre-conditions:** admin_key_present, task_has_no_bids, allocation_released, rate_limit_ok

**Policy checks:**<br>- no-bids check (refused when the task has bids/commits; a2a_inbound_market.py)<br>- sincor2.a2a_rate_limits.task_write

**Rate limits:**<br>`task_write`<br>**windows**: 20/min + 300/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; task_id, released allocation, admin identity

**Post-conditions:**<br>- task removed; any pool allocation released first

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; tasks with bids/commits are refused deletion

**Reversibility:** reversible-with-cost

**Reversibility plan:** Task re-created (new id); cost is re-listing and notifying watchers.

### `grade_task`

Entry point: `src/sincor2/blueprints/command_center.py:497` · HTTP: `POST /api/command-center/grade-task` · Auth: `['none visible at route level']`

**Approval:** auto — Auto once admin-gated: submits a quality grade for a completed task. Guardrail REQUIRES the admin gate first.

**Pre-conditions:** admin_key_present, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: grading policy (rubric, grader authorization) — AUTH GAP: no route-level gate visible; feeds reputation scoring

**Rate limits:**<br>`PROPOSED: admin_write`<br>**per_admin**: 200/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; task_id, grade, rubric ref, grader identity

**Post-conditions:**<br>- grade recorded; reputation scoring ingests it

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; unauthenticated grades denied

**Reversibility:** reversible

**Reversibility plan:** Re-grade supersedes; history retained.

### `issue_creator_token`

Entry point: `src/sincor2/a2a_inbound_market.py:1659` · HTTP: `POST /v1/a2a/socialfi/issue` · Auth: `['membership (registered agent)']`

**Approval:** auto — Auto within the issuance envelope; live issuance is blocked at the P24 gate until unblocked by founder decision.

**Pre-conditions:** agent_registered, p24_live_allowed, content_screen_clean, rate_limit_ok

**Policy checks:**<br>- sincor2.defi.p24.policy.require_clean (name/symbol/description/bio screening)<br>- P24 live-block gate: live issuance refused while P24 is live-blocked (dry-run only by default)<br>- sincor2.a2a_rate_limits.issuance (5/hour + 20/day)

**Rate limits:**<br>`issuance`<br>**windows**: 5/hour + 20/day per agent<br>**note**: strictest tier; registration tier backstops agent_id rotation

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; agent_id, token name/symbol, screen result, dry-run flag

**Post-conditions:**<br>- dry-run: issuance simulated, no token created<br>- live (when allowed): token issued, idempotent

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; screen failure or live-block -> refuse issuance

**Reversibility:** reversible-with-cost

**Reversibility plan:** Issued tokens governed by the P24 token policy; cost is market/liquidity impact.

### `kernel_claude_reason`

Entry point: `src/sincor2/agency_kernel_tools.py:150` · HTTP: `n/a (in-kernel tool)` · Auth: `['operator (kernel)']`

**Approval:** auto — Auto: agency-kernel LLM reasoning calls (analysis, validation, summarisation, cross-reference).

**Pre-conditions:** operator_key_present, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: LLM-spend policy (per-call budgets, allowed task types) — external API spend

**Rate limits:**<br>`PROPOSED: kernel_llm`<br>**per_operator**: 200/hour<br>**note**: cost control; spend alerts on threshold

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; task type, token usage, cost, operator identity

**Post-conditions:**<br>- reasoning result returned within budget

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; budget exceeded -> deny

**Reversibility:** reversible

**Reversibility plan:** No external state; results cached or discarded.

### `kya_list`

Entry point: `src/sincor2/kya_blueprint.py:30` · HTTP: `POST /api/kya/list` · Auth: `['none']`

**Approval:** auto — Auto once admin-gated: creates a KYA record. Guardrail REQUIRES the admin gate first.

**Pre-conditions:** admin_key_present, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: KYA-listing policy — AUTH GAP: no route-level auth visible; lists (creates) a KYA record from an inbound payload. Must be admin-gated before exposure

**Rate limits:**<br>`PROPOSED: kya_admin`<br>**per_admin**: 100/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; payload ref, created record id, admin identity

**Post-conditions:**<br>- KYA record created/listed

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; unauthenticated listing denied

**Reversibility:** reversible

**Reversibility plan:** Record revoked via kya_revoke.

### `mcp_register_agent`

Entry point: `src/sincor2/mcp_server.py:310` · HTTP: `MCP tools/call register_agent` · Auth: `['operator_confirm']`

**Approval:** auto — Auto after operator confirmation: the two-phase confirm IS the human approval instrument; registration itself executes automatically.

**Pre-conditions:** operator_key_present, rate_limit_ok

**Policy checks:**<br>- MCP two-phase operator confirmation (mcp_server.py, default-deny)<br>- reputation starts at 0.0 (probation)

**Rate limits:**<br>`PROPOSED: mcp_write`<br>**per_operator**: 60/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; agent_id, confirmation record, probation flag

**Post-conditions:**<br>- agent in the marketplace directory at 0.0 reputation

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; unconfirmed registrations never execute (default-deny)

**Reversibility:** reversible

**Reversibility plan:** Directory entry removed; probation means no routing impact yet.

### `memory_ingest`

Entry point: `src/sincor2/blueprints/cortex.py:39` · HTTP: `POST /api/cortex/memory/ingest` · Auth: `['none visible at route level']`

**Approval:** auto — Auto once scoped: writes to the owning agent's long-term memory. Guardrail REQUIRES agent-scoping first.

**Pre-conditions:** operator_key_present, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: memory-scope policy — AUTH GAP: should be scoped to the owning agent; writes to agent long-term memory<br>- sincor2.compliance_guardrails.GuardrailsEngine.check_pii_storage

**Rate limits:**<br>`PROPOSED: memory`<br>**per_agent**: 100/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; agent_id, memory refs (never content)

**Post-conditions:**<br>- memory written to the owning agent's store

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; cross-agent writes denied

**Reversibility:** reversible

**Reversibility plan:** Memory entry deleted/expired per retention policy.

### `memory_run`

Entry point: `src/sincor2/blueprints/cortex.py:32` · HTTP: `POST /api/cortex/memory/run` · Auth: `['none visible at route level']`

**Approval:** auto — Auto once gated: triggers a memory-consolidation run.

**Pre-conditions:** operator_key_present, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: consolidation policy (what may be merged/forgotten) — AUTH GAP: no route-level gate visible

**Rate limits:**<br>`PROPOSED: memory`<br>**per_agent**: 10/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; agent_id, run id, merges/forgets summary

**Post-conditions:**<br>- consolidation completed; summary recorded

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Consolidation is append-with-tombstones; prior state recoverable from the log.

### `onboarding_submit`

Entry point: `src/sincor2/mvp_blueprints/auth.py:202` · HTTP: `POST /api/onboarding` · Auth: `['session']`

**Approval:** auto — Auto: persists the session owner's onboarding profile data.

**Pre-conditions:** session_owner_verified, rate_limit_ok

**Policy checks:**<br>- session ownership (mvp_blueprints/auth.py)<br>- sincor2.compliance_guardrails.GuardrailsEngine.check_pii_storage

**Rate limits:**<br>`PROPOSED: account`<br>**per_ip**: 30/min

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; user ref (redacted), fields persisted

**Post-conditions:**<br>- onboarding profile persisted

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Profile updated or deleted via profile_delete.

### `partner_status_update`

Entry point: `src/sincor2/mvp_blueprints/launch.py:60` · HTTP: `POST /api/launch/partners/<partner_id>` · Auth: `['admin']`

**Approval:** auto — Auto for the admin: marks partner outreach status after contact.

**Pre-conditions:** admin_key_present, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: partner-status policy (allowed status transitions) — currently inline in mvp_blueprints/launch.py

**Rate limits:**<br>`PROPOSED: admin_write`<br>**per_admin**: 100/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; partner_id, old/new status, admin identity

**Post-conditions:**<br>- status persisted

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Status re-set to the prior value with audit.

### `pool_release`

Entry point: `src/sincor2/a2a_inbound_market.py:1815` · HTTP: `POST /v1/a2a/pool/release` · Auth: `['admin']`

**Approval:** auto — Auto for the admin: returns an unspent allocation to the pool.

**Pre-conditions:** admin_key_present, allocation_released, rate_limit_ok

**Policy checks:**<br>- allocation-validity check (a2a_inbound_market.py)<br>- sincor2.a2a_rate_limits.bid

**Rate limits:**<br>`bid`<br>**windows**: 30/min + 300/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; task_id, amount, pool balance after, admin identity

**Post-conditions:**<br>- allocation returned to available pool balance

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Re-allocate via pool_allocate if needed.

### `quest_seed`

Entry point: `src/sincor2/kya/blueprint.py:132` · HTTP: `POST /v1/quest/seed` · Auth: `['none']`

**Approval:** auto — Auto for the admin: seeds a quest campaign in the airdrop quest registry.

**Pre-conditions:** admin_key_present, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: quest-campaign policy (reward pool backing, campaign rules) — admin-seeded

**Rate limits:**<br>`PROPOSED: quest`<br>**per_admin**: 20/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; campaign id, reward pool, rules ref, admin identity

**Post-conditions:**<br>- campaign live in the registry

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Campaign closed to new claims; seeded rewards returned to pool.

### `register_agent_a2a`

Entry point: `src/sincor2/a2a_inbound_ext.py:570` · HTTP: `POST /v1/a2a/register (aliases /api/v1/a2a/register, /api/marketplace/register)` · Auth: `['EIP-191 (required for re-registration)']`

**Approval:** auto — Auto: anonymous first registration is open; any wallet claim requires a valid EIP-191 proof (C3 fail-closed, 2026-10-08) — unverified wallet claims are rejected with 403. Re-registration requires EIP-191 proof by the registered wallet.

**Pre-conditions:** eip191_proof_valid, rate_limit_ok

**Policy checks:**<br>- sincor2.a2a_identity.verify_wallet_proof (required for re-registration; first registration open)<br>- reserved agent_id check (a2a_inbound_ext.py)<br>- sincor2.a2a_rate_limits.register (5/hour + 20/day per IP)

**Rate limits:**<br>`register`<br>**windows**: 5/hour + 20/day per IP<br>**note**: strictest policy in the set (Sybil surface)

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; agent_id, wallet claim, identity=verified/unverified, proof digest

**Post-conditions:**<br>- agent in registry; re-registration bound to owner wallet

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; bad re-registration proof -> deny (identity hijack blocked)

**Reversibility:** reversible

**Reversibility plan:** Record updated via re-registration; removal via admin path.

### `register_agent_marketplace`

Entry point: `src/sincor2/blueprints/marketplace.py:79` · HTTP: `POST /api/marketplace/register` · Auth: `['SINC credit gate (min staked 250, listing fee credits)']`

**Approval:** auto — Auto: older marketplace registration path; the SINC credit gate is the policy.

**Pre-conditions:** stake_sufficient, rate_limit_ok

**Policy checks:**<br>- SINC credit gate (min staked 250, listing fee credits; blueprints/marketplace.py)<br>- sincor2.a2a_rate_limits.register

**Rate limits:**<br>`register`<br>**windows**: 5/hour + 20/day per IP

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; agent_id, staked amount, fee credits

**Post-conditions:**<br>- agent listed; credit gate satisfied

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; below-minimum stake -> deny

**Reversibility:** reversible

**Reversibility plan:** Listing removed; stake subject to its own unstake terms.

### `registry_probe`

Entry point: `src/sincor2/blueprints/registry.py:78` · HTTP: `POST /api/registry/probe` · Auth: `['none']`

**Approval:** auto — Auto: probes a candidate agent's callback URL (outbound HTTP). SSRF guards required.

**Pre-conditions:** rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: probe policy (target allowlist, probe frequency, SSRF guards on the callback URL) — outbound HTTP

**Rate limits:**<br>`PROPOSED: probe`<br>**per_ip**: 30/min

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; target host (allowlisted), probe result

**Post-conditions:**<br>- probe result recorded (reachable/unreachable)

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; non-allowlisted targets denied

**Reversibility:** reversible

**Reversibility plan:** No state change; probe results expire.

### `sadas_publish`

Entry point: `src/sincor2/kya/blueprint.py:90` · HTTP: `POST /v1/sadas/publish` · Auth: `['admin']`

**Approval:** auto — Auto for the admin: publishes a SADAS attestation.

**Pre-conditions:** admin_key_present, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: attestation-content policy (what a SADAS attestation may assert) — admin-gated

**Rate limits:**<br>`PROPOSED: kya_admin`<br>**per_admin**: 100/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; attestation id, subject, admin identity

**Post-conditions:**<br>- attestation published and retrievable

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Attestation superseded/revoked with audit.

### `send_welcome_email`

Entry point: `src/sincor2/email_sender.py:129` · HTTP: `triggered by POST /api/signup (mvp_blueprints/ops.py:159)` · Auth: `['none (public signup)']`

**Approval:** auto — Auto: transactional welcome email triggered by a user's own signup.

**Pre-conditions:** rate_limit_ok, content_screen_clean

**Policy checks:**<br>- sincor2.compliance_guardrails.GuardrailsEngine.check_email_send (transactional template)

**Rate limits:**<br>`PROPOSED: email`<br>**per_ip**: 10/hour<br>**note**: tied to the signup rate

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; recipient (redacted), template id, signup ref

**Post-conditions:**<br>- welcome email accepted by provider

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; signup succeeds even if the email fails (email is best-effort, retried separately)

**Reversibility:** irreversible — **IRREVERSIBLE**

**Reversibility plan:** N/A — transactional email. Mitigation: fixed template, no marketing content.

### `stake_sinc_reputation`

Entry point: `src/sincor2/blueprints/marketplace.py:524` · HTTP: `POST /api/marketplace/reputation/<agent_id>/stake` · Auth: `['none']`

**Approval:** auto — Auto: ledger-only SINC stake boosting routing priority; on-chain tx required separately to finalise.

**Pre-conditions:** agent_registered, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: reputation-stake policy (min/max, lock terms, onchain finalisation binding) — AUTH GAP: no route-level auth visible; ledger-only boost

**Rate limits:**<br>`PROPOSED: reputation_stake`<br>**per_agent**: 20/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; agent_id, amount, priority delta

**Post-conditions:**<br>- ledger stake recorded; routing priority updated

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** unstake_sinc_reputation removes the boost.

### `submit_proof`

Entry point: `src/sincor2/a2a_inbound_market.py:1445` · HTTP: `POST /v1/a2a/proofs (alias /api/v1/proofs)` · Auth: `['membership (registered agent)']`

**Approval:** auto — Auto: submits a completion receipt_hash (HTTP 202); proof is evidence, not settlement.

**Pre-conditions:** agent_registered, rate_limit_ok

**Policy checks:**<br>- sincor2.a2a_rate_limits.task_write<br>- POLICY-MISSING: proof-validity policy (receipt_hash format, task-assignment binding) — currently inline

**Rate limits:**<br>`task_write`<br>**windows**: 20/min + 300/hour per agent\|ip composite key

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; task_id, agent_id, receipt_hash

**Post-conditions:**<br>- proof accepted (202) and linked to the task

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Superseding proof overwrites; history retained.

### `submit_task_sync`

Entry point: `src/sincor2/blueprints/marketplace.py:279` · HTTP: `POST /api/marketplace/tasks` · Auth: `['none']`

**Approval:** auto — Auto: synchronous task execution with settlement quoting. AUTH GAP flagged — guardrail requires an auth decision before broad exposure.

**Pre-conditions:** rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: marketplace task policy (settlement quoting, execution bounds) — AUTH GAP: no route-level auth visible<br>- sincor2.a2a_rate_limits.task_msg (30/min + 300/hour)

**Rate limits:**<br>`task_msg`<br>**windows**: 30/min + 300/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; task params, quote, execution result

**Post-conditions:**<br>- task executed; settlement quote honored

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Execution result recorded; compensating task issued on failure.

### `unstake_sinc_reputation`

Entry point: `src/sincor2/blueprints/marketplace.py:546` · HTTP: `POST /api/marketplace/reputation/<agent_id>/unstake` · Auth: `['none']`

**Approval:** auto — Auto: removes the reputation-boost stake.

**Pre-conditions:** agent_registered, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: reputation-unstake policy — AUTH GAP: no route-level auth visible

**Rate limits:**<br>`PROPOSED: reputation_stake`<br>**per_agent**: 20/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; agent_id, amount released

**Post-conditions:**<br>- boost removed; routing priority recomputed

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Re-stake via stake_sinc_reputation.

### `user_signup`

Entry point: `src/sincor2/mvp_blueprints/ops.py:159` · HTTP: `POST /api/signup` · Auth: `['none (public)']`

**Approval:** auto — Auto: public signup persists a lead, starts a session, fires a welcome email.

**Pre-conditions:** rate_limit_ok

**Policy checks:**<br>- sincor2.compliance_guardrails.GuardrailsEngine.check_email_send (welcome email)<br>- sincor2.compliance_guardrails.GuardrailsEngine.check_pii_storage (lead persistence)<br>- sincor2.a2a_rate_limits.register (public-endpoint Sybil tier)

**Rate limits:**<br>`register`<br>**windows**: 5/hour + 20/day per IP

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; lead ref (redacted), session id

**Post-conditions:**<br>- lead persisted; session started; welcome email queued

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; email failure does not fail the signup (retried separately)

**Reversibility:** reversible

**Reversibility plan:** profile_delete removes the lead data.

### `uw_register_agent`

Entry point: `src/sincor2/underwriting/blueprint.py:55` · HTTP: `POST /v1/agents/register` · Auth: `['none']`

**Approval:** auto — Auto: underwriting-runtime agent registration. AUTH GAP flagged.

**Pre-conditions:** rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: underwriting-registration policy (ERC-8004 identity family checks) — AUTH GAP: no route-level auth visible<br>- sincor2.a2a_rate_limits.register

**Rate limits:**<br>`register`<br>**windows**: 5/hour + 20/day per IP

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; agent_id, identity family refs

**Post-conditions:**<br>- agent registered in the underwriting runtime

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Revoked via uw_revoke_agent with human approval.

### `x402_execute_paid_resource`

Entry point: `src/sincor2/x402_payments.py:272` · HTTP: `GET\|POST /api/paid/<resource_id> (with access_token)` · Auth: `['x402 access token']`

**Approval:** auto — Auto: serves paid API payloads after a verified payment (token mint was the human-gated step).

**Pre-conditions:** x402_access_token_valid, rate_limit_ok

**Policy checks:**<br>- x402 access-token verification (scope, TTL; x402_payments.py)

**Rate limits:**<br>`PROPOSED: paid_api`<br>**per_agent**: 600/hour<br>**per_tenant**: 6000/hour

**Audit:**<br>- decision_event (shadow_monitor/events.py): proposed_action, risk_tier, policy_result, policy_reason_codes, human_disposition, outcome_evidence, blocked_in_live_mode; resource id, token scope, bytes served

**Post-conditions:**<br>- payload served within token scope/TTL

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; expired/out-of-scope token -> 402 re-challenge

**Reversibility:** reversible

**Reversibility plan:** Read-only serve; token revocation stops access.

---

## LOW actions

Read-only or agent-local writes. Auto; audit log optional.

| Action | Domain | Approval | Rate limits | Reversible |
|---|---|---|---|---|
| `contract_net_auction` | marketplace | auto | PROPOSED: demo | reversible |
| `heartbeat` | marketplace | auto | heartbeat | reversible |
| `kernel_file_read` | mcp | auto | PROPOSED: kernel_read | n/a |
| `kernel_web_search` | mcp | auto | PROPOSED: kernel_search | reversible |
| `kya_heartbeat` | identity | auto | heartbeat | reversible |
| `kya_sla_report` | identity | auto | PROPOSED: sla | reversible |
| `mcp_get_agent_card` | mcp | auto | read | n/a |
| `mcp_get_quote` | mcp | auto | quote | n/a |
| `mcp_get_task` | mcp | auto | read | n/a |
| `mcp_list_tasks` | mcp | auto | read | n/a |
| `registry_validate` | admin | auto | read | n/a |
| `sadas_subscribe` | identity | auto | PROPOSED: sla | reversible |
| `sinc_stake_calldata` | payments | auto | read | n/a |
| `sinc_unstake_calldata` | payments | auto | read | n/a |
| `sla_ping` | identity | auto | heartbeat | reversible |
| `sla_subscribe` | identity | auto | PROPOSED: sla | reversible |
| `x402_create_challenge` | payments | auto | PROPOSED: challenge | reversible |

### `contract_net_auction`

Entry point: `src/sincor2/blueprints/contract_net.py:124` · HTTP: `POST /api/contract-net/auctions` · Auth: `['none']`

**Approval:** auto — Auto: demo auction; no real funds or agents.

**Pre-conditions:** rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: demo-auction policy — demo Vickrey round over the demo roster only; writes engine history

**Rate limits:**<br>`PROPOSED: demo`<br>**per_ip**: 30/min

**Audit:**<br>- decision_event optional

**Post-conditions:**<br>- demo round recorded in engine history

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Demo history cleared; no external effect.

### `heartbeat`

Entry point: `src/sincor2/a2a_inbound_ext.py:626` · HTTP: `POST /v1/a2a/heartbeat` · Auth: `['EIP-191 (heartbeat auth)']`

**Approval:** auto — Auto: liveness heartbeat; freshness gates commit_bid.

**Pre-conditions:** eip191_proof_valid, agent_registered, rate_limit_ok

**Policy checks:**<br>- sincor2.a2a_identity.verify_wallet_proof (heartbeat auth; HeartbeatAuthError -> 401)<br>- sincor2.a2a_rate_limits.heartbeat (20/min + 300/hour, composite agent\|ip keying — a spoofer must not exhaust another agent's bucket)

**Rate limits:**<br>`heartbeat`<br>**windows**: 20/min + 300/hour per agent\|ip composite key<br>**note**: 20/min is ~10x headroom over the ~TTL/2 beat cadence

**Audit:**<br>- decision_event optional for heartbeats (high volume); anomalies (missed beats, auth failures) logged via shadow_monitor/alerting

**Post-conditions:**<br>- heartbeat timestamp recorded; agent marked live

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; bad proof -> 401, liveness unchanged

**Reversibility:** reversible

**Reversibility plan:** Next heartbeat overwrites; missed beats age out per TTL.

### `kernel_file_read`

Entry point: `src/sincor2/agency_kernel_tools.py:128` · HTTP: `n/a (in-kernel tool)` · Auth: `['operator (kernel)']`

**Approval:** auto — Auto: agency-kernel file read, path-sanitised and size-capped.

**Pre-conditions:** operator_key_present, rate_limit_ok

**Policy checks:**<br>- path sanitisation + size caps (agency_kernel_tools.py)

**Rate limits:**<br>`PROPOSED: kernel_read`<br>**per_operator**: 600/hour

**Audit:**<br>- decision_event optional (read-only)

**Post-conditions:**<br>- file content returned within caps

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; path-traversal attempts denied

**Reversibility:** n/a

**Reversibility plan:** N/A — read-only.

### `kernel_web_search`

Entry point: `src/sincor2/agency_kernel_tools.py:44` · HTTP: `n/a (in-kernel tool)` · Auth: `['operator (kernel)']`

**Approval:** auto — Auto: agency-kernel web search (outbound queries only).

**Pre-conditions:** operator_key_present, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: search policy (allowed query classes, result handling) — outbound queries only; currently inline in agency_kernel_tools.py

**Rate limits:**<br>`PROPOSED: kernel_search`<br>**per_operator**: 300/hour

**Audit:**<br>- decision_event optional; query logged

**Post-conditions:**<br>- search results returned; no external posts

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** No external state; results discarded.

### `kya_heartbeat`

Entry point: `src/sincor2/kya_blueprint.py:75` · HTTP: `POST /api/kya/heartbeat` · Auth: `['none']`

**Approval:** auto — Auto: liveness signal for a KYA identity.

**Pre-conditions:** agent_registered, rate_limit_ok

**Policy checks:**<br>- sincor2.a2a_rate_limits.heartbeat

**Rate limits:**<br>`heartbeat`<br>**windows**: 20/min + 300/hour

**Audit:**<br>- decision_event optional; anomalies logged

**Post-conditions:**<br>- KYA liveness timestamp recorded

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Ages out per TTL.

### `kya_sla_report`

Entry point: `src/sincor2/kya_blueprint.py:87` · HTTP: `POST /api/kya/sla` · Auth: `['none']`

**Approval:** auto — Auto: posts an SLA datapoint for a KYA identity.

**Pre-conditions:** agent_registered, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: SLA-datapoint policy (schema, submission frequency caps) — currently inline

**Rate limits:**<br>`PROPOSED: sla`<br>**per_agent**: 60/hour

**Audit:**<br>- decision_event optional; datapoint stored

**Post-conditions:**<br>- datapoint stored against the identity

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Datapoints are append-only evidence; corrections appended.

### `mcp_get_agent_card`

Entry point: `src/sincor2/mcp_server.py:272` · HTTP: `MCP tools/call get_agent_card` · Auth: `['none']`

**Approval:** auto — Auto: read-only MCP tool.

**Pre-conditions:** rate_limit_ok

**Policy checks:**<br>- sincor2.a2a_rate_limits.read

**Rate limits:**<br>`read`<br>**windows**: 120/min + 5000/hour

**Audit:**<br>- decision_event optional (read-only)

**Post-conditions:**<br>- agent card returned

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** n/a

**Reversibility plan:** N/A — read-only.

### `mcp_get_quote`

Entry point: `src/sincor2/mcp_server.py:290` · HTTP: `MCP tools/call get_quote` · Auth: `['none']`

**Approval:** auto — Auto: read-only price quote in AXM.

**Pre-conditions:** rate_limit_ok

**Policy checks:**<br>- sincor2.a2a_rate_limits.quote (60/min + 2000/hour per IP; unauthenticated price endpoint)

**Rate limits:**<br>`quote`<br>**windows**: 60/min + 2000/hour per IP

**Audit:**<br>- decision_event optional (read-only)

**Post-conditions:**<br>- quote returned; no commitment created

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** n/a

**Reversibility plan:** N/A — read-only.

### `mcp_get_task`

Entry point: `src/sincor2/mcp_server.py:252` · HTTP: `MCP tools/call get_task` · Auth: `['none']`

**Approval:** auto — Auto: read-only MCP tool.

**Pre-conditions:** rate_limit_ok

**Policy checks:**<br>- sincor2.a2a_rate_limits.read

**Rate limits:**<br>`read`<br>**windows**: 120/min + 5000/hour

**Audit:**<br>- decision_event optional (read-only)

**Post-conditions:**<br>- task detail returned

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** n/a

**Reversibility plan:** N/A — read-only.

### `mcp_list_tasks`

Entry point: `src/sincor2/mcp_server.py:230` · HTTP: `MCP tools/call list_tasks` · Auth: `['none']`

**Approval:** auto — Auto: read-only MCP tool.

**Pre-conditions:** rate_limit_ok

**Policy checks:**<br>- sincor2.a2a_rate_limits.read (120/min + 5000/hour)

**Rate limits:**<br>`read`<br>**windows**: 120/min + 5000/hour

**Audit:**<br>- decision_event optional (read-only)

**Post-conditions:**<br>- open auctions listed

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** n/a

**Reversibility plan:** N/A — read-only.

### `registry_validate`

Entry point: `src/sincor2/blueprints/registry.py:48` · HTTP: `POST /api/registry/validate` · Auth: `['none']`

**Approval:** auto — Auto: validates a payload; no state change.

**Pre-conditions:** rate_limit_ok

**Policy checks:**<br>- agent-card payload schema validation (blueprints/registry.py)

**Rate limits:**<br>`read`<br>**windows**: 120/min + 5000/hour

**Audit:**<br>- decision_event optional (read-only)

**Post-conditions:**<br>- validation verdict returned

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects; invalid payloads rejected

**Reversibility:** n/a

**Reversibility plan:** N/A — no state change.

### `sadas_subscribe`

Entry point: `src/sincor2/kya/blueprint.py:98` · HTTP: `POST /v1/sadas/subscribe` · Auth: `['none']`

**Approval:** auto — Auto: subscribes a KYA identity to SADAS attestations.

**Pre-conditions:** agent_registered, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: SADAS-subscription policy — currently inline

**Rate limits:**<br>`PROPOSED: sla`<br>**per_agent**: 20/hour

**Audit:**<br>- decision_event optional

**Post-conditions:**<br>- subscription recorded

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Unsubscribe.

### `sinc_stake_calldata`

Entry point: `src/sincor2/blueprints/sinc.py:345` · HTTP: `POST /api/sinc/stake` · Auth: `['none']`

**Approval:** auto — Auto: read-only calldata helper.

**Pre-conditions:** rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: calldata-generation policy — read-only helper; returns calldata instructions for SINCPlatformAccess (Base 8453); performs no chain tx

**Rate limits:**<br>`read`<br>**windows**: 120/min + 5000/hour

**Audit:**<br>- decision_event optional (read-only)

**Post-conditions:**<br>- calldata returned; no transaction broadcast

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** n/a

**Reversibility plan:** N/A — read-only, no state change.

### `sinc_unstake_calldata`

Entry point: `src/sincor2/blueprints/sinc.py:406` · HTTP: `POST /api/sinc/unstake` · Auth: `['none']`

**Approval:** auto — Auto: read-only calldata helper.

**Pre-conditions:** rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: calldata-generation policy — read-only; 7-day cool-down terms surfaced in the instructions

**Rate limits:**<br>`read`<br>**windows**: 120/min + 5000/hour

**Audit:**<br>- decision_event optional (read-only)

**Post-conditions:**<br>- calldata returned; no transaction broadcast

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** n/a

**Reversibility plan:** N/A — read-only.

### `sla_ping`

Entry point: `src/sincor2/kya/blueprint.py:73` · HTTP: `POST /v1/sla/ping` · Auth: `['none']`

**Approval:** auto — Auto: SLA heartbeat datapoint.

**Pre-conditions:** agent_registered, rate_limit_ok

**Policy checks:**<br>- sincor2.a2a_rate_limits.heartbeat

**Rate limits:**<br>`heartbeat`<br>**windows**: 20/min + 300/hour

**Audit:**<br>- decision_event optional

**Post-conditions:**<br>- ping recorded

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Ages out.

### `sla_subscribe`

Entry point: `src/sincor2/kya/blueprint.py:67` · HTTP: `POST /v1/sla/subscribe` · Auth: `['none']`

**Approval:** auto — Auto: subscribes a KYA identity to SLA monitoring.

**Pre-conditions:** agent_registered, rate_limit_ok

**Policy checks:**<br>- POLICY-MISSING: SLA-subscription policy — currently inline

**Rate limits:**<br>`PROPOSED: sla`<br>**per_agent**: 20/hour

**Audit:**<br>- decision_event optional

**Post-conditions:**<br>- subscription recorded

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Unsubscribe.

### `x402_create_challenge`

Entry point: `src/sincor2/x402_payments.py:120` · HTTP: `GET /api/paid/<resource_id> -> 402` · Auth: `['none']`

**Approval:** auto — Auto: issues an x402 payment challenge (402) for a paid resource.

**Pre-conditions:** rate_limit_ok

**Policy checks:**<br>- challenge-ledger policy (SQLite-backed; x402_payments.py)<br>- POLICY-MISSING: challenge policy (per-resource pricing, challenge TTL) — partially inline

**Rate limits:**<br>`PROPOSED: challenge`<br>**per_ip**: 60/min

**Audit:**<br>- decision_event optional; challenge id logged

**Post-conditions:**<br>- challenge recorded in the ledger with TTL

**Failure handling:** fail_closed: any failed pre-condition or policy check blocks the action before side effects; deny returns 403/429; alert via sincor2.shadow_monitor.alerting; DecisionEvent logged with policy_result="deny"; no partial ledger/state effects

**Reversibility:** reversible

**Reversibility plan:** Challenge expires unclaimed.

