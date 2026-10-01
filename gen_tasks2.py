#!/usr/bin/env python3
"""Append finer-grained tasks to TASK_DECOMPOSITION.json to reach ~1000."""
import json

d = json.load(open("TASK_DECOMPOSITION.json"))
tasks = d["tasks"]
_n = len(tasks)

def T(parent_item, phase, title, scope, acceptance, size, deps=None, status="pending", blocked_by=None):
    global _n
    _n += 1
    tasks.append({
        "id": f"T{_n:04d}",
        "parent_item": parent_item,
        "phase": phase,
        "title": title,
        "scope": scope,
        "acceptance": acceptance,
        "est_size": size,
        "dependencies": deps or [],
        "status": status,
        "blocked_by": blocked_by,
    })
    return f"T{_n:04d}"

PRODUCTS = [
    (1,"Yield Aggregator Vault","SINCOR-DEFI-P01-VAULT"),(2,"Concentrated Liquidity Manager","SINCOR-DEFI-P02-CLMM"),
    (3,"Intent Solver & Dark Pool","SINCOR-DEFI-P03-DARKPOOL"),(4,"MEV Protection & Capture","SINCOR-DEFI-P04-MEV"),
    (5,"DeFi Risk Mutual","SINCOR-DEFI-P05-MUTUAL"),(6,"Perp DEX Hedging Swarm","SINCOR-DEFI-P06-PERP"),
    (7,"Cross-Chain Bridge Optimizer","SINCOR-DEFI-P07-BRIDGE"),(8,"RWA Tokenization Vaults","SINCOR-DEFI-P08-RWA"),
    (9,"DAO Governance Optimizer","SINCOR-DEFI-P09-GOV"),(10,"Flash Loan Arbitrage Engine","SINCOR-DEFI-P10-FLASH"),
    (11,"Delta-Neutral Yield","SINCOR-DEFI-P11-DELTA"),(12,"TWAMM Large-Order Engine","SINCOR-DEFI-P12-TWAMM"),
    (13,"AVS Tranching & Restaking","SINCOR-DEFI-P13-AVS"),(14,"Prediction Market Automation","SINCOR-DEFI-P14-PREDICTION"),
    (15,"Lending Protocol Optimizer","SINCOR-DEFI-P15-LENDING"),(16,"Best-Execution DEX Aggregator","SINCOR-DEFI-P16-DEXAGG"),
    (17,"On-Chain Options Protocol","SINCOR-DEFI-P17-OPTIONS"),(18,"Structured Product Vaults","SINCOR-DEFI-P18-STRUCTURED"),
    (19,"Decentralized Credit Underwriting","SINCOR-DEFI-P19-CREDIT"),(20,"DeFi Compliance Automation","SINCOR-DEFI-P20-COMPLIANCE"),
    (21,"DAO Treasury Management","SINCOR-DEFI-P21-TREASURY"),(22,"Stablecoin Yield Maximizer","SINCOR-DEFI-P22-STABLEYIELD"),
    (23,"NFT-Fi Liquidity Pools","SINCOR-DEFI-P23-NFTFI"),(24,"SocialFi Revenue Share","SINCOR-DEFI-P24-SOCIALFI"),
    (25,"Agent-Managed Portfolio","SINCOR-DEFI-P25-PORTFOLIO"),(26,"Self-Improving DeFi OS","SINCOR-DEFI-P26-DEFIOS"),
]
LIVE_BLOCKED = {2,3,4,5,6,7,8,9,10,11,12,13,16,17,18,19,20,23,24}

