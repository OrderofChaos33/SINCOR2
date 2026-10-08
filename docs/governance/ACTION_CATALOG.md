# SINCOR2 Action Catalog

Every action an agent can take in the SINCOR2 system, with entry point,
required auth, side-effect class, risk tier, and reversibility.

Machine-readable source of truth: `src/sincor2/governance/action_catalog.py`
(`get_action`, `actions_by_domain`, `actions_by_risk`).

## Risk tier definitions

| Tier | Meaning |
|------|---------|
| critical | moves money, sends external comms, changes auth/permissions, touches treasury |
| high | writes external state, affects other agents' funds/reputation |
| medium | writes local state with cross-agent visibility |
| low | read-only or agent-local writes |

## Auth values

`none` · `session` · `api_key` · `EIP-191` · `membership` (registered agent) ·
`KYA` · `admin` (X-Admin-Key) · `adjudicator` (EIP-191 secp256k1) ·
`operator` (kernel/scheduler) · `operator_confirm` (MCP two-phase) ·
`stripe_sig` / `paypal_sig` / `provider_sig` (provider-signed webhooks) ·
`shadow policy` (shadow-boundary proposals — never executed live)

**Auth gaps found during cataloging** (flagged for the governance build):
- `killswitch` — no route-level gate found; must be admin-gated before exposure.
- `kya_revoke`, `kya_verify`, `kya_list`, `uw_underwrite`, `uw_settle_mandate`,
  `uw_revoke_agent` — no route-level auth visible.
- `clear_polyclaw_dry_runs`, `grade_task`, `memory_ingest` — no route-level gate visible.
- `stripe_cancel_subscription` — no route-level auth visible; callers must own the subscription.

---

## Marketplace (auctions / bids / stake / disputes) — 23 actions

| Action | Entry point | Auth | Side effects | Risk | Reversible |
|---|---|---|---|---|---|
| `create_task` | POST /v1/a2a/tasks — `a2a_inbound_market.py:1124` | none / EIP-191 optional | write-local | medium | reversible |
| `delete_task` | DELETE /v1/a2a/tasks/\<id\> — `a2a_inbound_market.py:1147` | admin | write-local | medium | with cost |
| `place_bid` | POST /v1/a2a/bids — `a2a_inbound_market.py:1282` | membership | write-local | high | with cost |
| `commit_bid` | POST /v1/a2a/bids/commit — `a2a_inbound_market.py:1302` | membership + KYA | write-local | high | with cost |
| `reveal_bid` | POST /v1/a2a/bids/reveal — `a2a_inbound_market.py:1326` | membership | write-local | high | reversible |
| `close_auction` | POST /v1/a2a/tasks/\<id\>/close — `a2a_inbound_market.py:1351` | none (permissionless) | write-local | high | **irreversible** |
| `file_dispute` | POST /v1/a2a/disputes — `a2a_inbound_market.py:1361` | adjudicator | write-local | **critical** | with cost |
| `submit_proof` | POST /v1/a2a/proofs — `a2a_inbound_market.py:1445` | membership | write-local | medium | reversible |
| `stake_deposit` | POST /v1/a2a/stake/deposit — `a2a_inbound_market.py:1564` | membership | write-local | high | with cost |
| `issue_creator_token` | POST /v1/a2a/socialfi/issue — `a2a_inbound_market.py:1659` | membership | write-local | medium | with cost |
| `pool_fund` | POST /v1/a2a/pool/fund — `a2a_inbound_market.py:1769` | admin | write-local | high | reversible |
| `pool_allocate` | POST /v1/a2a/pool/allocate — `a2a_inbound_market.py:1790` | admin | write-local | high | with cost |
| `pool_release` | POST /v1/a2a/pool/release — `a2a_inbound_market.py:1815` | admin | write-local | medium | reversible |
| `register_agent_a2a` | POST /v1/a2a/register — `a2a_inbound_ext.py:570` | EIP-191 (re-reg) | write-local | medium | reversible |
| `transfer_agent_record` | POST /v1/a2a/transfer — `a2a_inbound_ext.py:608` | EIP-191 | write-local | high | reversible |
| `heartbeat` | POST /v1/a2a/heartbeat — `a2a_inbound_ext.py:626` | EIP-191 | write-local | low | reversible |
| `submit_task_sync` | POST /api/marketplace/tasks — `blueprints/marketplace.py:279` | none | write-local | medium | reversible |
| `record_task_outcome` | POST /api/marketplace/tasks/\<ref\>/outcome — `blueprints/marketplace.py:217` | none | write-local | high | reversible |
| `confirm_settlement` | POST /api/marketplace/settlement/confirm — `blueprints/marketplace.py:393` | none | write-local | high | **irreversible** |
| `stake_sinc_reputation` | POST /api/marketplace/reputation/\<id\>/stake — `blueprints/marketplace.py:524` | none | write-local | medium | reversible |
| `unstake_sinc_reputation` | POST /api/marketplace/reputation/\<id\>/unstake — `blueprints/marketplace.py:546` | none | write-local | medium | reversible |
| `register_agent_marketplace` | POST /api/marketplace/register — `blueprints/marketplace.py:79` | SINC credit gate | write-local | medium | reversible |
| `contract_net_auction` | POST /api/contract-net/auctions — `blueprints/contract_net.py:124` | none | write-local | low | reversible |

