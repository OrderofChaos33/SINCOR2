# SINCOR2 Documentation Hub

Architecture, protocol, operations, and contributor guides for SINCOR2.
Every doc in this tree is reachable from this index.

## Start here if you landed from X

- [`LOOP_SETTLEMENT.md`](./LOOP_SETTLEMENT.md) — Base Sepolia closed-loop tape: CFP, sealed bid, USDC escrow, ERC-7579 session key payout, timeout refund.

## Sections

- [`architecture/`](./architecture/) — system architecture views, boundaries, and scaling models. Index: [`architecture/README.md`](./architecture/README.md).
- [`api/`](./api/) — protocol and interface documentation.
- [`deployment/`](./deployment/) — deploy runbooks and production ops. Index: [`deployment/README.md`](./deployment/README.md).
- [`launch/`](./launch/) — launch and conversion assets. Index: [`launch/README.md`](./launch/README.md).
- [`security/`](./security/) — security audits and reports. Index: [`security/README.md`](./security/README.md).
- [`specs/`](./specs/) — protocol and SKU specs. Index: [`specs/README.md`](./specs/README.md).
- [`token/`](./token/) — SINC and AXIOM token overview.
- [`transition/`](./transition/) — transition strategy, gap analysis, and execution planning.
- [`guides/`](./guides/) — contributor and operator guides.
- [`funding/`](./funding/) — funding operations tracker, cycle logs, and submission artifacts.
- [`superpowers/`](./superpowers/) — existing launch runbooks/plans/specs.
- [`a2a/`](./a2a/), [`ops/`](./ops/), [`sinax/`](./sinax/), [`underwriting/`](./underwriting/) — working areas; see each dir.

## Recommended reading order

1. [`LOOP_SETTLEMENT.md`](./LOOP_SETTLEMENT.md)
2. [`transition/how-we-scale.md`](./transition/how-we-scale.md)
3. [`transition/gap-assessment.md`](./transition/gap-assessment.md)
4. [`architecture/overview.md`](./architecture/overview.md)
5. [`architecture/CANONICAL_PATHS.md`](./architecture/CANONICAL_PATHS.md)
6. [`../README.md`](../README.md)

## Canonical references

Single sources of truth. If anything disagrees with these, these win until amended.

| Doc | Authority |
|---|---|
| [`architecture/AUCTION_GROUND_TRUTH.md`](./architecture/AUCTION_GROUND_TRUTH.md) | Sealed-bid auction spec, verified against code. |
| [`architecture/CANONICAL_PATHS.md`](./architecture/CANONICAL_PATHS.md) | The one canonical path per money movement; grandfathered/dormant/quarantined paths labeled. |
| [`architecture/MONEY_FLOW.md`](./architecture/MONEY_FLOW.md) | Money flow: task → auction → settlement → fee → treasury. |
| [`architecture/registry.md`](./architecture/registry.md) | A2A schema gate + canonical on-chain addresses. |
| [`TRUST_STACK.md`](./TRUST_STACK.md) | Trust stack — one canonical surface. |
| [`CANON_POINTER.md`](./CANON_POINTER.md) | Canon pointer. |
| [`RUNTIME_STATE.md`](./RUNTIME_STATE.md) | Runtime state — honesty sheet. |
| [`runtime-and-configuration.md`](./runtime-and-configuration.md) | Runtime and configuration. |

## A2A protocol

| Doc | What it is |
|---|---|
| [`A2A_EXTERNAL_WIRE.md`](./A2A_EXTERNAL_WIRE.md) | A2A external wire — friction log (2026-09-10). |
| [`A2A_PRODUCTION_CHECKLIST.md`](./A2A_PRODUCTION_CHECKLIST.md) | A2A production checklist. |
| [`AGENT_PASSPORT_SPEC.md`](./AGENT_PASSPORT_SPEC.md) | Agent Passport — DAE interop primitive. |
| [`KYA_SPEC.md`](./KYA_SPEC.md) | SINCOR KYA v0. |
| [`KYA.md`](./KYA.md) | KYA stack — ship notes. |
| [`ERC8004_REGISTER.md`](./ERC8004_REGISTER.md) | ERC-8004 registration — operator steps. |
| [`METAMASK_AGENT_WALLET.md`](./METAMASK_AGENT_WALLET.md) | Agent wallet (MetaMask) notes. |
| [`ASYNC_TASK_QUEUE.md`](./ASYNC_TASK_QUEUE.md) | Async task queue + real A2A streaming. |

## Auction, settlement, treasury