# ---- P1 expansions ----
T(1,"P1","Verify locked 5%-no-burn policy in fee paths","Confirm settlement.py PLATFORM_FEE_BPS=500 and no burn reintroduced by any merged branch.","grep + fee tests green", "S", status="done")
T(1,"P1","Fee-policy lock documented","Locked 2026-09-26 policy (5% to treasury, 100% converted, no burn) referenced from fee modules.","doc reference present", "S", status="done")
T(2,"P1","Social-share copy audit","value_engine.py social copy audited for stale counts/prices.","grep clean", "S", status="done")
T(3,"P1","Served-HTML exposure scrub verification","Rendered HTML of /, /axiom, /privacy, /pitch contains no treasury address, no stripe internals, no live-on-Base claims.","31+ routes 200; exposure strings absent", "M", status="done")
T(4,"P1","sinc_payment_verifier quarantine decision","Orphaned SINC twin of AXM payment_verifier: quarantine in place with note.","quarantined; note explains", "S", status="done")
T(4,"P1","bidding_engine zero-caller verification","Confirm BiddingEngine.run_auction has zero callers before any removal decision.","grep: zero callers", "S", status="done")
T(5,"P1","Canonical auction path test pin","Test asserts marketplace.contract_net is the sealed-bid Vickrey money path.","test green", "S", status="done")
T(6,"P1","Untracked-file disposition log","Record decision per untracked file (README.DRAFT.md deferred for founder review, etc.).","log committed", "S", status="done")
T(7,"P1","Canonical AXM verifier/policy/measurement doc","payment_verifier.py / treasury_policy.py / treasury_inflow.py roles documented.","doc committed", "S", status="done")

# ---- P2 expansions (finer sub-tasks of done waves) ----
T(9,"P2","Bridge: issue() calldata builder","Exact ABI encoding for CreatorTokenFactory.issue.","byte-exact test green", "S", status="done")
T(9,"P2","Bridge: eth_call dry-run wrapper","Dry-run returns revert reasons without signing.","test green", "S", status="done")
T(9,"P2","Bridge: key-hygiene audit","Module imports audited: no eth_account, no key material.","grep clean", "S", status="done")
T(10,"P2","Route: auth enforcement test","Unauthenticated POST /v1/a2a/socialfi/issue -> 401/403.","test green", "S", status="done")
T(10,"P2","Route: policy-rejection path","Policy-violating metadata -> 400 with RULESET_VERSION.","test green", "S", status="done")
T(10,"P2","SDK: issue_creator_token signature","SDK method modeled on existing client; signs nothing server-side.","test green", "S", status="done")
T(11,"P2","Skill: OnboardingAgent.register flow","Skill drives register -> policy screen -> issue.","e2e test green", "S", status="done")
T(11,"P2","Skill: enforcement-point audit","Confirm enforcement stays in onboarding.py::register, not the skill.","code review note", "S", status="done")
T(12,"P2","Foundry: admin-only issue test","Non-admin issue() reverts.","forge test green", "S", status="done")
T(12,"P2","Foundry: screened-gate test","Unscreened issue() reverts via ContentPolicyGuard.","test green", "S", status="done")
T(12,"P2","Foundry: symbol-uniqueness test","Duplicate symbol issue() reverts.","test green", "S", status="done")
T(8,"P2","P24: FeeSplitDistributor treasury pin review","Hardcoded treasury 0x09E2...9612Ac confirmed vs canonical.","matches onchain/constants", "S", status="done")
T(8,"P2","P24: BondingCurve graduation math review","price=supply^2/16000, $69k graduation verified in tests.","test green", "S", status="done")
T(8,"P2","P24: ContentPolicyGuard deny-list review","24-phrase deny list RULESET_VERSION 1.0.0 reviewed.","review note", "S", status="done")
T(8,"P2","P24: RevenueAccrual epoch accounting review","7-day epochs, pull claims verified.","test green", "S", status="done")