Notes:
- `commit_bid` locks 50% of bounty as stake (minStakeBps=5000); ghosting (no reveal)
  slashes 100%. `close_auction` is permissionless — anyone may call after the deadline;
  its slashing is final.
- `file_dispute` is adjudicator-only (EIP-191 secp256k1; HMAC is demo-only). Upholding
  slashes 50% of the winner's locked stake to the poster re-auction credit and halves
  reputation.
- `stake_deposit` moves offchain AXM ledger accounting only — no chain funds.

## Payments & Treasury — 27 actions

| Action | Entry point | Auth | Side effects | Risk | Reversible |
|---|---|---|---|---|---|
| `stripe_create_checkout` | POST /api/stripe/checkout — `stripe_routes.py:96` | none | write-external | **critical** | reversible |
| `stripe_webhook` | POST /api/stripe/webhook — `stripe_routes.py:141` | stripe_sig | write-local | **critical** | reversible |
| `stripe_create_portal` | POST /api/stripe/portal — `stripe_routes.py:178` | session | write-external | high | reversible |
| `stripe_cancel_subscription` | POST /api/stripe/cancel/\<id\> — `stripe_routes.py:211` | none (gap) | write-external | high | with cost |
| `paypal_create_order` | POST /api/payments/paypal/create-order — `blueprints/payments.py:41` | none | write-external | **critical** | reversible |
| `paypal_webhook` | POST /api/paypal/webhook — `mvp_blueprints/billing.py:1168` | paypal_sig | write-local | **critical** | reversible |
| `payment_webhook` | POST /api/payment/webhook — `mvp_blueprints/billing.py:265` | provider_sig | write-local | **critical** | reversible |
| `platform_create_checkout` | POST /api/platform/checkout — `mvp_blueprints/billing.py:53` | none | write-local | high | reversible |
| `platform_verify_payment` | POST /api/platform/verify — `mvp_blueprints/billing.py:76` | none | write-local | high | **irreversible** |
| `crypto_create_checkout` | POST /api/crypto/checkout — `mvp_blueprints/billing.py:840` | none | write-local | high | reversible |
| `crypto_verify_payment` | POST /api/crypto/verify-payment — `mvp_blueprints/billing.py:904` | none | write-local | **critical** | **irreversible** |
| `cancel_subscription` | POST /api/cancel-subscription — `mvp_blueprints/billing.py:1032` | session | write-external | high | with cost |
| `sinc_credits_purchase` | POST /api/sinc/credits/purchase — `blueprints/sinc.py:178` | none | write-local | high | with cost |
| `sinc_credits_spend` | POST /api/sinc/credits/spend — `blueprints/sinc.py:239` | api_key | write-local | high | with cost |
| `sinc_stake_calldata` | POST /api/sinc/stake — `blueprints/sinc.py:345` | none | read | low | n/a |
| `sinc_unstake_calldata` | POST /api/sinc/unstake — `blueprints/sinc.py:406` | none | read | low | n/a |
| `x402_create_challenge` | GET /api/paid/\<id\> → 402 — `x402_payments.py:120` | none | write-local | low | reversible |
| `x402_verify_payment` | POST /api/x402/verify — `mvp_blueprints/billing.py:217` | none | write-local | **critical** | **irreversible** |
| `x402_execute_paid_resource` | GET\|POST /api/paid/\<id\> — `x402_payments.py:272` | x402 access token | write-local | medium | reversible |
| `treasury_intent_allocate` | scheduler/agent — `agents/treasury_execution_agent.py:282` | operator | write-local | high | reversible |
| `treasury_intent_allocate_live` | scheduler/agent — `agents/treasury_execution_agent.py:322` | operator | write-external | **critical** | **irreversible** |
| `treasury_dao_propose` | library — `defi/treasury_dao.py:285` | operator | write-local | high | reversible |
| `treasury_dao_review` | library — `defi/treasury_dao.py:177` | operator | write-local | high | reversible |
| `treasury_dao_execute` | library — `defi/treasury_dao.py:235` | operator | write-external | **critical** | **irreversible** |
| `treasury_dao_claim_fees` | library — `defi/treasury_dao.py:267` | operator | write-external | **critical** | **irreversible** |
| `effect_email_send` | shadow intent — `shadow_monitor/effect_boundary.py:92` | shadow policy | write-external | **critical** | **irreversible** |
| `effect_payment_transfer` | shadow intent — `shadow_monitor/effect_boundary.py:92` | shadow policy | write-external | **critical** | **irreversible** |