| Doc | What it is |
|---|---|
| [`SETTLEMENT_PROOF.md`](./SETTLEMENT_PROOF.md) | Settlement proofs. |
| [`LIQUIDITY_AGENTS.md`](./LIQUIDITY_AGENTS.md) | Liquidity agents system. |
| [`TREASURY_HOLD.md`](./TREASURY_HOLD.md) | Treasury HOLD — lifted 2026-09-10 20:54 CDT. |
| [`CEO_TREASURY_EXEC_AGENT_2026-08-19.md`](./CEO_TREASURY_EXEC_AGENT_2026-08-19.md) | Treasury execution agent notes. |
| [`AXM_BASESCAN_VERIFICATION.md`](./AXM_BASESCAN_VERIFICATION.md) | AXM Basescan verification runbook. |
| [`SINC_INTEGRATION.md`](./SINC_INTEGRATION.md) | SINC token integration. |
| [`TOKEN_ADOPTION_PLAN.md`](./TOKEN_ADOPTION_PLAN.md) | Token adoption & liquidity plan. |

## DeFi product arm

| Doc | What it is |
|---|---|
| [`DEFI_PRODUCT_ARM.md`](./DEFI_PRODUCT_ARM.md) | Speculative DeFi product arm charter. |
| [`DEFI_16_DEEP_SPECS.md`](./DEFI_16_DEEP_SPECS.md) | Deep specs for the 16 expansion DeFi builds. |
| [`DEFI_26_PROTOCOL_BUILD_STATUS.md`](./DEFI_26_PROTOCOL_BUILD_STATUS.md) | 26-protocol build status (2026-09-12). |
| [`DEFI_PROJECTS_COORDINATION.md`](./DEFI_PROJECTS_COORDINATION.md) | DeFi projects coordination hub. |
| [`DEFI_SWARM_EXPANSION_PLAN.md`](./DEFI_SWARM_EXPANSION_PLAN.md) | DeFi swarm expansion plan. |

## Agent operations

| Doc | What it is |
|---|---|
| [`AGENT_OPS.md`](./AGENT_OPS.md) | Agent ops manual — 24/7 swarm operation. |
| [`AGENT_ADDONS.md`](./AGENT_ADDONS.md) | Agent add-ons implementation spec. |
| [`ACTIONS.md`](./ACTIONS.md) | GitHub Actions — paused until billing is restored. |
| [`CHROMA_E2E_STATUS.md`](./CHROMA_E2E_STATUS.md) | Chroma E2E status. |
| [`CODE_BUILDER_HANDOFF_2026-09-19.md`](./CODE_BUILDER_HANDOFF_2026-09-19.md) | Code builder handoff (2026-09-19 EOD). |
| [`UNDERWRITING.md`](./UNDERWRITING.md) | Agent underwriting runtime. |

## Revenue & growth

| Doc | What it is |
|---|---|
| [`AUTONOMOUS_REVENUE_ENGINE.md`](./AUTONOMOUS_REVENUE_ENGINE.md) | Autonomous revenue engine. |
| [`AGENT_PROFIT_LOOPS.md`](./AGENT_PROFIT_LOOPS.md) | Agent profit loops — executive summary. |
| [`POLYCLAW_SELF_PERPETUATING_EARNING_MACHINE.md`](./POLYCLAW_SELF_PERPETUATING_EARNING_MACHINE.md) | Polyclaw self-perpetuating earning machine. |
| [`SALES_OUTREACH_SWARM.md`](./SALES_OUTREACH_SWARM.md) | Sales outreach swarm — autonomous (agents only). |
| [`MACHINE_BUREAUCRACY_2026-09-26.md`](./MACHINE_BUREAUCRACY_2026-09-26.md) | Machine bureaucracy (Form 01). |
| [`AXIOM_2_LAUNCH_CHECKLIST.md`](./AXIOM_2_LAUNCH_CHECKLIST.md) | Axiom 2.0 launch checklist. |
| [`WEBSITE_PRICING_COPY.md`](./WEBSITE_PRICING_COPY.md) | Website pricing copy (ready to use). |
| [`CONTENT_ORCHESTRATION_PLAN.md`](./CONTENT_ORCHESTRATION_PLAN.md) | Content/copy orchestration — full production plan. |
| [`CONTENT_STANDARDS.md`](./CONTENT_STANDARDS.md) | Content & copy quality standards. |
| [`VERTICAL_CONTENT_CATALOGS.md`](./VERTICAL_CONTENT_CATALOGS.md) | Vertical content catalogs. |
| [`WIRING_CONTENT_AGENTS.md`](./WIRING_CONTENT_AGENTS.md) | Wiring plan: content orchestration agents. |

## Secrets & safety

| Doc | What it is |
|---|---|
| [`SECRETS_HYGIENE.md`](./SECRETS_HYGIENE.md) | Secrets hygiene (2026-09-10). |
| [`SALT_AND_SECRETS.md`](./SALT_AND_SECRETS.md) | Salt files and environment debris. |
| [`REVERTING.md`](./REVERTING.md) | Emergency revert procedure. |

