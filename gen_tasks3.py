#!/usr/bin/env python3
"""Third expansion: reach ~1000 tasks."""
import json

d = json.load(open("TASK_DECOMPOSITION.json"))
tasks = d["tasks"]
_n = len(tasks)

def T(parent_item, phase, title, scope, acceptance, size, deps=None, status="pending", blocked_by=None):
    global _n
    _n += 1
    tasks.append({
        "id": f"T{_n:04d}", "parent_item": parent_item, "phase": phase, "title": title,
        "scope": scope, "acceptance": acceptance, "est_size": size,
        "dependencies": deps or [], "status": status, "blocked_by": blocked_by,
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
UNBLOCKED = [p for p in PRODUCTS if p[0] not in LIVE_BLOCKED]
OBS_SKUS = ["health","audit-trail","evidence","uptime","proof-ledger","catalog-sync"]

# Per-product end-to-end walkthrough (24/7 directive: each product must genuinely work)
for pid, name, sku in PRODUCTS:
    blk = pid in LIVE_BLOCKED
    T(27,"P4",f"End-to-end walkthrough: {name}",
      f"Exercise {sku} through its real workflow on the merged tree; record pass/fail honestly.",
      "walkthrough log; no fabricated results", "M",
      status="blocked" if blk else "pending",
      blocked_by="Founder live-block decision required." if blk else None)

# Observability SKU walkthroughs for unblocked products
for pid, name, sku in UNBLOCKED:
    for s in OBS_SKUS:
        T(30,"P4",f"Observability walkthrough [{s}]: {name}",
          f"Run the {s} observability SKU live against {sku}; publish results.",
          f"{s} walkthrough green and published", "S")

# Per-branch merged-tree test re-verification (32 push-ready branches)
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
    T(0,"X",f"Re-run wave tests on merged tree: {b}",
      f"Re-run {b}'s new + neighbor suites after merge; deltas vs wave report triaged.",
      "green or deltas explained", "M")

# Per-item acceptance re-verification on merged tree (37 items)
for i in range(1, 38):
    ph = "P1" if i <= 7 else "P2" if i <= 13 else "P3" if i <= 26 else "P4" if i <= 31 else "P5"
    T(i, ph, f"Acceptance re-check: backlog item {i}",
      f"Verify item {i}'s acceptance criteria still hold on the merged tree.",
      "acceptance holds; evidence cited", "S")

# Per-product spec freshness + compliance scoring + unit re-run
for pid, name, sku in PRODUCTS:
    T(27,"P4",f"Unit suite re-run (merged): {name}",f"Re-run {sku} unit tests on merged tree.","green", "S")
    T(27,"P4",f"Spec freshness check: {name}",f"Confirm {sku} implementation matches its spec; file drift findings.","0 undocumented drift", "S")
    T(30,"P4",f"Compliance scoring re-run: {name}",f"Re-run {sku} compliance scoring on merged tree.","score recorded", "S")

# Swarm auction operationalization (founder directive: task auction, parallel)
for i in range(1, 9):
    T(0,"X",f"Swarm auction batch {i}: package + post",
      f"Package ~100 pending independent tasks into marketplace task postings (batch {i}); verify seed keys unique.",
      "batch posted; 0 duplicate seed_keys", "M")
T(0,"X","Task-board posting verification","Verify all posted batches visible via GET /v1/a2a/tasks with filters.","visible; counts match", "S")

# Security hardening sweep: top sensitive modules
SENSITIVE = ["a2a_integration.py","a2a_inbound_market.py","a2a_inbound_ext.py","stake_ledger.py",
    "sponsored_stake.py","recovery.py","a2a_bounty_pool.py","platform_payments.py","treasury_policy.py",
    "fee_conversion_executor.py","auction_bridge.py","a2a_rate_limits.py","wardrobe.py","billing.py",
    "kya.py","disputes","payment_verifier.py","x402_payments.py","agent_billing.py","mvp_app.py"]
for m in SENSITIVE:
    T(0,"X",f"Security sweep (merged): {m}",f"Focused review of {m}: auth, injection, secret handling, error leakage.","findings filed or clean note", "M")

# Docs: per new wave-doc review
for doc in ["CANONICAL_PATHS.md","MONEY_FLOW.md","DEFI_LEDGER_SKU_CANON.md","SHARED_STATE_ADOPTION.md",
    "MERGE_PLAN.md","MERGE_PLAN_SUPPLEMENT.md","FORK_SIM_NOTES.md","STAKE_SLASH_DEPLOY_RUNBOOK.md",
    "FEE_EXECUTOR_RUNBOOK.md","AUCTION_SECURITY_DECISIONS.md","AUCTION_TEST_MATRIX.md"]:
    T(6,"P1",f"Doc review: {doc}",f"Review {doc} for accuracy vs merged code; fix drift.","accurate; links resolve", "S")

# Merge hotspot resolution tasks (from MERGE_PLAN)
for h, note in [
    ("a2a_integration.py 10-branch overlap","dedupe w18/w24 identity helpers; order w13->w10->w14 money-path hunks"),
    ("a2a_inbound_market.py 7-branch cluster","resolve adjacent inserts at issuance/caller/idempotency/envelope/stream lines"),
    ("a2a_rate_limits.py w08 tier","re-add P24 issuance tier after w16 rewrite"),
    ("a2a_rate_limits.py w15 vs w16","port w15 backend as SharedStateRateLimitStore adapter"),
    ("templates/axiom.html w02 vs w22","merge w02 first, take w22 side, re-grep fiction strings"),
    ("proof_ledger.json 5-branch chain","entry-ID concatenation w20->w21->w25->w26->w28"),
    ("a2a_inbound_ext.py handler registration","keep inside w23 first-mount idempotency guard"),
    ("a2a_sdk.py adjacent inserts","merge w07/w09/w18 SDK additions"),
    ("payment_verifier.py w10 vs w13","reconcile verifier changes"),
    ("test assertion conflicts","w16 unmapped-routes assertion wins; byte-identical hunks auto-resolve")]:
    T(0,"X",f"Merge hotspot: {h}",f"Resolve per MERGE_PLAN.md: {note}.","resolved; tests green", "M")

# Dead-module deletion execution (17)
for m in ["adversarial_resilience","agent_schema","check_status","interop_negotiation","sinc_payment_verifier",
    "startup","zk_privacy_layer","security_lockdown","membership","lifecycle_system","unified_content_engine",
    "hitl_protocol","meta_optimizer","bidding_engine","marketplace/barter_engine","chroma_app","wsgi"]:
    T(4,"P1",f"Execute disposition: {m}",f"Delete or archive {m} per recorded decision; update archive index.","tree clean; index accurate", "S")

# CertiK per-contract re-examination (blocked on evidence)
for c in ["SINC live","AXM","CreatorTokenFactory","BondingCurve","FeeSplitDistributor"]:
    T(3,"P1",f"Skynet evidence check: {c}",f"Verify current Skynet score report names the live {c} contract address.","linked claim or no claim", "S",
      status="blocked", blocked_by="Founder must provide current CertiK Skynet scan URLs/report PDFs.")

# P24 rehearsal sub-steps
for s in ["Fund rehearsal wallet (Sepolia)","Dry-run deploy script","Deploy to Sepolia","Verify contracts","Rehearse issue() end-to-end",
    "Rehearse policy rejection","Gas profile","Rehearsal report"]:
    T(8,"P2",f"P24 rehearsal: {s}",f"Execute rehearsal step: {s}.","step complete; evidence logged", "S",
      status="blocked", blocked_by="Founder must authorize contract broadcast + fund deployer wallet on Base Sepolia.")

# Monitoring / ops
for s in ["Basescan fee watcher: first realized fee alert","Liveness runner: commit/reveal coverage restored when stake funded",
    "Railway /data volume mount verification","orders.db backup procedure","Pool ledger self-heal verification",
    "Heartbeat token rotation procedure","Admin credential rotation procedure","Incident runbook: RPC outage",
    "Incident runbook: Redis outage","Post-deploy smoke checklist"]:
    T(0,"X",f"Ops: {s}",f"Implement/verify: {s}.","done; runbook updated", "S")

# Load tests on critical routes
for r in ["settle","bids/commit","v1_stream","tasks/send","close_auction","stake/deposit"]:
    T(0,"X",f"Load test: {r}",f"Load-test {r} to 10x expected peak; record p95/limits.","report; no worker exhaustion", "M")

# Program management close-out
for s in ["Phase summaries P1-P5","Program retrospective","Handoff doc to founder","Worktree cleanup after landing",
    "Delete merged branches","Goal close-out review","Cron audit: memory-safety clauses present",
    "Final program report"]:
    T(0,"X",f"Program: {s}",f"Complete: {s}.","done", "S")

d["tasks"] = tasks
with open("TASK_DECOMPOSITION.json","w") as f:
    json.dump(d, f, indent=1)
print(f"TOTAL TASKS: {len(tasks)}")
from collections import Counter
print("by phase:", dict(Counter(t['phase'] for t in tasks)))
print("by status:", dict(Counter(t['status'] for t in tasks)))
print("by size:", dict(Counter(t['est_size'] for t in tasks)))