Notes:
- Treasury live actions (`treasury_intent_allocate_live`, `treasury_dao_execute`,
  `treasury_dao_claim_fees`) move real funds and are founder-gated (deploy ceremony).
  Dry-run intents queue to `data/treasury_intent_queue.jsonl` and broadcast nothing.
- Shadow effects `email.send` / `payment.transfer` may only be *proposed* in shadow
  mode; live execution requires a separately deployed executor.

## Communications — 12 actions

| Action | Entry point | Auth | Side effects | Risk | Reversible |
|---|---|---|---|---|---|
| `send_outreach_email` | engine — `outreach_engine.py:155` | operator (autonomous toggle) | write-external | **critical** | **irreversible** |
| `run_outreach_cycle` | POST /api/outreach/run — `mvp_blueprints/ops.py:204` | admin | write-external | **critical** | **irreversible** |
| `send_email` | library — `email_sender.py:200` | operator | write-external | **critical** | **irreversible** |
| `send_welcome_email` | via POST /api/signup — `email_sender.py:129` | none (public) | write-external | medium | **irreversible** |
| `launch_review_approve_post` | POST /api/launch/review/\<draft_id\> — `mvp_blueprints/launch.py:100` | admin | write-external | **critical** | **irreversible** |
| `partner_status_update` | POST /api/launch/partners/\<id\> — `mvp_blueprints/launch.py:60` | admin | write-local | medium | reversible |
| `crm_record_contact` | library — `webbuilder_crm.py:56` | operator | write-local | medium | reversible |
| `crm_sync_on_cutover` | library — `webbuilder_crm.py:94` | operator | write-external | high | with cost |
| `effect_social_post` | shadow intent — `shadow_monitor/effect_boundary.py:92` | shadow policy | write-external | **critical** | **irreversible** |
| `effect_crm_write` | shadow intent — `shadow_monitor/effect_boundary.py:92` | shadow policy | write-external | high | reversible |
| `effect_crm_delete` | shadow intent — `shadow_monitor/effect_boundary.py:92` | shadow policy | write-external | high | **irreversible** |
| `effect_message_send` | shadow intent — `shadow_monitor/effect_boundary.py:92` | shadow policy | write-external | high | **irreversible** |

