"""Machine-readable guardrail sub-catalog for the SINCOR2 agent governance system.

Worker 3 (2026-10-08); hand-maintained after.

Every entry is keyed by a REAL action name from
``sincor2.governance.action_catalog.ACTIONS`` (validated by
:func:`validate_coverage` — no orphans, no gaps).

Schema per guardrail::

    {
        "pre_conditions":   [things that must hold before execution],
        "policy_checks":    [real policy references, or "POLICY-MISSING: <what>"],
        "approval":         "auto" | "human" | "quorum",
        "approval_note":    mechanism detail (interactive vs standing approval),
        "rate_limits":      {"policy_class": ... | "PROPOSED: ..." ...},
        "audit":            [DecisionEvent (shadow_monitor/events.py) fields to log],
        "post_conditions":  [how success is verified],
        "failure_handling": "fail_closed" (+ mechanism detail),
        "reversibility":    "reversible" | "reversible-with-cost" | "irreversible" | "n/a",
        "reversibility_plan": "..." (or "irreversible": True when irreversible),
    }

Approval rules (risk-tier driven, per the governance spec):
  * critical -> human approval REQUIRED, or quorum for treasury movements.
  * high     -> human approval. For autonomous protocol mechanics (auction
               commit/reveal/close, stake deposit) the human is the
               agent's human owner / the task poster, acting through
               pre-authorized standing approval envelopes; every execution
               logs a DecisionEvent with human_disposition="not_reviewed"
               for post-hoc review, and anything outside the envelope needs
               fresh interactive approval.
  * medium   -> auto with policy check + audit log.
  * low      -> auto, audit log optional.

Failure handling is fail-closed everywhere: ANY failed pre-condition or
policy check blocks the action (no partial effects), raises an alert via
``sincor2.shadow_monitor.alerting``, and logs a DecisionEvent with
policy_result="deny".

Vocabulary for pre_conditions (canonical tokens):
  agent_registered, kya_verified, kya_heartbeat_fresh, eip191_proof_valid,
  stake_sufficient, pool_balance_sufficient, pool_allocation_valid,
  agent_not_killed, admin_key_present, operator_key_present,
  adjudicator_signature_valid, provider_signature_valid, stripe_sig_valid,
  paypal_sig_valid, session_owner_verified, api_key_valid,
  x402_access_token_valid, idempotency_key_present, deadline_passed,
  deadline_not_passed, commit_phase_open, reveal_phase_open,
  auction_window_closed, hold_approved, hold_fresh, quorum_reached,
  executive_key_present, exec_live_armed, killswitch_clear,
  ofac_screen_clean, content_screen_clean, p24_live_allowed,
  task_has_no_bids, allocation_released, double_claim_guard_pass,
  sender_is_poster_or_admin, owner_signature_valid, cooldown_elapsed,
  rate_limit_ok, not_in_shadow_mode, standing_approval_envelope_ok

Policy check references point at REAL code where it exists:
  sincor2.a2a_identity.verify_wallet_proof          EIP-191 identity proof (fail-closed)
  sincor2.a2a_rate_limits.<policy>                  sliding-window enforcement (before_request)
  sincor2.shadow_monitor.effect_boundary.ShadowEffectBoundary.evaluate
  sincor2.compliance_guardrails.GuardrailsEngine.<check_*>
  sincor2.defi.p24.policy.require_clean             P24 content screening
  sincor2.defi.ofac_sdn.screen_wallet_address       sanctions screening
  sincor2.treasury_policy.TreasuryPolicy.<method>
  sincor2.defi.treasury_dao.Hold/Reviewer/Executor lifecycle
  sincor2.defi.gates.next_stage                     strategy stage gates
Missing coverage is marked "POLICY-MISSING: <what is needed>" — never
silently skipped.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .action_catalog import ACTIONS

# Canonical DecisionEvent audit payload used for every non-read action.
# (shadow_monitor/events.py DecisionEvent; append-only hash-chained store.)
_DECISION_EVENT = (
    "decision_event (shadow_monitor/events.py): proposed_action, risk_tier, "
    "policy_result, policy_reason_codes, human_disposition, outcome_evidence, "
    "blocked_in_live_mode"
)

_FAIL_CLOSED = (
    "fail_closed: any failed pre-condition or policy check blocks the action "
    "before side effects; deny returns 403/429; alert via "
    "sincor2.shadow_monitor.alerting; DecisionEvent logged with "
    'policy_result="deny"; no partial ledger/state effects'
)

GUARDRAILS: Dict[str, Dict[str, object]] = {
    # ==================================================================
    # CRITICAL — human approval required (quorum for treasury movements)
    # ==================================================================
    "file_dispute": {
        "pre_conditions": [
            "adjudicator_signature_valid",
            "eip191_proof_valid",
            "agent_registered",
            "auction_window_closed",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.a2a_identity.verify_wallet_proof (adjudicator EIP-191 secp256k1)",
            "sincor2.a2a_rate_limits.dispute (5/hour + 20/day per IP backstop)",
            "POLICY-MISSING: slashing-amount policy (ghosting 100% / quality 50% "
            "slash ratios currently live in a2a_inbound_market constants, no "
            "dedicated policy module)",
        ],
        "approval": "human",
        "approval_note": "The adjudicator's EIP-191 signature IS the human "
                         "approval: the adjudicator is the designated human "
                         "keyholder (Sepolia demo address "
                         "0x6AF997C5ba8db8153C95dd837b5555Ef5867cE3f).",
        "rate_limits": {
            "policy_class": "dispute",
            "windows": "5/hour + 20/day per IP",
            "note": "adjudicator-signed already; tier is a backstop "
                    "(a2a_rate_limits.A2A_RATE_POLICIES['dispute'])",
        },
        "audit": [
            _DECISION_EVENT + "; upheld flag, slash amount, slashed agent_id, "
            "adjudicator wallet, signature digest"
        ],
        "post_conditions": [
            "dispute record persisted with adjudicator signature",
            "upheld=true -> 50% of winner stake slashed to poster re-auction "
            "credit + agent reputation halved",
            "upheld=false -> winner stake released",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible-with-cost",
        "reversibility_plan": "Slash reversal requires a second adjudicator "
                              "ruling crediting the slashed stake back; "
                              "reputation half-loss is recomputed, not refunded "
                              "in kind — plan recorded before execution.",
    },
    "stripe_create_checkout": {
        "pre_conditions": [
            "rate_limit_ok",
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "POLICY-MISSING: checkout-intent policy (amount caps, currency "
            "allowlist, product mapping to /signup?plan=<product>) — no "
            "dedicated policy module; route-level checks only",
        ],
        "approval": "human",
        "approval_note": "The initiating customer is the human approver: a "
                         "checkout session is never created except from a "
                         "customer-initiated request; the session URL is never "
                         "auto-charged.",
        "rate_limits": {
            "policy_class": "PROPOSED: checkout",
            "per_ip": "20/min",
            "per_tenant": "200/hour",
        },
        "audit": [
            _DECISION_EVENT + "; stripe session id, amount, currency, product, "
            "success/cancel URLs"
        ],
        "post_conditions": [
            "Stripe session object created (provider-side)",
            "session id stored with idempotency key; no charge yet",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Session expires unused; Stripe dashboard "
                              "expire/cancel before completion.",
    },
    "stripe_webhook": {
        "pre_conditions": [
            "stripe_sig_valid",
            "not_in_shadow_mode",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.stripe_routes webhook signature verification "
            "(Stripe-Signature header, endpoint secret)",
            "POLICY-MISSING: provisioning policy (what each event type is "
            "allowed to provision, and value thresholds requiring human "
            "disposition) — currently implicit in the handler",
        ],
        "approval": "human",
        "approval_note": "Stripe's signature gates INTAKE (provider "
                         "counterparty approval). Value-granting (provisioning "
                         "access) requires human approval above the standing "
                         "auto-provision threshold; below threshold, "
                         "auto-provision logs DecisionEvent with "
                         'human_disposition="not_reviewed" for review queue.',
        "rate_limits": {
            "policy_class": "PROPOSED: webhook",
            "per_ip": "60/min",
            "note": "provider-signed; tier guards replay storms",
        },
        "audit": [
            _DECISION_EVENT + "; stripe event id, event type, idempotency key, "
            "provisioned entitlements, human_disposition"
        ],
        "post_conditions": [
            "event id recorded (replay-safe)",
            "provisioning applied exactly once (idempotency key)",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Provisioned access revoked via refund/void "
                              "flow; event replay rejected by idempotency key.",
    },
    "paypal_create_order": {
        "pre_conditions": [
            "rate_limit_ok",
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "POLICY-MISSING: paypal order-intent policy (amount caps, "
            "currency allowlist) — no dedicated policy module",
        ],
        "approval": "human",
        "approval_note": "The initiating customer is the human approver; "
                         "order creation is customer-initiated only, no "
                         "auto-capture.",
        "rate_limits": {
            "policy_class": "PROPOSED: checkout",
            "per_ip": "20/min",
            "per_tenant": "200/hour",
        },
        "audit": [
            _DECISION_EVENT + "; paypal order id, amount, currency"
        ],
        "post_conditions": [
            "PayPal order created (provider-side), idempotency-keyed",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Order voided before capture via PayPal API.",
    },
    "paypal_webhook": {
        "pre_conditions": [
            "paypal_sig_valid",
            "not_in_shadow_mode",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "PayPal IPN/webhook signature verification "
            "(mvp_blueprints/billing.py)",
            "POLICY-MISSING: provisioning policy (event-type -> entitlement "
            "mapping, value thresholds for human disposition)",
        ],
        "approval": "human",
        "approval_note": "PayPal's signature gates intake; value-granting "
                         "requires human approval above the standing "
                         "auto-provision threshold; below threshold "
                         "auto-provision with post-hoc review queue.",
        "rate_limits": {
            "policy_class": "PROPOSED: webhook",
            "per_ip": "60/min",
        },
        "audit": [
            _DECISION_EVENT + "; paypal event id, event type, idempotency key, "
            "human_disposition"
        ],
        "post_conditions": [
            "event id recorded (replay-safe)",
            "provisioning applied exactly once",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Reverse via PayPal refund; access revoked.",
    },
    "payment_webhook": {
        "pre_conditions": [
            "provider_signature_valid",
            "not_in_shadow_mode",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "provider signature verification for the configured provider",
            "POLICY-MISSING: generic provisioning policy (provider -> "
            "entitlement mapping, thresholds)",
        ],
        "approval": "human",
        "approval_note": "Provider signature gates intake; human approval "
                         "required for value grants above the standing "
                         "threshold.",
        "rate_limits": {
            "policy_class": "PROPOSED: webhook",
            "per_ip": "60/min",
        },
        "audit": [
            _DECISION_EVENT + "; provider, event id, type, human_disposition"
        ],
        "post_conditions": [
            "event id recorded (replay-safe)",
            "provisioning applied exactly once",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Provider-side refund/void; access revoked.",
    },
    "crypto_verify_payment": {
        "pre_conditions": [
            "not_in_shadow_mode",
            "rate_limit_ok",
            "idempotency_key_present",
        ],
        "policy_checks": [
            "onchain payment verification against the claimed tx_hash "
            "(mvp_blueprints/billing.py)",
            "sincor2.defi.ofac_sdn.screen_wallet_address (payer screening)",
            "POLICY-MISSING: mis-verification circuit breaker (re-check "
            "count, confirmation depth policy) — currently ad hoc",
        ],
        "approval": "human",
        "approval_note": "Verification math may run automatically, but "
                         "granting value (provisioning) requires human "
                         "approval: mis-verification is the money-path risk. "
                         "Human disposition recorded on the DecisionEvent.",
        "rate_limits": {
            "policy_class": "PROPOSED: money_verify",
            "per_ip": "10/min",
            "per_tenant": "100/hour",
        },
        "audit": [
            _DECISION_EVENT + "; claimed tx_hash, chain, confirmations, "
            "screened payer, provisioned entitlements, human_disposition"
        ],
        "post_conditions": [
            "onchain payment confirmed at required depth",
            "access provisioned exactly once (idempotency key)",
        ],
        "failure_handling": _FAIL_CLOSED + "; failed verification NEVER "
                                           "provisions access",
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A — provisioning is effectively final. "
                              "Mitigation is pre-action: human approval + "
                              "confirmation-depth verification before grant.",
    },
    "x402_verify_payment": {
        "pre_conditions": [
            "not_in_shadow_mode",
            "rate_limit_ok",
            "idempotency_key_present",
        ],
        "policy_checks": [
            "x402 onchain payment verification to the treasury address "
            "(x402_payments.py)",
            "sincor2.defi.ofac_sdn.screen_wallet_address (payer screening)",
            "POLICY-MISSING: access-token mint policy (token scope, TTL, "
            "revocation) — implicit in x402_payments.py",
        ],
        "approval": "human",
        "approval_note": "Payment math verified automatically; minting the "
                         "access token (granting value) requires human "
                         "approval or standing owner policy; DecisionEvent "
                         "carries human_disposition.",
        "rate_limits": {
            "policy_class": "PROPOSED: money_verify",
            "per_ip": "10/min",
            "per_tenant": "100/hour",
        },
        "audit": [
            _DECISION_EVENT + "; tx_hash, treasury address, amount, minted "
            "token scope/TTL, human_disposition"
        ],
        "post_conditions": [
            "onchain x402 payment confirmed to treasury",
            "access token minted once, scoped to the paid resource",
        ],
        "failure_handling": _FAIL_CLOSED + "; no token minted on any check "
                                           "failure",
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A — token grant is final. Mitigation: "
                              "short TTL, single-resource scope, human "
                              "approval before mint.",
    },
    "treasury_intent_allocate_live": {
        "pre_conditions": [
            "exec_live_armed",
            "executive_key_present",
            "quorum_reached",
            "killswitch_clear",
            "hold_approved",
            "hold_fresh",
            "ofac_screen_clean",
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "sincor2.defi.treasury_dao.Hold/Reviewer/Executor lifecycle "
            "(approved, unreviewed, stale, band checks)",
            "sincor2.treasury_policy.TreasuryPolicy.should_convert_before_treasury "
            "(5% fee -> 100% USDC/WETH conversion, NO burn)",
            "sincor2.defi.ofac_sdn.screen_wallet_address (counterparty/protocol)",
        ],
        "approval": "quorum",
        "approval_note": "QUORUM required: live treasury allocation moves "
                         "real funds onchain. Founder-gated deploy ceremony; "
                         "onchain executor key from Secure Vault only.",
        "rate_limits": {
            "policy_class": "PROPOSED: treasury_live",
            "per_operator": "5/hour",
            "per_tenant": "20/day",
            "note": "quorum acts as the hard gate; tier is a backstop",
        },
        "audit": [
            _DECISION_EVENT + "; intent hash, protocol calldata, amounts, "
            "quorum signatures, executor key id (never the key), chain receipt"
        ],
        "post_conditions": [
            "chain receipt confirmed for the broadcast transaction",
            "intent hash matches executed calldata (canonical_intent_hash)",
        ],
        "failure_handling": _FAIL_CLOSED + "; intent stays DRY-RUN; nothing "
                                           "broadcast without quorum + armed "
                                           "executor",
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A — onchain fund movement. Mitigation: "
                              "quorum, dry-run first, allocation bands, "
                              "kill switch that blocks NEW intents.",
    },
    "treasury_dao_execute": {
        "pre_conditions": [
            "hold_approved",
            "hold_fresh",
            "quorum_reached",
            "executive_key_present",
            "killswitch_clear",
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "sincor2.defi.treasury_dao.Executor (raises UnreviewedHold, "
            "StaleHold, BroadcastForbidden, BandViolation)",
            "sincor2.treasury_policy.TreasuryPolicy.should_convert_before_treasury",
        ],
        "approval": "quorum",
        "approval_note": "QUORUM: executes an approved treasury hold "
                         "onchain; moves real funds.",
        "rate_limits": {
            "policy_class": "PROPOSED: treasury_live",
            "per_operator": "5/hour",
            "per_tenant": "20/day",
        },
        "audit": [
            _DECISION_EVENT + "; hold id, review signatures, quorum "
            "signatures, chain receipt"
        ],
        "post_conditions": [
            "chain receipt confirmed",
            "hold marked executed; no double-execution",
        ],
        "failure_handling": _FAIL_CLOSED + "; UnreviewedHold/StaleHold/Band "
                                           "violations abort with no broadcast",
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A — onchain movement. Mitigation: quorum + "
                              "review + band + freshness checks before broadcast.",
    },
    "treasury_dao_claim_fees": {
        "pre_conditions": [
            "quorum_reached",
            "executive_key_present",
            "killswitch_clear",
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "sincor2.defi.treasury_dao.YieldLedger (accrued-fee accounting)",
            "sincor2.treasury_policy.TreasuryPolicy (conversion policy)",
        ],
        "approval": "quorum",
        "approval_note": "QUORUM: claims accrued fees to the treasury; "
                         "onchain, irreversible.",
        "rate_limits": {
            "policy_class": "PROPOSED: treasury_live",
            "per_operator": "5/hour",
            "per_tenant": "20/day",
        },
        "audit": [
            _DECISION_EVENT + "; claimed amounts, destination, quorum "
            "signatures, chain receipt"
        ],
        "post_conditions": [
            "chain receipt confirmed",
            "treasury balance reflects claimed fees",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A — onchain claim. Mitigation: quorum + "
                              "ledger reconciliation before claim.",
    },
    "send_outreach_email": {
        "pre_conditions": [
            "operator_key_present",
            "rate_limit_ok",
            "content_screen_clean",
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "sincor2.compliance_guardrails.GuardrailsEngine.check_email_send",
            "sincor2.compliance_guardrails.GuardrailsEngine.check_content "
            "(authenticity, claims, competitor-comparison, crypto-promotion)",
            "sincor2.shadow_monitor.effect_boundary.default_shadow_policy "
            "(email.send -> kill-switch priority path)",
        ],
        "approval": "human",
        "approval_note": "Human approval required: cold-outreach email to "
                         "third parties is irreversible once delivered. "
                         "Autonomous toggle must itself be human-armed.",
        "rate_limits": {
            "policy_class": "PROPOSED: outreach",
            "per_agent": "50/day",
            "per_tenant": "500/day",
            "note": "backstops the background outreach engine",
        },
        "audit": [
            _DECISION_EVENT + "; recipient (redacted via "
            "shadow_monitor/events.redact), template id, campaign, "
            "provider message id, human_disposition"
        ],
        "post_conditions": [
            "provider accepted the message (message id returned)",
            "no raw PII in the audit record (redact() applied)",
        ],
        "failure_handling": _FAIL_CLOSED + "; send aborted before provider "
                                           "handoff; queued message dropped, "
                                           "alert raised",
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A — email cannot be unsent. Mitigation: "
                              "human approval + content screening + recipient "
                              "allowlist before send.",
    },
    "run_outreach_cycle": {
        "pre_conditions": [
            "admin_key_present",
            "rate_limit_ok",
            "content_screen_clean",
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "sincor2.compliance_guardrails.GuardrailsEngine.check_email_send "
            "(per recipient)",
            "sincor2.compliance_guardrails.GuardrailsEngine.check_content",
        ],
        "approval": "human",
        "approval_note": "Admin manually triggers one full outreach cycle; "
                         "the admin's action IS the human approval for the "
                         "cycle. Each individual send still passes "
                         "check_email_send.",
        "rate_limits": {
            "policy_class": "PROPOSED: outreach_cycle",
            "per_admin": "4/day",
            "note": "one cycle may send many emails; per-email tier applies too",
        },
        "audit": [
            _DECISION_EVENT + "; cycle id, recipient count, per-send policy "
            "results, human_disposition=accepted (admin trigger)"
        ],
        "post_conditions": [
            "cycle completed; per-recipient outcomes recorded",
            "failed sends retried or dead-lettered, never silently dropped",
        ],
        "failure_handling": _FAIL_CLOSED + "; cycle aborts on policy failure; "
                                           "already-sent emails are logged, "
                                           "unsent remainder is blocked",
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A — delivered emails cannot be recalled. "
                              "Mitigation: admin trigger + per-send screening.",
    },
    "send_email": {
        "pre_conditions": [
            "operator_key_present",
            "rate_limit_ok",
            "content_screen_clean",
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "sincor2.compliance_guardrails.GuardrailsEngine.check_email_send",
            "sincor2.compliance_guardrails.GuardrailsEngine.check_content",
        ],
        "approval": "human",
        "approval_note": "Generic email primitive used by signup/billing/"
                         "outreach: each send path must carry its own human "
                         "approval (customer signup, billing trigger, or "
                         "operator-confirmed outreach).",
        "rate_limits": {
            "policy_class": "PROPOSED: email",
            "per_agent": "100/day",
            "per_tenant": "2000/day",
        },
        "audit": [
            _DECISION_EVENT + "; recipient (redacted), purpose, template, "
            "provider message id, human_disposition"
        ],
        "post_conditions": [
            "provider accepted the message",
            "audit record redacted of raw PII",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A — cannot be unsent. Mitigation: per-path "
                              "human approval + screening.",
    },
    "launch_review_approve_post": {
        "pre_conditions": [
            "admin_key_present",
            "content_screen_clean",
            "not_in_shadow_mode",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.compliance_guardrails.GuardrailsEngine.check_content "
            "(authenticity, claims, crypto-promotion)",
            "sincor2.shadow_monitor.effect_boundary.default_shadow_policy "
            "(social.post -> needs_approval)",
        ],
        "approval": "human",
        "approval_note": "Admin approval AND public posting in one action: "
                         "the admin's review click is the human approval. "
                         "Public comms cannot be unsent.",
        "rate_limits": {
            "policy_class": "PROPOSED: publish",
            "per_admin": "20/day",
        },
        "audit": [
            _DECISION_EVENT + "; draft id, channel, posted content hash, "
            "human_disposition=accepted"
        ],
        "post_conditions": [
            "post published (provider post id returned)",
            "draft marked posted; no double-post",
        ],
        "failure_handling": _FAIL_CLOSED + "; draft stays unposted on any "
                                           "check failure",
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A — public post. Mitigation: draft review "
                              "separate from posting; human approval gate "
                              "before the post call.",
    },
    "killswitch": {
        "pre_conditions": [
            "admin_key_present",
            "rate_limit_ok",
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "POLICY-MISSING: kill-switch auth policy — AUTH GAP: no "
            "route-level gate found in this tree; must be admin-gated before "
            "exposure (see docs/governance/ACTION_CATALOG.md)",
        ],
        "approval": "human",
        "approval_note": "Human operator kills or reinstates an agent. "
                         "AUTH GAP: guardrail REQUIRES the admin gate to be "
                         "implemented before this action is exposed.",
        "rate_limits": {
            "policy_class": "admin",
            "windows": "30/min + 200/hour",
            "note": "a2a_rate_limits admin tier (brute-force backstop); the "
                    "admin credential is the real gate",
        },
        "audit": [
            _DECISION_EVENT + "; target agent_id, kill/reinstate, admin "
            "identity, reason"
        ],
        "post_conditions": [
            "agent status flipped in registry",
            "fleet state confirms kill/reinstate (no stale liveness)",
        ],
        "failure_handling": _FAIL_CLOSED + "; unauthenticated calls denied; "
                                           "kill state never half-applied",
        "reversibility": "reversible",
        "reversibility_plan": "Reinstate via the same endpoint with human "
                              "approval; reinstate is audited identically.",
    },
    "kya_revoke": {
        "pre_conditions": [
            "admin_key_present",
            "agent_registered",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: revocation policy (who may revoke which "
            "identity, appeal/cool-down) — AUTH GAP: no route-level auth "
            "visible; must be admin-gated before exposure",
        ],
        "approval": "human",
        "approval_note": "Human admin revokes a KYA identity; agent is "
                         "excluded from discovery immediately. AUTH GAP: "
                         "guardrail REQUIRES admin gating first.",
        "rate_limits": {
            "policy_class": "PROPOSED: kya_admin",
            "per_admin": "60/hour",
        },
        "audit": [
            _DECISION_EVENT + "; revoked agent_id, reason, admin identity, "
            "human_disposition"
        ],
        "post_conditions": [
            "identity marked revoked",
            "list_agents() excludes the agent immediately (live_statuses "
            "consulted)",
        ],
        "failure_handling": _FAIL_CLOSED + "; unauthenticated revocation "
                                           "denied outright",
        "reversibility": "reversible",
        "reversibility_plan": "Re-verify via kya_verify with human approval; "
                              "revocation history retained in audit log.",
    },
    "uw_revoke_agent": {
        "pre_conditions": [
            "admin_key_present",
            "agent_registered",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: underwriting revocation policy — AUTH GAP: no "
            "route-level auth visible; must be admin-gated before exposure",
        ],
        "approval": "human",
        "approval_note": "Human admin revokes underwriting standing "
                         "(auth/permissions change). Guardrail REQUIRES the "
                         "admin gate first.",
        "rate_limits": {
            "policy_class": "PROPOSED: kya_admin",
            "per_admin": "60/hour",
        },
        "audit": [
            _DECISION_EVENT + "; revoked agent_id, envelope ids affected, "
            "admin identity, human_disposition"
        ],
        "post_conditions": [
            "standing revoked; open envelopes flagged for review",
            "agent excluded from underwritten mandates",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Re-underwrite via uw_underwrite with human "
                              "approval; full history in audit log.",
    },
    "effect_email_send": {
        "pre_conditions": [
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "sincor2.shadow_monitor.effect_boundary.ShadowEffectBoundary.evaluate "
            "(default_shadow_policy: email.send -> deny in shadow; "
            "needs_approval for live promotion)",
        ],
        "approval": "human",
        "approval_note": "Shadow-boundary intent: may only be PROPOSED/"
                         "recorded in shadow mode — never executed live "
                         "without a separately deployed executor. Live "
                         "promotion requires human approval.",
        "rate_limits": {
            "policy_class": "PROPOSED: shadow_effect",
            "per_agent": "100/day",
            "note": "proposals only; execution is gated separately",
        },
        "audit": [
            _DECISION_EVENT + "; effect type email.send, intent hash, "
            "blocked_in_live_mode=true while in shadow"
        ],
        "post_conditions": [
            "intent recorded with receipt (proposed, not executed)",
            "no live side effect performed",
        ],
        "failure_handling": _FAIL_CLOSED + "; ShadowBoundaryViolation on any "
                                           "live-execution attempt",
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A at the effect level. Guardrail: shadow "
                              "mode never executes; live promotion needs a "
                              "separately deployed executor + human approval.",
    },
    "effect_social_post": {
        "pre_conditions": [
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "sincor2.shadow_monitor.effect_boundary.ShadowEffectBoundary.evaluate "
            "(social.post -> deny in shadow; needs_approval for live)",
        ],
        "approval": "human",
        "approval_note": "Shadow intent; live promotion requires human "
                         "approval + separately deployed executor.",
        "rate_limits": {
            "policy_class": "PROPOSED: shadow_effect",
            "per_agent": "100/day",
        },
        "audit": [
            _DECISION_EVENT + "; effect type social.post, intent hash, "
            "blocked_in_live_mode=true"
        ],
        "post_conditions": [
            "intent recorded with receipt; no live side effect",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A. Shadow never executes; live needs human "
                              "approval.",
    },
    "effect_payment_transfer": {
        "pre_conditions": [
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "sincor2.shadow_monitor.effect_boundary.ShadowEffectBoundary.evaluate "
            "(payment.transfer -> WOULD_PAY kill-switch priority; deny in "
            "shadow)",
            "sincor2.defi.ofac_sdn.screen_wallet_address (destination, on "
            "live promotion)",
        ],
        "approval": "human",
        "approval_note": "Shadow intent (WOULD_PAY). Live promotion requires "
                         "human approval AND would inherit the treasury "
                         "quorum path for real funds.",
        "rate_limits": {
            "policy_class": "PROPOSED: shadow_effect",
            "per_agent": "50/day",
        },
        "audit": [
            _DECISION_EVENT + "; effect type payment.transfer, intent hash, "
            "amount, destination (redacted), blocked_in_live_mode=true"
        ],
        "post_conditions": [
            "intent recorded with receipt; no live side effect",
        ],
        "failure_handling": _FAIL_CLOSED + "; kill switch blocks would-pay "
                                           "intents first",
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A. Shadow never executes; live promotion "
                              "inherits money-path quorum.",
    },
    "effect_trade_swap": {
        "pre_conditions": [
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "sincor2.shadow_monitor.effect_boundary.ShadowEffectBoundary.evaluate "
            "(trade.swap -> WOULD_PAY; deny in shadow)",
        ],
        "approval": "human",
        "approval_note": "Shadow intent (WOULD_PAY). Live promotion requires "
                         "human approval.",
        "rate_limits": {
            "policy_class": "PROPOSED: shadow_effect",
            "per_agent": "50/day",
        },
        "audit": [
            _DECISION_EVENT + "; effect type trade.swap, intent hash, "
            "blocked_in_live_mode=true"
        ],
        "post_conditions": [
            "intent recorded with receipt; no live side effect",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A. Shadow never executes; live needs human "
                              "approval.",
    },
    "effect_contract_call": {
        "pre_conditions": [
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "sincor2.shadow_monitor.effect_boundary.ShadowEffectBoundary.evaluate "
            "(contract.call -> WOULD_PAY; deny in shadow)",
        ],
        "approval": "human",
        "approval_note": "Shadow intent (WOULD_PAY). Live promotion requires "
                         "human approval.",
        "rate_limits": {
            "policy_class": "PROPOSED: shadow_effect",
            "per_agent": "50/day",
        },
        "audit": [
            _DECISION_EVENT + "; effect type contract.call, intent hash, "
            "blocked_in_live_mode=true"
        ],
        "post_conditions": [
            "intent recorded with receipt; no live side effect",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A. Shadow never executes; live needs human "
                              "approval.",
    },
    "defi_execute_strategy": {
        "pre_conditions": [
            "operator_key_present",
            "exec_live_armed",
            "killswitch_clear",
            "ofac_screen_clean",
            "not_in_shadow_mode",
            "rate_limit_ok",
            "standing_approval_envelope_ok",
        ],
        "policy_checks": [
            "sincor2.defi.gates.next_stage (strategy lifecycle gates: spec, "
            "implementation, unit tests, invariant tests, fork sim)",
            "sincor2.defi.ofac_sdn.screen_wallet_address (protocol "
            "counterparty)",
            "sincor2.shadow_monitor.effect_boundary.default_shadow_policy "
            "(trade.swap/contract.call would-pay path)",
        ],
        "approval": "human",
        "approval_note": "Human operator approves the live strategy "
                         "execution: scan/plan ticks are automated "
                         "(defi_swarm_tick), but EXECUTION moving real funds "
                         "onchain requires human approval per strategy run.",
        "rate_limits": {
            "policy_class": "PROPOSED: defi_live",
            "per_operator": "10/hour",
            "per_tenant": "50/day",
        },
        "audit": [
            _DECISION_EVENT + "; strategy id, protocol, calldata hash, "
            "amounts, gate evidence, chain receipt, human_disposition"
        ],
        "post_conditions": [
            "chain receipt confirmed",
            "strategy state reflects execution; no duplicate execution",
        ],
        "failure_handling": _FAIL_CLOSED + "; any gate or screen failure "
                                           "keeps the strategy in plan-only "
                                           "mode; nothing broadcast",
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A — onchain fund movement. Mitigation: "
                              "stage gates + human approval + kill switch "
                              "blocking new executions.",
    },
    # ==================================================================
    # HIGH — human approval (standing owner/policy approval for autonomous
    #        protocol mechanics; interactive approval elsewhere)
    # ==================================================================
    "place_bid": {
        "pre_conditions": [
            "agent_registered",
            "kya_verified",
            "stake_sufficient",
            "agent_not_killed",
            "standing_approval_envelope_ok",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.a2a_identity.verify_wallet_proof (membership binding)",
            "sincor2.a2a_rate_limits.bid (30/min + 300/hour, composite "
            "agent|ip keying)",
            "POLICY-MISSING: bid-value policy (max bid vs stake ratio, "
            "bounty sanity bounds) — currently inline in "
            "a2a_inbound_market.py",
        ],
        "approval": "human",
        "approval_note": "Human-owner standing approval: the owner "
                         "pre-authorizes a bidding envelope (max bid, max "
                         "stake at risk); bids inside the envelope execute "
                         "autonomously with DecisionEvent audit and "
                         'human_disposition="not_reviewed"; outside the '
                         "envelope needs fresh interactive approval.",
        "rate_limits": {
            "policy_class": "bid",
            "windows": "30/min + 300/hour per agent|ip composite key",
            "note": "a2a_rate_limits.A2A_RATE_POLICIES['bid'] via "
                    "before_request on the a2a blueprint",
        },
        "audit": [
            _DECISION_EVENT + "; task_id, bid amount, stake locked, envelope "
            "bounds, human_disposition"
        ],
        "post_conditions": [
            "bid recorded; losing-bid release path armed on close",
            "stake lock reflected in the stake ledger",
        ],
        "failure_handling": _FAIL_CLOSED + "; bid rejected before any stake "
                                           "lock; no partial ledger row",
        "reversibility": "reversible-with-cost",
        "reversibility_plan": "Losing bids released on close_auction; "
                              "winner's bid locked for the auction lifecycle; "
                              "withdrawal rules apply — cost is the locked "
                              "stake window.",
    },
    "commit_bid": {
        "pre_conditions": [
            "agent_registered",
            "kya_verified",
            "kya_heartbeat_fresh",
            "stake_sufficient",
            "commit_phase_open",
            "agent_not_killed",
            "standing_approval_envelope_ok",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.a2a_identity.verify_wallet_proof (EIP-191)",
            "heartbeat freshness gate (KYA live_statuses)",
            "sincor2.a2a_rate_limits.bid (30/min + 300/hour composite keying)",
        ],
        "approval": "human",
        "approval_note": "Human-owner standing approval within the bidding "
                         "envelope; commit locks 50% of bounty (minStakeBps="
                         "5000). Ghosting (no reveal) slashes 100% — the "
                         "owner accepts this term in the envelope.",
        "rate_limits": {
            "policy_class": "bid",
            "windows": "30/min + 300/hour per agent|ip composite key",
        },
        "audit": [
            _DECISION_EVENT + "; task_id, commitment hash, locked stake, "
            "heartbeat freshness, human_disposition"
        ],
        "post_conditions": [
            "commitment stored; 50% of bounty locked as stake",
            "reveal window armed for this commitment",
        ],
        "failure_handling": _FAIL_CLOSED + "; stale heartbeat or insufficient "
                                           "stake -> 403 before lock",
        "reversibility": "reversible-with-cost",
        "reversibility_plan": "Valid reveal converts to a bid; no reveal "
                              "-> 100% slash (final). Cost accepted in the "
                              "standing approval envelope.",
    },
    "reveal_bid": {
        "pre_conditions": [
            "agent_registered",
            "reveal_phase_open",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "commitment recomputed in constant time (a2a_inbound_market.py)",
            "sincor2.a2a_rate_limits.bid",
        ],
        "approval": "human",
        "approval_note": "Human approval inherited from the commit_bid "
                         "standing approval: reveal is the idempotent "
                         "completion of an already-approved commitment.",
        "rate_limits": {
            "policy_class": "bid",
            "windows": "30/min + 300/hour per agent|ip composite key",
        },
        "audit": [
            _DECISION_EVENT + "; task_id, commitment hash, reveal validity, "
            "human_disposition"
        ],
        "post_conditions": [
            "only valid reveals become bids (idempotent)",
            "invalid reveals rejected without state change",
        ],
        "failure_handling": _FAIL_CLOSED + "; mismatched commitment rejected; "
                                           "no bid created",
        "reversibility": "reversible",
        "reversibility_plan": "Revealed bid follows the normal bid lifecycle "
                              "(release on loss, lock on win).",
    },
    "close_auction": {
        "pre_conditions": [
            "deadline_passed",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "reveal-deadline check (permissionless timeout() only after "
            "deadline; a2a_inbound_market.py)",
            "sincor2.a2a_rate_limits.task_write (20/min + 300/hour composite)",
        ],
        "approval": "human",
        "approval_note": "Human approval inherited from the task poster at "
                         "create_task time: auction parameters (deadlines, "
                         "slash terms) are poster-approved; close_auction "
                         "executes exactly those terms. Any deviation from "
                         "the approved terms is denied.",
        "rate_limits": {
            "policy_class": "task_write",
            "windows": "20/min + 300/hour per agent|ip composite key",
        },
        "audit": [
            _DECISION_EVENT + "; task_id, winner, ghosted commits slashed, "
            "released bids, human_disposition"
        ],
        "post_conditions": [
            "ghosted commits slashed 100%; losing bids released; winner "
            "locked",
            "auction state = closed (no re-close)",
        ],
        "failure_handling": _FAIL_CLOSED + "; pre-deadline calls rejected; "
                                           "slash failures abort the close "
                                           "with no partial state",
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A — slashing is final. Mitigation: "
                              "poster-approved terms + deadline enforcement "
                              "before any slash.",
    },
    "stake_deposit": {
        "pre_conditions": [
            "agent_registered",
            "eip191_proof_valid",
            "idempotency_key_present",
            "standing_approval_envelope_ok",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.a2a_identity.verify_wallet_proof (fail-closed EIP-191 "
            "identity binding on deposits)",
            "idempotency on tx_hash (a2a_inbound_market.py)",
            "sincor2.a2a_rate_limits.bid (same abuse class as bids)",
        ],
        "approval": "human",
        "approval_note": "Human-owner standing approval: self-service stake "
                         "deposit to the offchain AXM ledger (Pool 1). "
                         "Ledger-only accounting — no chain funds move.",
        "rate_limits": {
            "policy_class": "bid",
            "windows": "30/min + 300/hour per agent|ip composite key",
        },
        "audit": [
            _DECISION_EVENT + "; agent_id, amount, tx_hash (idempotency), "
            "ledger balance after, human_disposition"
        ],
        "post_conditions": [
            "ledger credited exactly once per tx_hash",
            "stake balance queryable via GET /v1/a2a/stake/<agent_id>",
        ],
        "failure_handling": _FAIL_CLOSED + "; identity-proof failure blocks "
                                           "before any ledger write",
        "reversibility": "reversible-with-cost",
        "reversibility_plan": "Unstake via kya_unstake_request/finalize "
                              "(cooldown applies); ledger adjustments "
                              "audited.",
    },
    "transfer_agent_record": {
        "pre_conditions": [
            "owner_signature_valid",
            "eip191_proof_valid",
            "agent_registered",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.a2a_identity.verify_wallet_proof (current-owner EIP-191 "
            "signature; a2a_inbound_ext.py)",
            "POLICY-MISSING: transfer policy (cool-down after transfer, "
            "stake encumbrance check) — currently inline",
        ],
        "approval": "human",
        "approval_note": "The current owner's EIP-191 signature IS the human "
                         "approval: identity transfer to a new owner wallet. "
                         "Audited as an identity change.",
        "rate_limits": {
            "policy_class": "PROPOSED: transfer",
            "per_agent": "5/hour",
            "per_ip": "20/hour",
            "note": "identity-change surface; strict PROPOSED tier",
        },
        "audit": [
            _DECISION_EVENT + "; agent_id, old wallet, new wallet, signature "
            "digest, human_disposition=accepted (owner-signed)"
        ],
        "post_conditions": [
            "record bound to the new owner wallet",
            "old wallet can no longer act for the agent",
        ],
        "failure_handling": _FAIL_CLOSED + "; signature mismatch -> transfer "
                                           "denied; record untouched",
        "reversibility": "reversible",
        "reversibility_plan": "New owner may transfer back with their own "
                              "EIP-191 signature; both transfers audited.",
    },
    "record_task_outcome": {
        "pre_conditions": [
            "admin_key_present",
            "agent_registered",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: reputation-write policy — AUTH GAP: no "
            "route-level auth visible; writes trust scores affecting other "
            "agents' routing priority. Must be admin/operator-gated before "
            "exposure",
        ],
        "approval": "human",
        "approval_note": "Human admin/operator approval required: writes "
                         "reputation affecting other agents' routing. "
                         "Guardrail REQUIRES the auth gate first.",
        "rate_limits": {
            "policy_class": "PROPOSED: reputation_write",
            "per_admin": "100/hour",
        },
        "audit": [
            _DECISION_EVENT + "; task_reference, agent_id, trust score "
            "delta, evidence, admin identity, human_disposition"
        ],
        "post_conditions": [
            "trust score recorded with evidence reference",
            "routing priority recompute reflects the write",
        ],
        "failure_handling": _FAIL_CLOSED + "; unauthenticated outcome posts "
                                           "denied",
        "reversibility": "reversible",
        "reversibility_plan": "Correcting outcome overwrites with new "
                              "evidence; history retained in audit log.",
    },
    "confirm_settlement": {
        "pre_conditions": [
            "admin_key_present",
            "idempotency_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "settlement quote match (tx_hash against quote; "
            "blueprints/marketplace.py)",
            "POLICY-MISSING: onchain verification policy — this path does "
            "NOT verify onchain; records are attested, not settled. "
            "Human attestation required.",
            "sincor2.a2a_rate_limits.settle (10/min + 100/hour) where the "
            "route is mapped",
        ],
        "approval": "human",
        "approval_note": "Human approval REQUIRED: confirms a payment "
                         "against a settlement quote by tx_hash with NO "
                         "onchain verification in this path. The human "
                         "attests the payment; the record is marked "
                         "attested-not-settled.",
        "rate_limits": {
            "policy_class": "settle",
            "windows": "10/min + 100/hour",
            "note": "money-path tier; human attestation is the real gate",
        },
        "audit": [
            _DECISION_EVENT + "; quote id, tx_hash, amount, attesting human, "
            "human_disposition=accepted, attested_not_settled flag"
        ],
        "post_conditions": [
            "settlement recorded once (idempotency key)",
            "record flagged as attested, not onchain-settled",
        ],
        "failure_handling": _FAIL_CLOSED + "; unverified claims never "
                                           "confirmed",
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A — confirmation is final. Mitigation: "
                              "human attestation + idempotency before confirm.",
    },
    "pool_fund": {
        "pre_conditions": [
            "admin_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "reserve configuration check (configured reserve amount; "
            "a2a_inbound_market.py)",
            "sincor2.a2a_rate_limits.bid (admin writes in the cheap-write "
            "abuse class)",
        ],
        "approval": "human",
        "approval_note": "Admin moves configured reserve into the spendable "
                         "launch-bounty pool (ledger-only, no onchain "
                         "movement). The admin's action is the human approval.",
        "rate_limits": {
            "policy_class": "bid",
            "windows": "30/min + 300/hour",
            "note": "ENDPOINT_POLICY maps POST /v1/a2a/pool/fund -> bid",
        },
        "audit": [
            _DECISION_EVENT + "; amount, source reserve, pool balance after, "
            "admin identity"
        ],
        "post_conditions": [
            "pool balance increased by the funded amount",
            "reserve ledger debited equally (no creation of funds)",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Unspent funds return via pool_release; pool "
                              "ledger is fully reconcilable.",
    },
    "pool_allocate": {
        "pre_conditions": [
            "admin_key_present",
            "pool_balance_sufficient",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "available-balance check (amount leaves available balance; "
            "a2a_inbound_market.py)",
            "sincor2.a2a_rate_limits.bid",
        ],
        "approval": "human",
        "approval_note": "Admin reserves pool funds for a task; the admin's "
                         "action is the human approval.",
        "rate_limits": {
            "policy_class": "bid",
            "windows": "30/min + 300/hour",
        },
        "audit": [
            _DECISION_EVENT + "; task_id, amount, pool balance after, admin "
            "identity"
        ],
        "post_conditions": [
            "allocation reserved; available balance reduced",
            "allocation linked to the task",
        ],
        "failure_handling": _FAIL_CLOSED + "; insufficient balance -> deny, "
                                           "no partial allocation",
        "reversibility": "reversible-with-cost",
        "reversibility_plan": "Release via pool_release returns funds to "
                              "available balance; cost is the reservation "
                              "window.",
    },
    "platform_create_checkout": {
        "pre_conditions": [
            "rate_limit_ok",
            "idempotency_key_present",
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "POLICY-MISSING: SINC/AXM checkout-quote policy (price source, "
            "quote TTL, amount caps) — idempotency-keyed offchain intent",
        ],
        "approval": "human",
        "approval_note": "The initiating customer is the human approver; "
                         "creates an offchain intent only — no charge.",
        "rate_limits": {
            "policy_class": "PROPOSED: checkout",
            "per_ip": "20/min",
            "per_tenant": "200/hour",
        },
        "audit": [
            _DECISION_EVENT + "; quote id, amount, currency, idempotency key"
        ],
        "post_conditions": [
            "quote created with TTL; idempotency-keyed",
            "no funds moved",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Quote expires unclaimed; cancel by idempotency "
                              "key.",
    },
    "platform_verify_payment": {
        "pre_conditions": [
            "not_in_shadow_mode",
            "rate_limit_ok",
            "idempotency_key_present",
        ],
        "policy_checks": [
            "onchain payment claim verification (mvp_blueprints/billing.py)",
            "sincor2.defi.ofac_sdn.screen_wallet_address (payer)",
            "POLICY-MISSING: confirmation-depth policy — ad hoc in handler",
        ],
        "approval": "human",
        "approval_note": "Human approval required: verifies an onchain "
                         "payment claim and provisions the purchase; "
                         "provisioning is effectively final.",
        "rate_limits": {
            "policy_class": "PROPOSED: money_verify",
            "per_ip": "10/min",
            "per_tenant": "100/hour",
        },
        "audit": [
            _DECISION_EVENT + "; tx_hash, confirmations, provisioned "
            "entitlements, human_disposition"
        ],
        "post_conditions": [
            "payment verified at required depth",
            "purchase provisioned exactly once",
        ],
        "failure_handling": _FAIL_CLOSED + "; failed verification never "
                                           "provisions",
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A — provisioning final. Mitigation: human "
                              "approval + depth verification pre-grant.",
    },
    "crypto_create_checkout": {
        "pre_conditions": [
            "rate_limit_ok",
            "idempotency_key_present",
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "POLICY-MISSING: crypto checkout-intent policy (accepted assets, "
            "address derivation, amount caps)",
        ],
        "approval": "human",
        "approval_note": "The initiating customer is the human approver; "
                         "creates an onchain checkout intent only.",
        "rate_limits": {
            "policy_class": "PROPOSED: checkout",
            "per_ip": "20/min",
            "per_tenant": "200/hour",
        },
        "audit": [
            _DECISION_EVENT + "; intent id, asset, amount, deposit address, "
            "idempotency key"
        ],
        "post_conditions": [
            "intent created with deposit address; idempotency-keyed",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Intent expires; unclaimed intents swept by "
                              "expiry job.",
    },
    "stripe_create_portal": {
        "pre_conditions": [
            "session_owner_verified",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "session ownership: portal is for the caller's own customer "
            "record (stripe_routes.py)",
            "POLICY-MISSING: customer-record binding policy (prove the "
            "session owns the Stripe customer id) — partially inline",
        ],
        "approval": "human",
        "approval_note": "The session owner (human customer) is the "
                         "approver: portal opens only for their own billing "
                         "record.",
        "rate_limits": {
            "policy_class": "PROPOSED: portal",
            "per_ip": "20/min",
        },
        "audit": [
            _DECISION_EVENT + "; stripe customer id, session id"
        ],
        "post_conditions": [
            "portal session URL returned for the caller's customer only",
        ],
        "failure_handling": _FAIL_CLOSED + "; cross-customer portal requests "
                                           "denied",
        "reversibility": "reversible",
        "reversibility_plan": "Portal sessions expire; no state change.",
    },
    "stripe_cancel_subscription": {
        "pre_conditions": [
            "session_owner_verified",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: subscription-ownership policy — AUTH GAP: no "
            "route-level auth visible; callers must own the subscription. "
            "Guardrail REQUIRES ownership proof before exposure",
        ],
        "approval": "human",
        "approval_note": "The subscription owner (human) approves the "
                         "cancel; ownership must be proven first (AUTH GAP "
                         "remediation).",
        "rate_limits": {
            "policy_class": "PROPOSED: subscription",
            "per_ip": "10/min",
        },
        "audit": [
            _DECISION_EVENT + "; subscription id, owner proof, "
            "human_disposition"
        ],
        "post_conditions": [
            "subscription canceled at provider",
            "local entitlement downgraded",
        ],
        "failure_handling": _FAIL_CLOSED + "; unproven ownership -> deny",
        "reversibility": "reversible-with-cost",
        "reversibility_plan": "Re-subscribe via checkout; cost is the "
                              "lapsed-service window.",
    },
    "cancel_subscription": {
        "pre_conditions": [
            "session_owner_verified",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "session ownership of the subscription (mvp_blueprints/billing.py)",
        ],
        "approval": "human",
        "approval_note": "The session owner (human) approves canceling "
                         "their own subscription with the provider.",
        "rate_limits": {
            "policy_class": "PROPOSED: subscription",
            "per_ip": "10/min",
        },
        "audit": [
            _DECISION_EVENT + "; subscription id, provider, session id, "
            "human_disposition"
        ],
        "post_conditions": [
            "provider confirms cancellation",
            "local entitlements updated",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible-with-cost",
        "reversibility_plan": "Re-subscribe; cost is the lapsed window.",
    },
    "sinc_credits_purchase": {
        "pre_conditions": [
            "rate_limit_ok",
            "idempotency_key_present",
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "POLICY-MISSING: SINC credit-purchase policy (price source, "
            "per-wallet caps, wallet binding) — currently inline in "
            "blueprints/sinc.py",
        ],
        "approval": "human",
        "approval_note": "The purchasing human approves; credits a SINC "
                         "purchase to their wallet.",
        "rate_limits": {
            "policy_class": "PROPOSED: credits",
            "per_ip": "20/min",
            "per_tenant": "200/hour",
        },
        "audit": [
            _DECISION_EVENT + "; wallet, amount, price, idempotency key, "
            "human_disposition"
        ],
        "post_conditions": [
            "credits credited exactly once (idempotency key)",
            "wallet balance reflects purchase",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible-with-cost",
        "reversibility_plan": "Refund path reverses the credit; cost is "
                              "settlement fees if any.",
    },
    "sinc_credits_spend": {
        "pre_conditions": [
            "api_key_valid",
            "pool_balance_sufficient",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "api_key ownership + balance check (blueprints/sinc.py)",
            "POLICY-MISSING: spend policy (per-key daily caps, purpose "
            "codes) — currently inline",
        ],
        "approval": "human",
        "approval_note": "The api_key holder (tenant operator, human) "
                         "approves each spend by signing the request; key "
                         "possession is the approval instrument.",
        "rate_limits": {
            "policy_class": "PROPOSED: credits",
            "per_agent": "100/min",
            "per_tenant": "1000/hour",
        },
        "audit": [
            _DECISION_EVENT + "; key id, amount, purpose, balance after"
        ],
        "post_conditions": [
            "credits debited; balance never negative",
            "spend recorded against the key",
        ],
        "failure_handling": _FAIL_CLOSED + "; insufficient balance -> deny; "
                                           "no partial debit",
        "reversibility": "reversible-with-cost",
        "reversibility_plan": "Operator-initiated credit-back with audit; "
                              "cost is reconciliation.",
    },
    "treasury_intent_allocate": {
        "pre_conditions": [
            "operator_key_present",
            "quorum_reached",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.defi.treasury_dao.Hold (allocation plan review state)",
            "POLICY-MISSING: dry-run vs live promotion policy — "
            "EXECUTE_LIVE arming procedure (founder-gated ceremony) is "
            "documented but not a code gate in this tree",
        ],
        "approval": "human",
        "approval_note": "Human operator queues a DRY-RUN allocation "
                         "intent; no chain broadcast unless EXECUTE_LIVE is "
                         "armed (which escalates to the quorum path of "
                         "treasury_intent_allocate_live).",
        "rate_limits": {
            "policy_class": "PROPOSED: treasury_dryrun",
            "per_operator": "20/hour",
        },
        "audit": [
            _DECISION_EVENT + "; intent hash, protocol, amounts, dry-run "
            "flag, human_disposition"
        ],
        "post_conditions": [
            "intent queued as DRY-RUN; no broadcast",
            "intent hash recorded for later promotion",
        ],
        "failure_handling": _FAIL_CLOSED + "; unreviewed intents never "
                                           "promoted to live",
        "reversibility": "reversible",
        "reversibility_plan": "Dry-run intents discarded; nothing onchain.",
    },
    "treasury_dao_propose": {
        "pre_conditions": [
            "operator_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.defi.treasury_dao.Hold (plan shape, allocation bands)",
        ],
        "approval": "human",
        "approval_note": "Human operator proposes a treasury hold "
                         "(allocation plan) for review; proposal is not "
                         "execution.",
        "rate_limits": {
            "policy_class": "PROPOSED: treasury_dryrun",
            "per_operator": "20/hour",
        },
        "audit": [
            _DECISION_EVENT + "; hold id, plan, bands, proposer, "
            "human_disposition"
        ],
        "post_conditions": [
            "hold recorded in proposed state",
            "reviewers notified",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Proposal withdrawn before review; no "
                              "onchain effect.",
    },
    "treasury_dao_review": {
        "pre_conditions": [
            "operator_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.defi.treasury_dao.Reviewer (approve/reject with reason)",
        ],
        "approval": "human",
        "approval_note": "The reviewer IS the human approval gate: "
                         "approve/reject with reason is the decision before "
                         "any broadcast.",
        "rate_limits": {
            "policy_class": "PROPOSED: treasury_review",
            "per_operator": "20/hour",
        },
        "audit": [
            _DECISION_EVENT + "; hold id, decision, reason, reviewer, "
            "human_disposition"
        ],
        "post_conditions": [
            "hold marked approved or rejected with reason",
            "only approved holds are executable",
        ],
        "failure_handling": _FAIL_CLOSED + "; unreviewed holds are not "
                                           "executable (UnreviewedHold)",
        "reversibility": "reversible",
        "reversibility_plan": "Review decision superseded by a later review "
                              "before execution; audit retains both.",
    },
    "kya_bind": {
        "pre_conditions": [
            "eip191_proof_valid",
            "agent_registered",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.a2a_identity.verify_wallet_proof (EIP-191 proof of "
            "wallet control; kya_blueprint.py)",
        ],
        "approval": "human",
        "approval_note": "The wallet owner's EIP-191 signature IS the human "
                         "approval: binds agent_id to a principal. Identity "
                         "anchor.",
        "rate_limits": {
            "policy_class": "PROPOSED: kya_bind",
            "per_agent": "10/hour",
            "per_ip": "50/hour",
        },
        "audit": [
            _DECISION_EVENT + "; agent_id, principal, signature digest, "
            "human_disposition=accepted (owner-signed)"
        ],
        "post_conditions": [
            "binding recorded; lookups resolve agent_id -> principal",
        ],
        "failure_handling": _FAIL_CLOSED + "; bad signature -> no binding",
        "reversibility": "reversible",
        "reversibility_plan": "Re-bind with a new owner signature; binding "
                              "history retained.",
    },
    "kya_verify": {
        "pre_conditions": [
            "admin_key_present",
            "agent_registered",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "stake-tx evidence check where supplied (kya_blueprint.py)",
            "POLICY-MISSING: verification policy — AUTH GAP: no route-level "
            "auth visible; marks an identity verified. Must be admin-gated "
            "before exposure",
        ],
        "approval": "human",
        "approval_note": "Human admin verifies a KYA identity; trust "
                         "decision with money-path consequences. Guardrail "
                         "REQUIRES the admin gate first.",
        "rate_limits": {
            "policy_class": "PROPOSED: kya_admin",
            "per_admin": "60/hour",
        },
        "audit": [
            _DECISION_EVENT + "; agent_id, evidence, admin identity, "
            "human_disposition"
        ],
        "post_conditions": [
            "identity marked verified with evidence reference",
        ],
        "failure_handling": _FAIL_CLOSED + "; unverifiable claims stay "
                                           "unverified",
        "reversibility": "reversible",
        "reversibility_plan": "Revoke via kya_revoke; verification history "
                              "retained.",
    },
    "kya_unstake_request": {
        "pre_conditions": [
            "owner_signature_valid",
            "eip191_proof_valid",
            "agent_registered",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.a2a_identity.verify_wallet_proof (owner proof)",
            "POLICY-MISSING: unstake-request policy (cool-down terms, "
            "encumbrance check against locked bids) — partially inline",
        ],
        "approval": "human",
        "approval_note": "The identity owner's EIP-191 signature IS the "
                         "human approval to start the unstake flow.",
        "rate_limits": {
            "policy_class": "PROPOSED: kya_unstake",
            "per_agent": "5/hour",
        },
        "audit": [
            _DECISION_EVENT + "; agent_id, stake amount, signature digest, "
            "human_disposition"
        ],
        "post_conditions": [
            "unstake request recorded; cool-down started",
            "stake not yet released",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Request canceled before finalize; no funds "
                              "moved.",
    },
    "kya_unstake_finalize": {
        "pre_conditions": [
            "owner_signature_valid",
            "eip191_proof_valid",
            "cooldown_elapsed",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.a2a_identity.verify_wallet_proof (owner proof)",
            "cool-down elapsed check (kya_blueprint.py)",
            "POLICY-MISSING: encumbrance policy (locked auction stake must "
            "block finalize) — verify inline coverage",
        ],
        "approval": "human",
        "approval_note": "The identity owner's EIP-191 signature IS the "
                         "human approval; finalize releases identity stake.",
        "rate_limits": {
            "policy_class": "PROPOSED: kya_unstake",
            "per_agent": "5/hour",
        },
        "audit": [
            _DECISION_EVENT + "; agent_id, released amount, cool-down "
            "evidence, signature digest"
        ],
        "post_conditions": [
            "stake released to the owner wallet",
            "identity stake balance zeroed",
        ],
        "failure_handling": _FAIL_CLOSED + "; early finalize or encumbered "
                                           "stake -> deny",
        "reversibility": "reversible-with-cost",
        "reversibility_plan": "Re-stake to restore standing; cost is the "
                              "cool-down window and lost priority.",
    },
    "quest_claim": {
        "pre_conditions": [
            "kya_verified",
            "owner_signature_valid",
            "double_claim_guard_pass",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "double-claim protection (kya/blueprint.py)",
            "POLICY-MISSING: quest-eligibility policy (campaign rules, "
            "per-identity caps, sybil checks) — partially inline",
        ],
        "approval": "human",
        "approval_note": "The claiming identity owner (EIP-191) is the "
                         "human approver; value-adjacent claim with "
                         "double-claim guard.",
        "rate_limits": {
            "policy_class": "PROPOSED: quest",
            "per_agent": "10/hour",
            "per_ip": "50/hour",
        },
        "audit": [
            _DECISION_EVENT + "; quest id, agent_id, reward, claim nonce, "
            "human_disposition"
        ],
        "post_conditions": [
            "reward credited once (claim nonce consumed)",
            "repeat claim with same nonce denied",
        ],
        "failure_handling": _FAIL_CLOSED + "; duplicate or ineligible claim "
                                           "denied",
        "reversibility": "reversible-with-cost",
        "reversibility_plan": "Fraudulent claim clawed back via dispute/"
                              "slash path; cost is investigation.",
    },
    "uw_underwrite": {
        "pre_conditions": [
            "admin_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: underwriting policy (allow/deny criteria, "
            "evidence requirements) — AUTH GAP: no route-level auth "
            "visible; decision gates money movement. Must be admin-gated "
            "before exposure",
        ],
        "approval": "human",
        "approval_note": "Human underwriter approves/denies the mandate; "
                         "the decision gates money movement. Guardrail "
                         "REQUIRES the admin gate first.",
        "rate_limits": {
            "policy_class": "PROPOSED: underwrite",
            "per_admin": "60/hour",
        },
        "audit": [
            _DECISION_EVENT + "; envelope id, decision, evidence, "
            "underwriter identity, human_disposition"
        ],
        "post_conditions": [
            "mandate recorded as underwritten (allow) or denied with reason",
        ],
        "failure_handling": _FAIL_CLOSED + "; unauthenticated underwrite "
                                           "denied; ambiguous mandates stay "
                                           "denied",
        "reversibility": "reversible",
        "reversibility_plan": "Decision superseded before settlement; "
                              "uw_revoke_envelope after settlement review.",
    },
    "uw_settle_mandate": {
        "pre_conditions": [
            "admin_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: mandate-settlement policy (settlement "
            "conditions, evidence) — AUTH GAP: no route-level auth visible. "
            "Must be admin-gated before exposure",
        ],
        "approval": "human",
        "approval_note": "Human approval required: settles an underwritten "
                         "mandate (irreversible). Guardrail REQUIRES the "
                         "admin gate first.",
        "rate_limits": {
            "policy_class": "PROPOSED: underwrite",
            "per_admin": "60/hour",
        },
        "audit": [
            _DECISION_EVENT + "; envelope id, settlement evidence, admin "
            "identity, human_disposition"
        ],
        "post_conditions": [
            "mandate marked settled with evidence",
            "no double-settlement (envelope state machine)",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A — settlement final. Mitigation: human "
                              "approval + evidence before settle.",
    },
    "uw_revoke_envelope": {
        "pre_conditions": [
            "admin_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: envelope-revocation policy — AUTH GAP: no "
            "route-level auth visible. Must be admin-gated before exposure",
        ],
        "approval": "human",
        "approval_note": "Human admin revokes a mandate envelope; "
                         "guardrail REQUIRES the admin gate first.",
        "rate_limits": {
            "policy_class": "PROPOSED: underwrite",
            "per_admin": "60/hour",
        },
        "audit": [
            _DECISION_EVENT + "; envelope id, reason, admin identity, "
            "human_disposition"
        ],
        "post_conditions": [
            "envelope marked revoked; unsettled legs blocked",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Envelope re-issued via uw_underwrite with "
                              "fresh human approval.",
    },
    "sponsored_stake": {
        "pre_conditions": [
            "admin_key_present",
            "agent_registered",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "genesis-cohort eligibility (opt-in mechanism; "
            "sponsored_stake.py)",
            "sincor2.a2a_rate_limits.admin (credential-gated backstop)",
        ],
        "approval": "human",
        "approval_note": "Human admin fronts an agent's first stake; the "
                         "admin's action is the human approval.",
        "rate_limits": {
            "policy_class": "admin",
            "windows": "30/min + 200/hour",
        },
        "audit": [
            _DECISION_EVENT + "; agent_id, sponsored amount, cohort, admin "
            "identity, human_disposition"
        ],
        "post_conditions": [
            "stake credited to the agent's ledger",
            "sponsorship terms recorded (repayment/clawback)",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible-with-cost",
        "reversibility_plan": "Sponsorship recalled per terms; cost is the "
                              "fronted-stake window.",
    },
    "recovery_sponsor": {
        "pre_conditions": [
            "admin_key_present",
            "agent_registered",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "bankruptcy eligibility as a pure function of evidence + wallet "
            "history (tiered C; recovery.py)",
            "sincor2.a2a_rate_limits.admin",
        ],
        "approval": "human",
        "approval_note": "Human admin fronts recovery stake for an "
                         "honestly-bankrupt agent; eligibility is computed, "
                         "approval is human.",
        "rate_limits": {
            "policy_class": "admin",
            "windows": "30/min + 200/hour",
        },
        "audit": [
            _DECISION_EVENT + "; agent_id, evidence refs, tier, amount, "
            "admin identity, human_disposition"
        ],
        "post_conditions": [
            "recovery stake credited; agent re-enabled per tiered-C ladder",
        ],
        "failure_handling": _FAIL_CLOSED + "; ineligible agents denied even "
                                           "with admin key (eligibility is a "
                                           "pure function)",
        "reversibility": "reversible-with-cost",
        "reversibility_plan": "Recovery terms enforced; re-offense follows "
                              "the fixed escalation ladder.",
    },
    "profile_delete": {
        "pre_conditions": [
            "session_owner_verified",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "session ownership (mvp_blueprints/auth.py)",
            "POLICY-MISSING: deletion policy (grace period, data-retention "
            "exceptions for audit/finance records) — currently inline",
        ],
        "approval": "human",
        "approval_note": "The profile owner (human, via session) approves "
                         "deleting their own data. Irreversible: confirm "
                         "explicitly.",
        "rate_limits": {
            "policy_class": "PROPOSED: account",
            "per_ip": "10/hour",
        },
        "audit": [
            _DECISION_EVENT + "; user id (redacted), scope deleted, "
            "retention exceptions, human_disposition=accepted"
        ],
        "post_conditions": [
            "profile/data deleted except legal-retention records",
            "session invalidated",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A — deletion final. Mitigation: explicit "
                              "human confirmation + grace period before "
                              "purge.",
    },
    "kernel_python_exec": {
        "pre_conditions": [
            "operator_key_present",
            "rate_limit_ok",
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "kernel sandbox policy (agency_kernel_tools.py): arbitrary code "
            "runs inside the kernel sandbox",
            "POLICY-MISSING: code-execution policy (allowed imports, "
            "network/filesystem allowlists, execution timeouts) — sandbox "
            "escape = host risk",
        ],
        "approval": "human",
        "approval_note": "Human operator approves kernel code execution; "
                         "sandbox escape is a host-level risk.",
        "rate_limits": {
            "policy_class": "PROPOSED: kernel_exec",
            "per_operator": "60/hour",
        },
        "audit": [
            _DECISION_EVENT + "; code hash, allowed-import set, timeout, "
            "result summary, operator identity"
        ],
        "post_conditions": [
            "execution completed within sandbox and timeout",
            "no host filesystem/network access outside allowlists",
        ],
        "failure_handling": _FAIL_CLOSED + "; sandbox violation kills the "
                                           "execution and alerts",
        "reversibility": "reversible",
        "reversibility_plan": "Sandbox is ephemeral; host effects require "
                              "separate review. Roll back any persisted "
                              "artifacts.",
    },
    "mcp_submit_bid": {
        "pre_conditions": [
            "operator_key_present",
            "standing_approval_envelope_ok",
            "rate_limit_ok",
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "MCP two-phase operator confirmation (mcp_server.py, "
            "default-deny)",
            "mode policy: auto / legacy / commit / reveal",
        ],
        "approval": "human",
        "approval_note": "Two-phase operator confirmation IS the human "
                         "approval: default-deny, operator confirms the bid "
                         "before submission.",
        "rate_limits": {
            "policy_class": "PROPOSED: mcp_write",
            "per_operator": "60/hour",
        },
        "audit": [
            _DECISION_EVENT + "; task_id, mode, bid params, operator "
            "confirmation record, human_disposition=accepted"
        ],
        "post_conditions": [
            "bid submitted in the confirmed mode",
            "confirmation record linked to the bid",
        ],
        "failure_handling": _FAIL_CLOSED + "; unconfirmed bids never submit "
                                           "(default-deny)",
        "reversibility": "reversible-with-cost",
        "reversibility_plan": "Follows the bid lifecycle (release/lock); "
                              "commit mode inherits ghosting-slash terms.",
    },
    "crm_sync_on_cutover": {
        "pre_conditions": [
            "operator_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.compliance_guardrails.GuardrailsEngine.check_email_send "
            "(owner notification email)",
        ],
        "approval": "human",
        "approval_note": "Human operator approves the CRM cutover sync; "
                         "notifies the project owner by email with contact "
                         "counts.",
        "rate_limits": {
            "policy_class": "PROPOSED: crm",
            "per_operator": "10/hour",
        },
        "audit": [
            _DECISION_EVENT + "; contact counts, owner notified, "
            "human_disposition"
        ],
        "post_conditions": [
            "owner notified with counts",
            "CRM records consistent post-cutover",
        ],
        "failure_handling": _FAIL_CLOSED + "; cutover blocked until owner "
                                           "notification succeeds",
        "reversibility": "reversible-with-cost",
        "reversibility_plan": "Cutover rolled back from pre-cutover "
                              "snapshot; cost is reconciliation.",
    },
    "effect_crm_write": {
        "pre_conditions": [
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "sincor2.shadow_monitor.effect_boundary.ShadowEffectBoundary.evaluate "
            "(crm.write -> deny in shadow; needs_approval for live)",
        ],
        "approval": "human",
        "approval_note": "Shadow intent; live promotion requires human "
                         "approval.",
        "rate_limits": {
            "policy_class": "PROPOSED: shadow_effect",
            "per_agent": "100/day",
        },
        "audit": [
            _DECISION_EVENT + "; effect type crm.write, intent hash, "
            "blocked_in_live_mode=true"
        ],
        "post_conditions": [
            "intent recorded with receipt; no live side effect",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "At the effect level: CRM writes are "
                              "correctable; live promotion still needs human "
                              "approval.",
    },
    "effect_crm_delete": {
        "pre_conditions": [
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "sincor2.shadow_monitor.effect_boundary.ShadowEffectBoundary.evaluate "
            "(crm.delete -> deny in shadow; needs_approval for live)",
        ],
        "approval": "human",
        "approval_note": "Shadow intent; deletions are not restorable by "
                         "the boundary — live promotion requires human "
                         "approval with extra scrutiny.",
        "rate_limits": {
            "policy_class": "PROPOSED: shadow_effect",
            "per_agent": "50/day",
        },
        "audit": [
            _DECISION_EVENT + "; effect type crm.delete, intent hash, "
            "blocked_in_live_mode=true"
        ],
        "post_conditions": [
            "intent recorded with receipt; no live side effect",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A — deletions are not restorable by the "
                              "boundary. Mitigation: human approval + "
                              "pre-delete export.",
    },
    "effect_message_send": {
        "pre_conditions": [
            "not_in_shadow_mode",
        ],
        "policy_checks": [
            "sincor2.shadow_monitor.effect_boundary.ShadowEffectBoundary.evaluate "
            "(message.send -> deny in shadow; needs_approval for live)",
            "sincor2.compliance_guardrails.GuardrailsEngine.check_content "
            "(on live promotion)",
        ],
        "approval": "human",
        "approval_note": "Shadow intent; live promotion requires human "
                         "approval + content screening.",
        "rate_limits": {
            "policy_class": "PROPOSED: shadow_effect",
            "per_agent": "100/day",
        },
        "audit": [
            _DECISION_EVENT + "; effect type message.send, intent hash, "
            "blocked_in_live_mode=true"
        ],
        "post_conditions": [
            "intent recorded with receipt; no live side effect",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A — sent messages cannot be recalled. "
                              "Mitigation: human approval before live send.",
    },
    # ==================================================================
    # MEDIUM — auto with policy check + audit log
    # ==================================================================
    "create_task": {
        "pre_conditions": [
            "pool_allocation_valid",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.a2a_rate_limits.task_write (20/min + 300/hour composite "
            "agent|ip keying)",
            "POLICY-MISSING: task-content policy (bounty bounds, description "
            "screening, poster attribution rules) — anonymous posts allowed "
            "by design",
        ],
        "approval": "auto",
        "approval_note": "Auto: task creation is the poster's own action; "
                         "anonymous or EIP-191-attributed.",
        "rate_limits": {
            "policy_class": "task_write",
            "windows": "20/min + 300/hour per agent|ip composite key",
            "note": "ENDPOINT_POLICY maps POST /v1/a2a/tasks -> task_write",
        },
        "audit": [
            _DECISION_EVENT + "; task_id, bounty, poster (or anonymous), "
            "auction params"
        ],
        "post_conditions": [
            "task listed; auction params (deadlines, slash terms) stored as "
            "the poster-approved terms for close_auction",
        ],
        "failure_handling": _FAIL_CLOSED + "; invalid bounty/params rejected "
                                           "before listing",
        "reversibility": "reversible",
        "reversibility_plan": "delete_task (admin) before bids arrive; after "
                              "bids, the auction lifecycle governs.",
    },
    "delete_task": {
        "pre_conditions": [
            "admin_key_present",
            "task_has_no_bids",
            "allocation_released",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "no-bids check (refused when the task has bids/commits; "
            "a2a_inbound_market.py)",
            "sincor2.a2a_rate_limits.task_write",
        ],
        "approval": "auto",
        "approval_note": "Auto for the admin: refused when bids exist "
                         "(must use the auction lifecycle).",
        "rate_limits": {
            "policy_class": "task_write",
            "windows": "20/min + 300/hour",
        },
        "audit": [
            _DECISION_EVENT + "; task_id, released allocation, admin identity"
        ],
        "post_conditions": [
            "task removed; any pool allocation released first",
        ],
        "failure_handling": _FAIL_CLOSED + "; tasks with bids/commits are "
                                           "refused deletion",
        "reversibility": "reversible-with-cost",
        "reversibility_plan": "Task re-created (new id); cost is re-listing "
                              "and notifying watchers.",
    },
    "submit_proof": {
        "pre_conditions": [
            "agent_registered",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.a2a_rate_limits.task_write",
            "POLICY-MISSING: proof-validity policy (receipt_hash format, "
            "task-assignment binding) — currently inline",
        ],
        "approval": "auto",
        "approval_note": "Auto: submits a completion receipt_hash (HTTP "
                         "202); proof is evidence, not settlement.",
        "rate_limits": {
            "policy_class": "task_write",
            "windows": "20/min + 300/hour per agent|ip composite key",
        },
        "audit": [
            _DECISION_EVENT + "; task_id, agent_id, receipt_hash"
        ],
        "post_conditions": [
            "proof accepted (202) and linked to the task",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Superseding proof overwrites; history "
                              "retained.",
    },
    "issue_creator_token": {
        "pre_conditions": [
            "agent_registered",
            "p24_live_allowed",
            "content_screen_clean",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.defi.p24.policy.require_clean (name/symbol/description/"
            "bio screening)",
            "P24 live-block gate: live issuance refused while P24 is "
            "live-blocked (dry-run only by default)",
            "sincor2.a2a_rate_limits.issuance (5/hour + 20/day)",
        ],
        "approval": "auto",
        "approval_note": "Auto within the issuance envelope; live issuance "
                         "is blocked at the P24 gate until unblocked by "
                         "founder decision.",
        "rate_limits": {
            "policy_class": "issuance",
            "windows": "5/hour + 20/day per agent",
            "note": "strictest tier; registration tier backstops agent_id "
                    "rotation",
        },
        "audit": [
            _DECISION_EVENT + "; agent_id, token name/symbol, screen result, "
            "dry-run flag"
        ],
        "post_conditions": [
            "dry-run: issuance simulated, no token created",
            "live (when allowed): token issued, idempotent",
        ],
        "failure_handling": _FAIL_CLOSED + "; screen failure or live-block "
                                           "-> refuse issuance",
        "reversibility": "reversible-with-cost",
        "reversibility_plan": "Issued tokens governed by the P24 token "
                              "policy; cost is market/liquidity impact.",
    },
    "pool_release": {
        "pre_conditions": [
            "admin_key_present",
            "allocation_released",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "allocation-validity check (a2a_inbound_market.py)",
            "sincor2.a2a_rate_limits.bid",
        ],
        "approval": "auto",
        "approval_note": "Auto for the admin: returns an unspent allocation "
                         "to the pool.",
        "rate_limits": {
            "policy_class": "bid",
            "windows": "30/min + 300/hour",
        },
        "audit": [
            _DECISION_EVENT + "; task_id, amount, pool balance after, admin "
            "identity"
        ],
        "post_conditions": [
            "allocation returned to available pool balance",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Re-allocate via pool_allocate if needed.",
    },
    "register_agent_a2a": {
        "pre_conditions": [
            "eip191_proof_valid",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.a2a_identity.verify_wallet_proof (required for "
            "re-registration; first registration open)",
            "reserved agent_id check (a2a_inbound_ext.py)",
            "sincor2.a2a_rate_limits.register (5/hour + 20/day per IP)",
        ],
        "approval": "auto",
        "approval_note": "Auto: first registration open; re-registration "
                         "requires EIP-191 proof by the registered wallet.",
        "rate_limits": {
            "policy_class": "register",
            "windows": "5/hour + 20/day per IP",
            "note": "strictest policy in the set (Sybil surface)",
        },
        "audit": [
            _DECISION_EVENT + "; agent_id, wallet claim, identity=verified/"
            "unverified, proof digest"
        ],
        "post_conditions": [
            "agent in registry; re-registration bound to owner wallet",
        ],
        "failure_handling": _FAIL_CLOSED + "; bad re-registration proof -> "
                                           "deny (identity hijack blocked)",
        "reversibility": "reversible",
        "reversibility_plan": "Record updated via re-registration; removal "
                              "via admin path.",
    },
    "submit_task_sync": {
        "pre_conditions": [
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: marketplace task policy (settlement quoting, "
            "execution bounds) — AUTH GAP: no route-level auth visible",
            "sincor2.a2a_rate_limits.task_msg (30/min + 300/hour)",
        ],
        "approval": "auto",
        "approval_note": "Auto: synchronous task execution with settlement "
                         "quoting. AUTH GAP flagged — guardrail requires an "
                         "auth decision before broad exposure.",
        "rate_limits": {
            "policy_class": "task_msg",
            "windows": "30/min + 300/hour",
        },
        "audit": [
            _DECISION_EVENT + "; task params, quote, execution result"
        ],
        "post_conditions": [
            "task executed; settlement quote honored",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Execution result recorded; compensating task "
                              "issued on failure.",
    },
    "stake_sinc_reputation": {
        "pre_conditions": [
            "agent_registered",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: reputation-stake policy (min/max, lock terms, "
            "onchain finalisation binding) — AUTH GAP: no route-level auth "
            "visible; ledger-only boost",
        ],
        "approval": "auto",
        "approval_note": "Auto: ledger-only SINC stake boosting routing "
                         "priority; on-chain tx required separately to "
                         "finalise.",
        "rate_limits": {
            "policy_class": "PROPOSED: reputation_stake",
            "per_agent": "20/hour",
        },
        "audit": [
            _DECISION_EVENT + "; agent_id, amount, priority delta"
        ],
        "post_conditions": [
            "ledger stake recorded; routing priority updated",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "unstake_sinc_reputation removes the boost.",
    },
    "unstake_sinc_reputation": {
        "pre_conditions": [
            "agent_registered",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: reputation-unstake policy — AUTH GAP: no "
            "route-level auth visible",
        ],
        "approval": "auto",
        "approval_note": "Auto: removes the reputation-boost stake.",
        "rate_limits": {
            "policy_class": "PROPOSED: reputation_stake",
            "per_agent": "20/hour",
        },
        "audit": [
            _DECISION_EVENT + "; agent_id, amount released"
        ],
        "post_conditions": [
            "boost removed; routing priority recomputed",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Re-stake via stake_sinc_reputation.",
    },
    "register_agent_marketplace": {
        "pre_conditions": [
            "stake_sufficient",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "SINC credit gate (min staked 250, listing fee credits; "
            "blueprints/marketplace.py)",
            "sincor2.a2a_rate_limits.register",
        ],
        "approval": "auto",
        "approval_note": "Auto: older marketplace registration path; the "
                         "SINC credit gate is the policy.",
        "rate_limits": {
            "policy_class": "register",
            "windows": "5/hour + 20/day per IP",
        },
        "audit": [
            _DECISION_EVENT + "; agent_id, staked amount, fee credits"
        ],
        "post_conditions": [
            "agent listed; credit gate satisfied",
        ],
        "failure_handling": _FAIL_CLOSED + "; below-minimum stake -> deny",
        "reversibility": "reversible",
        "reversibility_plan": "Listing removed; stake subject to its own "
                              "unstake terms.",
    },
    "send_welcome_email": {
        "pre_conditions": [
            "rate_limit_ok",
            "content_screen_clean",
        ],
        "policy_checks": [
            "sincor2.compliance_guardrails.GuardrailsEngine.check_email_send "
            "(transactional template)",
        ],
        "approval": "auto",
        "approval_note": "Auto: transactional welcome email triggered by a "
                         "user's own signup.",
        "rate_limits": {
            "policy_class": "PROPOSED: email",
            "per_ip": "10/hour",
            "note": "tied to the signup rate",
        },
        "audit": [
            _DECISION_EVENT + "; recipient (redacted), template id, signup "
            "ref"
        ],
        "post_conditions": [
            "welcome email accepted by provider",
        ],
        "failure_handling": _FAIL_CLOSED + "; signup succeeds even if the "
                                           "email fails (email is best-"
                                           "effort, retried separately)",
        "reversibility": "irreversible",
        "irreversible": True,
        "reversibility_plan": "N/A — transactional email. Mitigation: "
                              "fixed template, no marketing content.",
    },
    "partner_status_update": {
        "pre_conditions": [
            "admin_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: partner-status policy (allowed status "
            "transitions) — currently inline in mvp_blueprints/launch.py",
        ],
        "approval": "auto",
        "approval_note": "Auto for the admin: marks partner outreach status "
                         "after contact.",
        "rate_limits": {
            "policy_class": "PROPOSED: admin_write",
            "per_admin": "100/hour",
        },
        "audit": [
            _DECISION_EVENT + "; partner_id, old/new status, admin identity"
        ],
        "post_conditions": [
            "status persisted",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Status re-set to the prior value with audit.",
    },
    "crm_record_contact": {
        "pre_conditions": [
            "operator_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.compliance_guardrails.GuardrailsEngine.check_pii_storage "
            "(local SQLite destination)",
        ],
        "approval": "auto",
        "approval_note": "Auto: records a CRM contact locally; PII stays "
                         "in the local store (references in logs, never "
                         "content).",
        "rate_limits": {
            "policy_class": "PROPOSED: crm",
            "per_operator": "200/hour",
        },
        "audit": [
            _DECISION_EVENT + "; contact ref (redacted), source"
        ],
        "post_conditions": [
            "contact persisted locally",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Contact record deleted/updated on request.",
    },
    "kya_list": {
        "pre_conditions": [
            "admin_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: KYA-listing policy — AUTH GAP: no route-level "
            "auth visible; lists (creates) a KYA record from an inbound "
            "payload. Must be admin-gated before exposure",
        ],
        "approval": "auto",
        "approval_note": "Auto once admin-gated: creates a KYA record. "
                         "Guardrail REQUIRES the admin gate first.",
        "rate_limits": {
            "policy_class": "PROPOSED: kya_admin",
            "per_admin": "100/hour",
        },
        "audit": [
            _DECISION_EVENT + "; payload ref, created record id, admin "
            "identity"
        ],
        "post_conditions": [
            "KYA record created/listed",
        ],
        "failure_handling": _FAIL_CLOSED + "; unauthenticated listing denied",
        "reversibility": "reversible",
        "reversibility_plan": "Record revoked via kya_revoke.",
    },
    "sadas_publish": {
        "pre_conditions": [
            "admin_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: attestation-content policy (what a SADAS "
            "attestation may assert) — admin-gated",
        ],
        "approval": "auto",
        "approval_note": "Auto for the admin: publishes a SADAS attestation.",
        "rate_limits": {
            "policy_class": "PROPOSED: kya_admin",
            "per_admin": "100/hour",
        },
        "audit": [
            _DECISION_EVENT + "; attestation id, subject, admin identity"
        ],
        "post_conditions": [
            "attestation published and retrievable",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Attestation superseded/revoked with audit.",
    },
    "quest_seed": {
        "pre_conditions": [
            "admin_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: quest-campaign policy (reward pool backing, "
            "campaign rules) — admin-seeded",
        ],
        "approval": "auto",
        "approval_note": "Auto for the admin: seeds a quest campaign in "
                         "the airdrop quest registry.",
        "rate_limits": {
            "policy_class": "PROPOSED: quest",
            "per_admin": "20/hour",
        },
        "audit": [
            _DECISION_EVENT + "; campaign id, reward pool, rules ref, admin "
            "identity"
        ],
        "post_conditions": [
            "campaign live in the registry",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Campaign closed to new claims; seeded rewards "
                              "returned to pool.",
    },
    "airdrop_register": {
        "pre_conditions": [
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: airdrop-registration policy (wallet binding, "
            "Sybil defenses: proof-of-personhood / stake / allowlist) — "
            "Sybil risk is the main concern",
            "sincor2.a2a_rate_limits.register (Sybil-surface tier)",
        ],
        "approval": "auto",
        "approval_note": "Auto: registers a wallet for the SIN airdrop; "
                         "Sybil defenses are the open policy item.",
        "rate_limits": {
            "policy_class": "register",
            "windows": "5/hour + 20/day per IP",
        },
        "audit": [
            _DECISION_EVENT + "; wallet (redacted), registration ref"
        ],
        "post_conditions": [
            "wallet registered once (deduped)",
        ],
        "failure_handling": _FAIL_CLOSED + "; duplicate or Sybil-flagged "
                                           "registrations denied/quarantined",
        "reversibility": "reversible",
        "reversibility_plan": "Registration removed before distribution; "
                              "post-distribution handled by clawback policy.",
    },
    "uw_register_agent": {
        "pre_conditions": [
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: underwriting-registration policy (ERC-8004 "
            "identity family checks) — AUTH GAP: no route-level auth visible",
            "sincor2.a2a_rate_limits.register",
        ],
        "approval": "auto",
        "approval_note": "Auto: underwriting-runtime agent registration. "
                         "AUTH GAP flagged.",
        "rate_limits": {
            "policy_class": "register",
            "windows": "5/hour + 20/day per IP",
        },
        "audit": [
            _DECISION_EVENT + "; agent_id, identity family refs"
        ],
        "post_conditions": [
            "agent registered in the underwriting runtime",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Revoked via uw_revoke_agent with human "
                              "approval.",
    },
    "grade_task": {
        "pre_conditions": [
            "admin_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: grading policy (rubric, grader authorization) "
            "— AUTH GAP: no route-level gate visible; feeds reputation "
            "scoring",
        ],
        "approval": "auto",
        "approval_note": "Auto once admin-gated: submits a quality grade "
                         "for a completed task. Guardrail REQUIRES the "
                         "admin gate first.",
        "rate_limits": {
            "policy_class": "PROPOSED: admin_write",
            "per_admin": "200/hour",
        },
        "audit": [
            _DECISION_EVENT + "; task_id, grade, rubric ref, grader identity"
        ],
        "post_conditions": [
            "grade recorded; reputation scoring ingests it",
        ],
        "failure_handling": _FAIL_CLOSED + "; unauthenticated grades denied",
        "reversibility": "reversible",
        "reversibility_plan": "Re-grade supersedes; history retained.",
    },
    "clear_polyclaw_dry_runs": {
        "pre_conditions": [
            "admin_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: kill-switch clear policy — AUTH GAP: no "
            "route-level auth visible; closes simulated open trades and "
            "releases stuck exposure. MUST be admin-gated before exposure "
            "(see RULES_AUDIT C1)",
        ],
        "approval": "auto",
        "approval_note": "Auto once admin-gated. Guardrail REQUIRES the "
                         "admin gate: an unauthenticated caller must never "
                         "re-arm trading.",
        "rate_limits": {
            "policy_class": "admin",
            "windows": "30/min + 200/hour",
        },
        "audit": [
            _DECISION_EVENT + "; cleared trades, released exposure, admin "
            "identity"
        ],
        "post_conditions": [
            "dry-run trades closed; exposure released",
        ],
        "failure_handling": _FAIL_CLOSED + "; unauthenticated calls denied; "
                                           "kill switch stays tripped",
        "reversibility": "reversible",
        "reversibility_plan": "Dry-run state is simulated; re-trip the kill "
                              "switch if exposure recurs.",
    },
    "user_signup": {
        "pre_conditions": [
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.compliance_guardrails.GuardrailsEngine.check_email_send "
            "(welcome email)",
            "sincor2.compliance_guardrails.GuardrailsEngine.check_pii_storage "
            "(lead persistence)",
            "sincor2.a2a_rate_limits.register (public-endpoint Sybil tier)",
        ],
        "approval": "auto",
        "approval_note": "Auto: public signup persists a lead, starts a "
                         "session, fires a welcome email.",
        "rate_limits": {
            "policy_class": "register",
            "windows": "5/hour + 20/day per IP",
        },
        "audit": [
            _DECISION_EVENT + "; lead ref (redacted), session id"
        ],
        "post_conditions": [
            "lead persisted; session started; welcome email queued",
        ],
        "failure_handling": _FAIL_CLOSED + "; email failure does not fail "
                                           "the signup (retried separately)",
        "reversibility": "reversible",
        "reversibility_plan": "profile_delete removes the lead data.",
    },
    "auth_login": {
        "pre_conditions": [
            "rate_limit_ok",
        ],
        "policy_checks": [
            "credential verification (mvp_blueprints/auth.py)",
            "POLICY-MISSING: login-attempt policy (lockout thresholds, "
            "breach-password screening) — partially inline",
        ],
        "approval": "auto",
        "approval_note": "Auto: issues a session/JWT on valid credentials.",
        "rate_limits": {
            "policy_class": "PROPOSED: login",
            "per_ip": "20/min",
            "note": "brute-force backstop; lockout policy is the open item",
        },
        "audit": [
            _DECISION_EVENT + "; user ref, success/failure, ip (rate-limit "
            "key only)"
        ],
        "post_conditions": [
            "session/JWT issued on valid credentials only",
        ],
        "failure_handling": _FAIL_CLOSED + "; bad credentials -> deny; "
                                           "lockout on repeated failure",
        "reversibility": "reversible",
        "reversibility_plan": "Session revoked; password reset flow.",
    },
    "onboarding_submit": {
        "pre_conditions": [
            "session_owner_verified",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "session ownership (mvp_blueprints/auth.py)",
            "sincor2.compliance_guardrails.GuardrailsEngine.check_pii_storage",
        ],
        "approval": "auto",
        "approval_note": "Auto: persists the session owner's onboarding "
                         "profile data.",
        "rate_limits": {
            "policy_class": "PROPOSED: account",
            "per_ip": "30/min",
        },
        "audit": [
            _DECISION_EVENT + "; user ref (redacted), fields persisted"
        ],
        "post_conditions": [
            "onboarding profile persisted",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Profile updated or deleted via profile_delete.",
    },
    "memory_ingest": {
        "pre_conditions": [
            "operator_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: memory-scope policy — AUTH GAP: should be "
            "scoped to the owning agent; writes to agent long-term memory",
            "sincor2.compliance_guardrails.GuardrailsEngine.check_pii_storage",
        ],
        "approval": "auto",
        "approval_note": "Auto once scoped: writes to the owning agent's "
                         "long-term memory. Guardrail REQUIRES "
                         "agent-scoping first.",
        "rate_limits": {
            "policy_class": "PROPOSED: memory",
            "per_agent": "100/hour",
        },
        "audit": [
            _DECISION_EVENT + "; agent_id, memory refs (never content)"
        ],
        "post_conditions": [
            "memory written to the owning agent's store",
        ],
        "failure_handling": _FAIL_CLOSED + "; cross-agent writes denied",
        "reversibility": "reversible",
        "reversibility_plan": "Memory entry deleted/expired per retention "
                              "policy.",
    },
    "memory_run": {
        "pre_conditions": [
            "operator_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: consolidation policy (what may be merged/"
            "forgotten) — AUTH GAP: no route-level gate visible",
        ],
        "approval": "auto",
        "approval_note": "Auto once gated: triggers a memory-consolidation "
                         "run.",
        "rate_limits": {
            "policy_class": "PROPOSED: memory",
            "per_agent": "10/hour",
        },
        "audit": [
            _DECISION_EVENT + "; agent_id, run id, merges/forgets summary"
        ],
        "post_conditions": [
            "consolidation completed; summary recorded",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Consolidation is append-with-tombstones; "
                              "prior state recoverable from the log.",
    },
    "registry_probe": {
        "pre_conditions": [
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: probe policy (target allowlist, probe "
            "frequency, SSRF guards on the callback URL) — outbound HTTP",
        ],
        "approval": "auto",
        "approval_note": "Auto: probes a candidate agent's callback URL "
                         "(outbound HTTP). SSRF guards required.",
        "rate_limits": {
            "policy_class": "PROPOSED: probe",
            "per_ip": "30/min",
        },
        "audit": [
            _DECISION_EVENT + "; target host (allowlisted), probe result"
        ],
        "post_conditions": [
            "probe result recorded (reachable/unreachable)",
        ],
        "failure_handling": _FAIL_CLOSED + "; non-allowlisted targets denied",
        "reversibility": "reversible",
        "reversibility_plan": "No state change; probe results expire.",
    },
    "content_generate": {
        "pre_conditions": [
            "admin_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.compliance_guardrails.GuardrailsEngine.check_content "
            "(drafts, not published)",
        ],
        "approval": "auto",
        "approval_note": "Auto for the admin: generates launch content "
                         "drafts (NOT published — publishing is the "
                         "separate human-gated launch_review_approve_post).",
        "rate_limits": {
            "policy_class": "PROPOSED: content",
            "per_admin": "50/hour",
        },
        "audit": [
            _DECISION_EVENT + "; draft id, content hash, admin identity"
        ],
        "post_conditions": [
            "draft stored; not published",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Draft discarded; never reached the public.",
    },
    "mcp_register_agent": {
        "pre_conditions": [
            "operator_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "MCP two-phase operator confirmation (mcp_server.py, "
            "default-deny)",
            "reputation starts at 0.0 (probation)",
        ],
        "approval": "auto",
        "approval_note": "Auto after operator confirmation: the two-phase "
                         "confirm IS the human approval instrument; "
                         "registration itself executes automatically.",
        "rate_limits": {
            "policy_class": "PROPOSED: mcp_write",
            "per_operator": "60/hour",
        },
        "audit": [
            _DECISION_EVENT + "; agent_id, confirmation record, probation "
            "flag"
        ],
        "post_conditions": [
            "agent in the marketplace directory at 0.0 reputation",
        ],
        "failure_handling": _FAIL_CLOSED + "; unconfirmed registrations "
                                           "never execute (default-deny)",
        "reversibility": "reversible",
        "reversibility_plan": "Directory entry removed; probation means no "
                              "routing impact yet.",
    },
    "kernel_claude_reason": {
        "pre_conditions": [
            "operator_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: LLM-spend policy (per-call budgets, allowed "
            "task types) — external API spend",
        ],
        "approval": "auto",
        "approval_note": "Auto: agency-kernel LLM reasoning calls "
                         "(analysis, validation, summarisation, "
                         "cross-reference).",
        "rate_limits": {
            "policy_class": "PROPOSED: kernel_llm",
            "per_operator": "200/hour",
            "note": "cost control; spend alerts on threshold",
        },
        "audit": [
            _DECISION_EVENT + "; task type, token usage, cost, operator "
            "identity"
        ],
        "post_conditions": [
            "reasoning result returned within budget",
        ],
        "failure_handling": _FAIL_CLOSED + "; budget exceeded -> deny",
        "reversibility": "reversible",
        "reversibility_plan": "No external state; results cached or "
                              "discarded.",
    },
    "defi_swarm_tick": {
        "pre_conditions": [
            "operator_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.defi.gates.next_stage (strategy lifecycle gates)",
            "scan/plan actions only — no execution (defi/engine.py)",
        ],
        "approval": "auto",
        "approval_note": "Auto: runs one strategy tick per protocol spec — "
                         "scan/plan only, never execution.",
        "rate_limits": {
            "policy_class": "PROPOSED: defi_tick",
            "per_operator": "120/hour",
        },
        "audit": [
            _DECISION_EVENT + "; protocol, tick id, plan output hash"
        ],
        "post_conditions": [
            "tick completed; plans recorded, nothing executed",
        ],
        "failure_handling": _FAIL_CLOSED + "; tick failure leaves prior "
                                           "plans intact",
        "reversibility": "reversible",
        "reversibility_plan": "Plans are ephemeral; superseded next tick.",
    },
    "defi_swarm_submit": {
        "pre_conditions": [
            "operator_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.defi.gates.next_stage",
            "POLICY-MISSING: plan-ranking policy (ranking weights, "
            "submission eligibility) — partially inline in defi/engine.py",
        ],
        "approval": "auto",
        "approval_note": "Auto: submits the swarm's ranked plan set "
                         "(plans, not executions).",
        "rate_limits": {
            "policy_class": "PROPOSED: defi_tick",
            "per_operator": "60/hour",
        },
        "audit": [
            _DECISION_EVENT + "; plan set hash, ranking, operator identity"
        ],
        "post_conditions": [
            "plan set submitted for review/execution gating",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Plan set superseded by the next submission.",
    },
    "x402_execute_paid_resource": {
        "pre_conditions": [
            "x402_access_token_valid",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "x402 access-token verification (scope, TTL; x402_payments.py)",
        ],
        "approval": "auto",
        "approval_note": "Auto: serves paid API payloads after a verified "
                         "payment (token mint was the human-gated step).",
        "rate_limits": {
            "policy_class": "PROPOSED: paid_api",
            "per_agent": "600/hour",
            "per_tenant": "6000/hour",
        },
        "audit": [
            _DECISION_EVENT + "; resource id, token scope, bytes served"
        ],
        "post_conditions": [
            "payload served within token scope/TTL",
        ],
        "failure_handling": _FAIL_CLOSED + "; expired/out-of-scope token -> "
                                           "402 re-challenge",
        "reversibility": "reversible",
        "reversibility_plan": "Read-only serve; token revocation stops "
                              "access.",
    },
    # ==================================================================
    # LOW — auto; audit log optional (still logged for agent-initiated)
    # ==================================================================
    "heartbeat": {
        "pre_conditions": [
            "eip191_proof_valid",
            "agent_registered",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.a2a_identity.verify_wallet_proof (heartbeat auth; "
            "HeartbeatAuthError -> 401)",
            "sincor2.a2a_rate_limits.heartbeat (20/min + 300/hour, composite "
            "agent|ip keying — a spoofer must not exhaust another agent's "
            "bucket)",
        ],
        "approval": "auto",
        "approval_note": "Auto: liveness heartbeat; freshness gates "
                         "commit_bid.",
        "rate_limits": {
            "policy_class": "heartbeat",
            "windows": "20/min + 300/hour per agent|ip composite key",
            "note": "20/min is ~10x headroom over the ~TTL/2 beat cadence",
        },
        "audit": [
            "decision_event optional for heartbeats (high volume); "
            "anomalies (missed beats, auth failures) logged via "
            "shadow_monitor/alerting"
        ],
        "post_conditions": [
            "heartbeat timestamp recorded; agent marked live",
        ],
        "failure_handling": _FAIL_CLOSED + "; bad proof -> 401, liveness "
                                           "unchanged",
        "reversibility": "reversible",
        "reversibility_plan": "Next heartbeat overwrites; missed beats age "
                              "out per TTL.",
    },
    "kya_heartbeat": {
        "pre_conditions": [
            "agent_registered",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.a2a_rate_limits.heartbeat",
        ],
        "approval": "auto",
        "approval_note": "Auto: liveness signal for a KYA identity.",
        "rate_limits": {
            "policy_class": "heartbeat",
            "windows": "20/min + 300/hour",
        },
        "audit": ["decision_event optional; anomalies logged"],
        "post_conditions": [
            "KYA liveness timestamp recorded",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Ages out per TTL.",
    },
    "kya_sla_report": {
        "pre_conditions": [
            "agent_registered",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: SLA-datapoint policy (schema, submission "
            "frequency caps) — currently inline",
        ],
        "approval": "auto",
        "approval_note": "Auto: posts an SLA datapoint for a KYA identity.",
        "rate_limits": {
            "policy_class": "PROPOSED: sla",
            "per_agent": "60/hour",
        },
        "audit": ["decision_event optional; datapoint stored"],
        "post_conditions": [
            "datapoint stored against the identity",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Datapoints are append-only evidence; "
                              "corrections appended.",
    },
    "sla_subscribe": {
        "pre_conditions": [
            "agent_registered",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: SLA-subscription policy — currently inline",
        ],
        "approval": "auto",
        "approval_note": "Auto: subscribes a KYA identity to SLA "
                         "monitoring.",
        "rate_limits": {
            "policy_class": "PROPOSED: sla",
            "per_agent": "20/hour",
        },
        "audit": ["decision_event optional"],
        "post_conditions": [
            "subscription recorded",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Unsubscribe.",
    },
    "sla_ping": {
        "pre_conditions": [
            "agent_registered",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.a2a_rate_limits.heartbeat",
        ],
        "approval": "auto",
        "approval_note": "Auto: SLA heartbeat datapoint.",
        "rate_limits": {
            "policy_class": "heartbeat",
            "windows": "20/min + 300/hour",
        },
        "audit": ["decision_event optional"],
        "post_conditions": [
            "ping recorded",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Ages out.",
    },
    "sadas_subscribe": {
        "pre_conditions": [
            "agent_registered",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: SADAS-subscription policy — currently inline",
        ],
        "approval": "auto",
        "approval_note": "Auto: subscribes a KYA identity to SADAS "
                         "attestations.",
        "rate_limits": {
            "policy_class": "PROPOSED: sla",
            "per_agent": "20/hour",
        },
        "audit": ["decision_event optional"],
        "post_conditions": [
            "subscription recorded",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Unsubscribe.",
    },
    "contract_net_auction": {
        "pre_conditions": [
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: demo-auction policy — demo Vickrey round over "
            "the demo roster only; writes engine history",
        ],
        "approval": "auto",
        "approval_note": "Auto: demo auction; no real funds or agents.",
        "rate_limits": {
            "policy_class": "PROPOSED: demo",
            "per_ip": "30/min",
        },
        "audit": ["decision_event optional"],
        "post_conditions": [
            "demo round recorded in engine history",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Demo history cleared; no external effect.",
    },
    "sinc_stake_calldata": {
        "pre_conditions": [
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: calldata-generation policy — read-only helper; "
            "returns calldata instructions for SINCPlatformAccess (Base "
            "8453); performs no chain tx",
        ],
        "approval": "auto",
        "approval_note": "Auto: read-only calldata helper.",
        "rate_limits": {
            "policy_class": "read",
            "windows": "120/min + 5000/hour",
        },
        "audit": ["decision_event optional (read-only)"],
        "post_conditions": [
            "calldata returned; no transaction broadcast",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "n/a",
        "reversibility_plan": "N/A — read-only, no state change.",
    },
    "sinc_unstake_calldata": {
        "pre_conditions": [
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: calldata-generation policy — read-only; 7-day "
            "cool-down terms surfaced in the instructions",
        ],
        "approval": "auto",
        "approval_note": "Auto: read-only calldata helper.",
        "rate_limits": {
            "policy_class": "read",
            "windows": "120/min + 5000/hour",
        },
        "audit": ["decision_event optional (read-only)"],
        "post_conditions": [
            "calldata returned; no transaction broadcast",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "n/a",
        "reversibility_plan": "N/A — read-only.",
    },
    "x402_create_challenge": {
        "pre_conditions": [
            "rate_limit_ok",
        ],
        "policy_checks": [
            "challenge-ledger policy (SQLite-backed; x402_payments.py)",
            "POLICY-MISSING: challenge policy (per-resource pricing, "
            "challenge TTL) — partially inline",
        ],
        "approval": "auto",
        "approval_note": "Auto: issues an x402 payment challenge (402) for "
                         "a paid resource.",
        "rate_limits": {
            "policy_class": "PROPOSED: challenge",
            "per_ip": "60/min",
        },
        "audit": ["decision_event optional; challenge id logged"],
        "post_conditions": [
            "challenge recorded in the ledger with TTL",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "Challenge expires unclaimed.",
    },
    "registry_validate": {
        "pre_conditions": [
            "rate_limit_ok",
        ],
        "policy_checks": [
            "agent-card payload schema validation (blueprints/registry.py)",
        ],
        "approval": "auto",
        "approval_note": "Auto: validates a payload; no state change.",
        "rate_limits": {
            "policy_class": "read",
            "windows": "120/min + 5000/hour",
        },
        "audit": ["decision_event optional (read-only)"],
        "post_conditions": [
            "validation verdict returned",
        ],
        "failure_handling": _FAIL_CLOSED + "; invalid payloads rejected",
        "reversibility": "n/a",
        "reversibility_plan": "N/A — no state change.",
    },
    "mcp_list_tasks": {
        "pre_conditions": [
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.a2a_rate_limits.read (120/min + 5000/hour)",
        ],
        "approval": "auto",
        "approval_note": "Auto: read-only MCP tool.",
        "rate_limits": {
            "policy_class": "read",
            "windows": "120/min + 5000/hour",
        },
        "audit": ["decision_event optional (read-only)"],
        "post_conditions": [
            "open auctions listed",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "n/a",
        "reversibility_plan": "N/A — read-only.",
    },
    "mcp_get_task": {
        "pre_conditions": [
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.a2a_rate_limits.read",
        ],
        "approval": "auto",
        "approval_note": "Auto: read-only MCP tool.",
        "rate_limits": {
            "policy_class": "read",
            "windows": "120/min + 5000/hour",
        },
        "audit": ["decision_event optional (read-only)"],
        "post_conditions": [
            "task detail returned",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "n/a",
        "reversibility_plan": "N/A — read-only.",
    },
    "mcp_get_agent_card": {
        "pre_conditions": [
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.a2a_rate_limits.read",
        ],
        "approval": "auto",
        "approval_note": "Auto: read-only MCP tool.",
        "rate_limits": {
            "policy_class": "read",
            "windows": "120/min + 5000/hour",
        },
        "audit": ["decision_event optional (read-only)"],
        "post_conditions": [
            "agent card returned",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "n/a",
        "reversibility_plan": "N/A — read-only.",
    },
    "mcp_get_quote": {
        "pre_conditions": [
            "rate_limit_ok",
        ],
        "policy_checks": [
            "sincor2.a2a_rate_limits.quote (60/min + 2000/hour per IP; "
            "unauthenticated price endpoint)",
        ],
        "approval": "auto",
        "approval_note": "Auto: read-only price quote in AXM.",
        "rate_limits": {
            "policy_class": "quote",
            "windows": "60/min + 2000/hour per IP",
        },
        "audit": ["decision_event optional (read-only)"],
        "post_conditions": [
            "quote returned; no commitment created",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "n/a",
        "reversibility_plan": "N/A — read-only.",
    },
    "kernel_web_search": {
        "pre_conditions": [
            "operator_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "POLICY-MISSING: search policy (allowed query classes, result "
            "handling) — outbound queries only; currently inline in "
            "agency_kernel_tools.py",
        ],
        "approval": "auto",
        "approval_note": "Auto: agency-kernel web search (outbound queries "
                         "only).",
        "rate_limits": {
            "policy_class": "PROPOSED: kernel_search",
            "per_operator": "300/hour",
        },
        "audit": ["decision_event optional; query logged"],
        "post_conditions": [
            "search results returned; no external posts",
        ],
        "failure_handling": _FAIL_CLOSED,
        "reversibility": "reversible",
        "reversibility_plan": "No external state; results discarded.",
    },
    "kernel_file_read": {
        "pre_conditions": [
            "operator_key_present",
            "rate_limit_ok",
        ],
        "policy_checks": [
            "path sanitisation + size caps (agency_kernel_tools.py)",
        ],
        "approval": "auto",
        "approval_note": "Auto: agency-kernel file read, path-sanitised "
                         "and size-capped.",
        "rate_limits": {
            "policy_class": "PROPOSED: kernel_read",
            "per_operator": "600/hour",
        },
        "audit": ["decision_event optional (read-only)"],
        "post_conditions": [
            "file content returned within caps",
        ],
        "failure_handling": _FAIL_CLOSED + "; path-traversal attempts denied",
        "reversibility": "n/a",
        "reversibility_plan": "N/A — read-only.",
    },
}


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------

REQUIRED_KEYS = (
    "pre_conditions",
    "policy_checks",
    "approval",
    "rate_limits",
    "audit",
    "post_conditions",
    "failure_handling",
    "reversibility",
)


def get_guardrail(action_name: str) -> Dict[str, object]:
    """Return the guardrail entry for ``action_name``.

    Raises :class:`KeyError` for unknown actions (fail closed: unknown
    actions have no guardrail and must not execute).
    """
    if action_name not in GUARDRAILS:
        raise KeyError(f"no guardrail defined for action {action_name!r}; "
                       "action must not execute")
    return GUARDRAILS[action_name]


def guardrails_by_risk(tier: str) -> Dict[str, Dict[str, object]]:
    """Return all guardrails for actions at one risk tier."""
    return {
        name: g
        for name, g in GUARDRAILS.items()
        if ACTIONS[name].get("risk_tier") == tier
    }


def summary() -> Dict[str, object]:
    """Counts and approval breakdown, for dashboards and reports."""
    from collections import Counter

    tiers = Counter()
    approvals: Dict[str, Counter] = {}
    policy_missing = 0
    for name, g in GUARDRAILS.items():
        tier = str(ACTIONS[name].get("risk_tier"))
        tiers[tier] += 1
        approvals.setdefault(tier, Counter())[str(g["approval"])] += 1
        if any(str(c).startswith("POLICY-MISSING") for c in g["policy_checks"]):  # type: ignore[union-attr]
            policy_missing += 1
    return {
        "total": len(GUARDRAILS),
        "by_tier": dict(tiers),
        "approval_by_tier": {t: dict(c) for t, c in approvals.items()},
        "policy_missing_count": policy_missing,
    }


def validate_coverage() -> None:
    """Assert full, schema-valid guardrail coverage over the action catalog.

    Raises on:
      * any ACTIONS key missing a guardrail (gap),
      * any guardrail keyed to an action not in ACTIONS (orphan),
      * any entry missing a required schema key,
      * invalid approval values,
      * critical actions without human/quorum approval,
      * high/critical actions without fail-closed failure handling,
      * high/critical actions without an audit log entry,
      * high/critical actions without a reversibility plan or explicit
        "irreversible": True marking.
    """
    action_names = set(ACTIONS)
    guardrail_names = set(GUARDRAILS)

    gaps = sorted(action_names - guardrail_names)
    if gaps:
        raise AssertionError(f"guardrail gaps (no guardrail defined): {gaps}")

    orphans = sorted(guardrail_names - action_names)
    if orphans:
        raise AssertionError(f"orphan guardrails (no such action): {orphans}")

    for name, g in GUARDRAILS.items():
        missing_keys = [k for k in REQUIRED_KEYS if k not in g]
        if missing_keys:
            raise AssertionError(f"{name}: missing schema keys {missing_keys}")
        if g["approval"] not in ("auto", "human", "quorum"):
            raise AssertionError(f"{name}: invalid approval {g['approval']!r}")

        tier = ACTIONS[name].get("risk_tier")
        if tier == "critical" and g["approval"] not in ("human", "quorum"):
            raise AssertionError(
                f"{name}: critical action must have human or quorum approval")
        if tier in ("high", "critical"):
            fh = str(g["failure_handling"])
            if not fh.startswith("fail_closed"):
                raise AssertionError(
                    f"{name}: {tier} action must be fail_closed")
            if not g["audit"]:
                raise AssertionError(
                    f"{name}: {tier} action must have an audit log entry")
            if g["reversibility"] == "irreversible":
                if not g.get("irreversible"):
                    raise AssertionError(
                        f"{name}: irreversible action must carry explicit "
                        "'irreversible': True marking")
            elif "reversibility_plan" not in g:
                raise AssertionError(
                    f"{name}: {tier} action needs a reversibility plan or "
                    "explicit 'irreversible': True")