## CEO briefs & execution logs (2026-08 → 2026-09)

Daily operating history. Newest last.

| Doc | Doc |
|---|---|
| [`CEO_EXECUTION_2026-08-04.md`](./CEO_EXECUTION_2026-08-04.md) | [`CEO_EXECUTION_2026-08-05.md`](./CEO_EXECUTION_2026-08-05.md) |
| [`CEO_ORCHESTRATION_2026-08-12.md`](./CEO_ORCHESTRATION_2026-08-12.md) | [`CEO_DAILY_BRIEF_2026-08-14.md`](./CEO_DAILY_BRIEF_2026-08-14.md) |
| [`CEO_DAILY_BRIEF_2026-08-15.md`](./CEO_DAILY_BRIEF_2026-08-15.md) | [`CEO_TOKEN_PIVOT_AXIOM_2026-08-16.md`](./CEO_TOKEN_PIVOT_AXIOM_2026-08-16.md) |
| [`CEO_DAILY_BRIEF_2026-08-16.md`](./CEO_DAILY_BRIEF_2026-08-16.md) | [`CEO_DAILY_BRIEF_2026-08-17.md`](./CEO_DAILY_BRIEF_2026-08-17.md) |
| [`CEO_DAILY_BRIEF_2026-08-19.md`](./CEO_DAILY_BRIEF_2026-08-19.md) | [`CEO_DAILY_BRIEF_2026-08-20.md`](./CEO_DAILY_BRIEF_2026-08-20.md) |
| [`CEO_DAILY_BRIEF_2026-08-21.md`](./CEO_DAILY_BRIEF_2026-08-21.md) | [`CEO_DAILY_BRIEF_2026-08-22.md`](./CEO_DAILY_BRIEF_2026-08-22.md) |
| [`CEO_DAILY_BRIEF_2026-08-23.md`](./CEO_DAILY_BRIEF_2026-08-23.md) | [`CEO_DAILY_BRIEF_2026-08-24.md`](./CEO_DAILY_BRIEF_2026-08-24.md) |
| [`CEO_5DAY_CASH_PLAN_2026-08-25.md`](./CEO_5DAY_CASH_PLAN_2026-08-25.md) | [`CEO_DAILY_BRIEF_2026-08-25.md`](./CEO_DAILY_BRIEF_2026-08-25.md) |
| [`CEO_DAILY_BRIEF_2026-08-26.md`](./CEO_DAILY_BRIEF_2026-08-26.md) | [`CEO_DAILY_BRIEF_2026-08-27.md`](./CEO_DAILY_BRIEF_2026-08-27.md) |
| [`CEO_DAILY_BRIEF_2026-08-28.md`](./CEO_DAILY_BRIEF_2026-08-28.md) | [`CEO_DAILY_BRIEF_2026-08-29.md`](./CEO_DAILY_BRIEF_2026-08-29.md) |
| [`CEO_DAILY_BRIEF_2026-08-31.md`](./CEO_DAILY_BRIEF_2026-08-31.md) | [`CEO_CONVERSION_DEPLOY_2026-09-01.md`](./CEO_CONVERSION_DEPLOY_2026-09-01.md) |
| [`CEO_CONVERSION_TICK_2026-09-01_T2.md`](./CEO_CONVERSION_TICK_2026-09-01_T2.md) | [`CEO_DAILY_BRIEF_2026-09-01.md`](./CEO_DAILY_BRIEF_2026-09-01.md) |
| [`CEO_EXECUTION_TODAY_2026-09-01.md`](./CEO_EXECUTION_TODAY_2026-09-01.md) | [`CEO_DAILY_BRIEF_2026-09-02.md`](./CEO_DAILY_BRIEF_2026-09-02.md) |
| [`CEO_DAILY_BRIEF_2026-09-09.md`](./CEO_DAILY_BRIEF_2026-09-09.md) | [`CEO_DAILY_BRIEF_2026-09-19.md`](./CEO_DAILY_BRIEF_2026-09-19.md) |

## Housekeeping (2026-09-30)

Previously untracked files, resolved:

- **Tracked:** `.gitmodules` + `foundry.lock` (forge-std v1.9.7 / openzeppelin-contracts v5.5.0 — required for Foundry builds), `docs/architecture/MONEY_FLOW.md` (canonical money-flow doc), `toa_money_move_rehearsal.py` + `toa_bankruptcy_policy_rehearsal.py` (TOA rehearsal scripts, referenced by path).
- **Explicitly deferred:** `README.DRAFT.md` — full draft README awaiting founder review. Not tracked and not deleted; do not merge or remove without the founder's call.