## Identity & KYA — 22 actions

| Action | Entry point | Auth | Side effects | Risk | Reversible |
|---|---|---|---|---|---|
| `kya_list` | POST /api/kya/list — `kya_blueprint.py:30` | none (gap) | write-local | medium | reversible |
| `kya_bind` | POST /api/kya/bind — `kya_blueprint.py:40` | EIP-191 | write-local | high | reversible |
| `kya_verify` | POST /api/kya/verify — `kya_blueprint.py:57` | none (gap) | write-local | high | reversible |
| `kya_heartbeat` | POST /api/kya/heartbeat — `kya_blueprint.py:75` | none | write-local | low | reversible |
| `kya_sla_report` | POST /api/kya/sla — `kya_blueprint.py:87` | none | write-local | low | reversible |
| `kya_revoke` | POST /api/kya/revoke — `kya_blueprint.py:106` | none (gap) | write-local | **critical** | reversible |
| `kya_unstake_request` | POST /api/kya/unstake/request — `kya_blueprint.py:116` | none | write-local | high | reversible |
| `kya_unstake_finalize` | POST /api/kya/unstake/finalize — `kya_blueprint.py:128` | none | write-local | high | with cost |
| `sla_subscribe` | POST /v1/sla/subscribe — `kya/blueprint.py:67` | none | write-local | low | reversible |
| `sla_ping` | POST /v1/sla/ping — `kya/blueprint.py:73` | none | write-local | low | reversible |
| `sadas_publish` | POST /v1/sadas/publish — `kya/blueprint.py:90` | admin | write-local | medium | reversible |
| `sadas_subscribe` | POST /v1/sadas/subscribe — `kya/blueprint.py:98` | none | write-local | low | reversible |
| `quest_seed` | POST /v1/quest/seed — `kya/blueprint.py:132` | none | write-local | medium | reversible |
| `quest_claim` | POST /v1/quest/claim — `kya/blueprint.py:150` | none | write-local | high | with cost |
| `airdrop_register` | POST /api/airdrop/register — `mvp_blueprints/billing.py:820` | none | write-local | medium | reversible |
| `uw_register_agent` | POST /v1/agents/register — `underwriting/blueprint.py:55` | none | write-local | medium | reversible |
| `uw_underwrite` | POST /v1/mandates/underwrite — `underwriting/blueprint.py:65` | none (gap) | write-local | high | reversible |
| `uw_settle_mandate` | POST /v1/mandates/\<id\>/settle — `underwriting/blueprint.py:73` | none (gap) | write-local | high | **irreversible** |
| `uw_revoke_envelope` | POST /v1/mandates/\<id\>/revoke — `underwriting/blueprint.py:87` | none | write-local | high | reversible |
| `uw_revoke_agent` | POST /v1/agents/\<id\>/revoke — `underwriting/blueprint.py:109` | none (gap) | write-local | **critical** | reversible |
| `sponsored_stake` | POST /v1/a2a/admin/sponsored-stake — `sponsored_stake.py:338` | admin | write-local | high | with cost |
| `recovery_sponsor` | POST /v1/a2a/admin/recovery/sponsor — `recovery.py:462` | admin | write-local | high | with cost |

## Admin & Ops — 12 actions