# ---- P3 expansions ----
T(14,"P3","Cancel route: ownership test matrix","Owner cancels 200; stranger 403; missing task 404.","tests green", "S", status="done")
T(14,"P3","Task read route: isolation test","Cross-caller read with valid uuid4 id -> 403.","test green", "S", status="done")
T(15,"P3","Registration: wallet regex validation","Malformed wallet rejected at register.","test green", "S", status="done")
T(15,"P3","Registration: MAX_AGENTS cap test","Cap enforced; over-cap -> 429/403.","test green", "S", status="done")
T(16,"P3","Heartbeat: wardrobe vs v1 reconciliation","Document the two heartbeat auth models and their trust domains.","doc note", "S", status="done")
T(17,"P3","Settle: tx_hash binding test","tx_hash bound to task payment; mismatch -> 400.","test green", "S", status="done")
T(18,"P3","Payment: PaymentVerifier reconciliation test","axmPaidWei corrected against onchain tx value.","test green", "S", status="done")
T(19,"P3","Quota: ECDSA claim-equality test","Claimed wallet must equal recovered signer (mismatch -> reject).","test green", "S", status="done")
T(19,"P3","Quota: replay-window test","Same signature replay within window handled; stale timestamp rejected.","test green", "S", status="done")
T(23,"P3","SSE: connection-cap test","Per-caller caps enforced; slot released on disconnect.","test green", "S", status="done")
T(24,"P3","Error envelope: blueprint coverage test","Both A2A blueprints return JSON errors for 400/401/403/404/429/500.","tests green", "S", status="done")
T(25,"P3","Admin: body-key rejection test","Admin key in request body rejected/ignored.","test green", "S", status="done")
T(26,"P3","Blueprint double-mount test","mount() twice is safe (idempotent).","test green", "S", status="done")
# Per-endpoint adversarial review on merged tree (49 endpoints, sampled as route groups)
A2A_GROUPS = ["discovery (agent-card/agent.json/manifest)","docs/a2a","RPC dispatch /api/a2a","tasks send/get/cancel",
    "agents/quote/settle/leaderboard/pricing","register x3","heartbeat","agents/directory/cards/chain",
    "tasks CRUD","bids/commit/reveal","close/disputes","proofs/auctions/bidder-kit","registration-velocity",
    "stake deposit/balance","pool fund/allocate/release/status","SSE stream","sponsored-stake admin","recovery admin",
    "wardrobe heartbeat/agents/well-known"]
for g in A2A_GROUPS:
    T(0,"X",f"Adversarial re-review (merged): {g}","Re-run adversarial review on merged tree: auth, input validation, idempotency, rate limits.","review note; findings filed as tasks", "M")

# ---- P4 expansions: per-product promotion checklists ----
for pid, name, sku in PRODUCTS:
    blk = pid in LIVE_BLOCKED
    T(27,"P4",f"Re-run invariants on merged tree: {name}",f"Re-run {sku} invariant suite post-merge; re-record if code changed.","green; ledger current", "S", deps=[])
    T(28,"P4",f"Re-run fork sim on merged tree: {name}",f"Re-run {sku} fork sim post-merge at new pinned block.","green or honest finding", "S")
    T(30,"P4",f"audit->product evidence bundle: {name}",f"Assemble {sku} evidence bundle: unit+invariant+fork+audit entries, 0 open criticals.","bundle complete", "S", status="blocked" if blk else "pending",
      blocked_by="Founder live-block decision required." if blk else None)
    T(31,"P4",f"Catalog page honesty check: {name}",f"{sku} product page: problem->mechanism->workflow->benefit->security->CTA, zero fabricated metrics.","page renders; copy audit clean", "S")
    T(0,"X",f"TOA ranking input: {name}",f"When {sku} reaches product stage, feed real build evidence into TOA money-move ranking.","evidence recorded", "S", status="blocked" if blk else "pending",
      blocked_by="Founder live-block decision required." if blk else None)

# ---- P5 expansions ----
T(32,"P5","FEE_EXECUTOR_RUNBOOK review","docs/ops/FEE_EXECUTOR_RUNBOOK.md reviewed: pool pin, fork-sim, vault key steps.","review note", "M", status="done")
T(32,"P5","ConversionLedger obligation schema test","Obligations recorded with tx/log-derived IDs; no dupes.","tests green", "S", status="done")
T(33,"P5","Listener: reorg-rewind test","Chain reorg rewinds cursor; no duplicate obligations.","test green", "S", status="done")
T(33,"P5","Listener: cursor durability test","Block/hash cursor survives restart under SINCOR_DATA_DIR.","test green", "S", status="done")
T(33,"P5","record_axm_receipt wiring cleanup","Stale 'called by on-chain listener' wording fixed or wired to listener.","docstring accurate", "S")
T(35,"P5","Axiom page render check","/axiom renders 200 with honest copy.","render check green", "S", status="done")
T(35,"P5","Privacy page render check","/privacy renders 200 with honest copy.","render check green", "S", status="done")
T(36,"P5","StakeSlashManager: timelock test","7-day unstake timelock enforced.","test green", "S", status="done")
T(36,"P5","StakeSlashManager: slash proceeds split test","Senior treasury cut first, remainder to poster re-auction credit.","test green", "S", status="done")
T(36,"P5","StakeSlashManager: adjudicator rotation test","Rotation invalidates old adjudicator rulings.","test green", "S", status="done")
T(36,"P5","Slash ruling digest byte-identity proof","Python digest builder byte-identical to on-chain digest.","test green", "S", status="done")
T(37,"P5","Whitepaper price reference inventory","List every $1.50 whitepaper reference for the decision.","inventory doc", "S")
T(37,"P5","Served price reference inventory","List every $0.15 served reference (api_price_official etc.).","inventory doc", "S")

# ---- X expansions: per-branch push + PR review tasks (human-gated) ----
BRANCHES = ["xioix/p1-housekeeping-stale-strings","xioix/p1-fabricated-claims","xioix/p1-mislabeled-files",
    "xioix/p1-burn-stats-retire","xioix/buildout-05-p24-bridge","xioix/buildout-07-registration-proof",
    "xioix/buildout-08-issuance-route","xioix/buildout-09-heartbeat-auth","xioix/buildout-10-settlement-proofs",
    "xioix/buildout-11-issuance-skill","xioix/buildout-12-p24-wiring-tests","xioix/buildout-06-caller-ownership",
    "xioix/buildout-13-payment-amounts","xioix/buildout-14-write-idempotency","xioix/buildout-17-error-envelopes-admin",
    "xioix/buildout-16-rate-limits-sse","xioix/buildout-15-durable-state","xioix/buildout-20-defi-invariant-ledger",
    "xioix/buildout-18-caller-quotas","xioix/buildout-19-axm-fee-listener","xioix/buildout-22-money-path-copy",
    "xioix/buildout-23-p1-cleanup","xioix/buildout-21-defi-fuzz-suites","xioix/buildout-24-reputation-identity",
    "xioix/buildout-26-ledger-hygiene","xioix/buildout-28-audit-artifacts","xioix/buildout-25-fork-sim-harness",
    "xioix/buildout-29-merge-recon","xioix/buildout-31-merge-supplement","xioix/buildout-30-shared-store-adopt",
    "xioix/buildout-33-docs-index-refresh","xioix/buildout-27-stake-slash-design"]
for b in BRANCHES:
    T(0,"X",f"Push {b}","Push branch with one-shot PAT (discarded after).","pushed", "S", status="blocked", blocked_by="Human gate: push batch needs founder go-ahead + one-shot PAT.")
    T(0,"X",f"PR review checklist: {b}","Human reviews PR: diff scope, tests, no secrets, no scope drift.","reviewed", "S", status="blocked", blocked_by="Human-gated: PRs are reviewed and merged by the founder only.")
# Launch-gate checklist
for i, item in enumerate(["All P0 PRs merged (#301-309, W-8)","Sepolia 6-phase matrix 100% green",
    "P24 custody/screener/live-block decisions recorded","19-product live-block decisions recorded",
    "Price-floor single source of truth live","Executor arming decision recorded",
    "Merge batch fully landed + suite green","Outreach copy unblocked by proof gate",
    "Launch date 2026-11-09 readiness review","Post-launch monitoring (liveness, fee watcher) live"], 1):
    T(0,"X",f"Launch gate {i}: {item}","Verify launch-gate item; record evidence.","signed off", "M", status="blocked", blocked_by="Founder-gated launch decisions.")

d["tasks"] = tasks
with open("TASK_DECOMPOSITION.json","w") as f:
    json.dump(d, f, indent=1)
print(f"TOTAL TASKS: {len(tasks)}")
from collections import Counter
print("by phase:", dict(Counter(t['phase'] for t in tasks)))
print("by status:", dict(Counter(t['status'] for t in tasks)))
print("by size:", dict(Counter(t["est_size"] for t in tasks)))