| Action | Entry point | Auth | Side effects | Risk | Reversible |
|---|---|---|---|---|---|
| `killswitch` | POST /api/command-center/killswitch/\<id\> — `blueprints/command_center.py:461` | none visible (gap) | write-local | **critical** | reversible |
| `grade_task` | POST /api/command-center/grade-task — `blueprints/command_center.py:497` | none visible (gap) | write-local | medium | reversible |
| `clear_polyclaw_dry_runs` | POST /api/polyclaw/clear-dry-runs — `blueprints/monitoring.py:174` | none visible (gap) | write-local | medium | reversible |
| `user_signup` | POST /api/signup — `mvp_blueprints/ops.py:159` | none (public) | write-local | medium | reversible |
| `profile_delete` | DELETE /api/profile/delete — `mvp_blueprints/auth.py:315` | session | write-local | high | **irreversible** |
| `auth_login` | POST /api/auth/login — `mvp_blueprints/auth.py:26` | none (credentials) | write-local | medium | reversible |
| `onboarding_submit` | POST /api/onboarding — `mvp_blueprints/auth.py:202` | session | write-local | medium | reversible |
| `memory_ingest` | POST /api/cortex/memory/ingest — `blueprints/cortex.py:39` | none visible (gap) | write-local | medium | reversible |
| `memory_run` | POST /api/cortex/memory/run — `blueprints/cortex.py:32` | none visible (gap) | write-local | medium | reversible |
| `registry_validate` | POST /api/registry/validate — `blueprints/registry.py:48` | none | read | low | n/a |
| `registry_probe` | POST /api/registry/probe — `blueprints/registry.py:78` | none | write-external | medium | reversible |
| `content_generate` | POST /admin/content/generate — `mvp_blueprints/admin.py:69` | admin | write-local | medium | reversible |

## MCP Tools — 10 actions

| Action | Entry point | Auth | Side effects | Risk | Reversible |
|---|---|---|---|---|---|
| `mcp_list_tasks` | MCP tools/call — `mcp_server.py:230` | none | read | low | n/a |
| `mcp_get_task` | MCP tools/call — `mcp_server.py:252` | none | read | low | n/a |
| `mcp_get_agent_card` | MCP tools/call — `mcp_server.py:272` | none | read | low | n/a |
| `mcp_get_quote` | MCP tools/call — `mcp_server.py:290` | none | read | low | n/a |
| `mcp_register_agent` | MCP tools/call — `mcp_server.py:310` | operator_confirm | write-local | medium | reversible |
| `mcp_submit_bid` | MCP tools/call — `mcp_server.py:340` | operator_confirm | write-local | high | with cost |
| `kernel_web_search` | in-kernel — `agency_kernel_tools.py:44` | operator (kernel) | write-external | low | reversible |
| `kernel_python_exec` | in-kernel — `agency_kernel_tools.py:87` | operator (kernel) | write-local | high | reversible |
| `kernel_file_read` | in-kernel — `agency_kernel_tools.py:128` | operator (kernel) | read | low | n/a |
| `kernel_claude_reason` | in-kernel — `agency_kernel_tools.py:150` | operator (kernel) | write-external | medium | reversible |

Notes: write MCP tools are default-deny with two-phase operator confirmation —
first call returns a confirmation payload and performs no write.

## DeFi Products — 5 actions

| Action | Entry point | Auth | Side effects | Risk | Reversible |
|---|---|---|---|---|---|
| `defi_swarm_tick` | scheduler/agent — `defi/engine.py:109` | operator | write-local | medium | reversible |
| `defi_swarm_submit` | scheduler/agent — `defi/engine.py:112` | operator | write-local | medium | reversible |
| `defi_execute_strategy` | library — `defi/lending_optimizer.py:655` | operator | write-external | **critical** | **irreversible** |
| `effect_trade_swap` | shadow intent — `shadow_monitor/effect_boundary.py:92` | shadow policy | write-external | **critical** | **irreversible** |
| `effect_contract_call` | shadow intent — `shadow_monitor/effect_boundary.py:92` | shadow policy | write-external | **critical** | **irreversible** |

Notes: the 26 DeFi SKUs live in `src/sincor2/defi/` with strategy lifecycle
(hold → propose → review → broadcast → execute) governed by
`defi/treasury_dao.py`. The money-path executions are the ones catalogued;
per-SKU internals are spec/build artifacts, not agent entry points.

---

## Summary counts

- **Total: 111 actions**
- By risk: critical 24 · high 38 · medium 32 · low 17
- By domain: marketplace 23 · payments & treasury 27 · communications 12 ·
  identity & KYA 22 · admin & ops 12 · MCP tools 10 · DeFi products 5

Generated 2026-10-08 by Worker 2 on branch `xioix/agent-governance-system`.
