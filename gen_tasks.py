#!/usr/bin/env python3
"""Generate TASK_DECOMPOSITION.json: ~1000 bounded swarm tasks from the 37-item gap audit."""
import json

tasks = []
_n = 0

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

# ---------------- Reference data ----------------
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

DEAD_MODULES = ["adversarial_resilience","agent_schema","check_status","interop_negotiation",
    "sinc_payment_verifier","startup","zk_privacy_layer","security_lockdown","membership",
    "lifecycle_system","unified_content_engine","hitl_protocol","meta_optimizer","bidding_engine",
    "marketplace/barter_engine","chroma_app","wsgi"]

IDEMP_ENDPOINTS = ["bids.place","bids.commit","bids.reveal","stake.deposit","pool.allocate",
    "tasks.create","settle","close_auction"]

RATELIMIT_ROUTES = ["POST /api/a2a","tasks/send","tasks/cancel","settle","leaderboard","pricing",
    "v1_chain","v1_stream","task create","close","proofs","heartbeat",
    "admin/sponsored-stake/fund","admin/sponsored-stake/status","admin/recovery/sponsor","admin/recovery/status"]

MERGE_ORDER = ["w01","w04","w03","w02","w05","w08","w11","w12","w07","w09","w06","w13","w10",
    "w14","w18","w24","w17","w16","w15","w20","w21","w25","w26","w28","w19","w22","w23",
    "w27","w30","w31","w33","w29"]
BRANCH_OF = {"w01":"xioix/p1-housekeeping-stale-strings","w02":"xioix/p1-fabricated-claims",
    "w03":"xioix/p1-burn-stats-retire","w04":"xioix/p1-mislabeled-files",
    "w05":"xioix/buildout-05-p24-bridge","w06":"xioix/buildout-06-caller-ownership",
    "w07":"xioix/buildout-07-registration-proof","w08":"xioix/buildout-08-issuance-route",
    "w09":"xioix/buildout-09-heartbeat-auth","w10":"xioix/buildout-10-settlement-proofs",
    "w11":"xioix/buildout-11-issuance-skill","w12":"xioix/buildout-12-p24-wiring-tests",
    "w13":"xioix/buildout-13-payment-amounts","w14":"xioix/buildout-14-write-idempotency",
    "w15":"xioix/buildout-15-durable-state","w16":"xioix/buildout-16-rate-limits-sse",
    "w17":"xioix/buildout-17-error-envelopes-admin","w18":"xioix/buildout-18-caller-quotas",
    "w19":"xioix/buildout-19-axm-fee-listener","w20":"xioix/buildout-20-defi-invariant-ledger",
    "w21":"xioix/buildout-21-defi-fuzz-suites","w22":"xioix/buildout-22-money-path-copy",
    "w23":"xioix/buildout-23-p1-cleanup","w24":"xioix/buildout-24-reputation-identity",
    "w25":"xioix/buildout-25-fork-sim-harness","w26":"xioix/buildout-26-ledger-hygiene",
    "w27":"xioix/buildout-27-stake-slash-design","w28":"xioix/buildout-28-audit-artifacts",
    "w29":"xioix/buildout-29-merge-recon","w30":"xioix/buildout-30-shared-store-adopt",
    "w31":"xioix/buildout-31-merge-supplement","w33":"xioix/buildout-33-docs-index-refresh"}

# ============ P1 — Org / housekeeping ============
# Item 1: burn contradiction (done: w01 1a, w03 1b; w34 in flight: AGENT_BURN_AUTO)
t = T(1,"P1","Remove burn-policy metadata string","Delete 'burn_policy: 50% ops retention / 50% burn' from agent_billing.py:52 and any test fixture echoing it.","grep -rn '50% burn' src/ marketplace/ returns nothing", "S", status="done")
t2 = T(1,"P1","Remove would_burn_atomic computation","Delete would_burn_atomic (agent_billing.py:92) and callers; keep locked 5%-no-burn policy intact.","grep -rn 'would_burn' src/ returns nothing; 5% fee path tests still green", "S", status="done")
T(1,"P1","Retire /api/sinc/burn-stats narrative","Endpoint returns 410 or is relabeled historical-only; no live policy implication.","GET /api/sinc/burn-stats -> 410 or historical label; copy grep clean", "S", status="done")
T(1,"P1","Merge-time burn-string sweep","After all 32 branches merge, re-grep entire tree for 50%-burn / would_burn / deflationary copy.","zero hits; CI grep job added to PR checklist", "S", deps=[t])
T(1,"P1","Remove inert AGENT_BURN_AUTO dead path","Delete AGENT_BURN_AUTO flag + _attempt_auto_burn (triply inert); verify zero live callers first.","no references remain; agent_billing neighbor tests green", "S")
T(1,"P1","Verify burn removal in merged tree","Full-suite run: no test asserts burn behavior.","test suite green; no burn assertions", "S", deps=[t])

# Item 2: stale agent counts (done w01)
T(2,"P1","Fix '43 agents' discovery strings","a2a_integration.py:32, :1090 -> 48.","no '43 agents' in machine-readable discovery", "S", status="done")
T(2,"P1","Fix '42 agents' routed templates","pitch.html, product_enterprise.html, product_starter.html -> 48.","rendered HTML grep clean", "S", status="done")
T(2,"P1","Merge-time agent-count sweep","Grep merged tree for 42/43-agent strings in routed templates + discovery docs.","zero hits", "S")
T(2,"P1","Fleet-count single source of truth","One constant/module owns the fleet count; templates + discovery read it.","single constant; no hardcoded counts", "M")

# Item 3: fabricated claims (done w02; CertiK re-exam blocked on evidence)
T(3,"P1","Remove CertiK 97/100 outreach defaults","partner_outreach.py:42 certik placeholder + :194 default; value_engine live-on-Base copy; stale launch date -> 2026-11-09.","no 'certik' defaults, no $0.15-floor copy, no live-on-Base claims in served copy", "S", status="done")
T(3,"P1","CertiK/Skynet re-examination task","When founder supplies current Skynet scan URLs: verify contract addresses vs live SINC/AXM, restore linked score claims per TOKEN_CANON rules.","linked claims only with verified report URLs naming live addresses", "M", status="blocked", blocked_by="Founder must provide current CertiK Skynet scan URLs/report PDFs for live SINC and AXM contracts.")
T(3,"P1","TOKEN_CANON enforcement check","Automated check that no live claim exceeds what TOKEN_CANON.json permits.","CI check green", "S")
T(3,"P1","Outreach copy compliance gate","obs_skus/gates.py certik-ban verified against partner_outreach templates.","gate test green", "S", status="done")

# Item 4: quarantine dead modules (done w23: quarantine, not deletion)
for i, m in enumerate(DEAD_MODULES):
    T(4,"P1",f"Dead-module disposition: {m}","Decide delete vs archive-retain for {m}; record decision; if archived keep index note accurate.","decision recorded; module absent from src tree or in archive/ with note", "S", status="done" if i < 8 else "pending")
T(4,"P1","Zero-importer sweep on merged tree","Repo-wide grep for zero-importer modules outside archive/ after merge.","none found", "S")
T(4,"P1","Archive index accuracy","archive/ index lists every quarantined module with reason.","index matches directory", "S", status="done")

# Item 5: mislabeled files (done w04)
T(5,"P1","contract_net.py docstring correction","Docstring matches live usage by a2a_inbound blueprints or callers migrated to canonical path.","docstring matches call graph", "S", status="done")
T(5,"P1","order_fulfillment.py broken import fix","Fix bare 'from unified_content_engine' import.","import succeeds; module loads", "S", status="done")
T(5,"P1","Ghost-canonical reference fix","marketplace/contract_net/engine.py:3 no longer cites zero-caller BiddingEngine as canonical.","reference corrected", "S", status="done")
T(5,"P1","Merge-time canonical-path verification","Confirm canonical auction path = sealed-bid Vickrey marketplace.contract_net in merged tree.","docs + code agree", "S")

# Item 6: doc index (done w23, refreshed w33)
T(6,"P1","Expand docs/README.md index","All top-level docs + per-subdir READMEs (architecture, api, deployment, launch, security, specs, token).","every doc reachable from an index", "M", status="done")
T(6,"P1","Resolve untracked files","README.DRAFT.md review decision; MONEY_FLOW.md tracking; toa scripts; .gitmodules; foundry.lock.","git status clean or explicitly deferred", "M", status="done")
T(6,"P1","Refresh index across build-out waves","Index all 11 new wave docs; 114 relative links resolve; zero policy contradictions.","w33 committed", "M", status="done")
T(6,"P1","Merge-time index supremacy","Take w33 docs/README.md over w23 at merge (superset).","merged index covers all", "S")

# Item 7: canonical paths (done w23)
T(7,"P1","Document canonical auction money path","One canonical path named in docs; divergent inbound usage migrated or grandfathered.","docs/architecture/CANONICAL_PATHS.md exists", "M", status="done")
T(7,"P1","Treasury/payment module responsibility map","Document which of the 8 modules is canonical for what; quarantine sinc_payment_verifier.","map published; dead twin quarantined", "M", status="done")
T(7,"P1","Migrate divergent inbound auction callers","a2a_inbound*.py call canonical contract_net or carry explicit grandfather note.","callers migrated or noted", "M")
T(7,"P1","Pricing-engine scope disambiguation","dynamic_pricing_engine vs defi/pricing scopes documented.","no same-name confusion", "S", status="done")

# ============ P2 — P24 issuance wiring ============
# Item 8: deploy P24 (blocked: broadcast)
d8a = T(8,"P2","P24 Foundry deploy script review","Review onchain/script deploy script: ContentPolicyGuard -> CreatorTokenFactory -> FeeSplitDistributor -> RevenueAccrual order; constructor args pinned.","script reviewed; args checklist complete", "M", status="done")
T(8,"P2","P24 Sepolia dry-run rehearsal","forge script --dry-run against Base Sepolia; issue() rehearsal succeeds.","dry-run green; gas profiled", "M", deps=[d8a], status="done")
T(8,"P2","P24 deploy ceremony (Sepolia)","Broadcast deployment; record addresses/tx; commit onchain/deployments/base-sepolia-p24.json.","manifest committed", "M", deps=[d8a], status="blocked", blocked_by="Founder must authorize contract broadcast + fund deployer wallet on Base Sepolia.")
T(8,"P2","Sourcify/Etherscan verification","Verify all 4 P24 contracts on Basescan + Sourcify.","verified badges present", "S", status="blocked", blocked_by="Blocked on P24 Sepolia deployment (founder broadcast authorization).")
T(8,"P2","P24 mainnet deploy decision","Founder go/no-go for mainnet P24 deployment after Sepolia matrix green.","recorded decision", "S", status="blocked", blocked_by="Founder decision: mainnet P24 deployment timing; depends on Sepolia 6-phase matrix 100% green.")

# Item 9: bridge (done w05)
T(9,"P2","P24 bridge module","src/sincor2/defi/p24/bridge.py: exact issue() calldata, eth_call dry-run, caller-supplied signing, never imports eth_account.","12/12 bridge tests green; no eth_account import", "M", status="done")
T(9,"P2","Bridge calldata byte-exactness proof","Differential test: bridge calldata matches contract ABI encoding.","test green", "S", status="done")

# Item 10: route + SDK (done w08; merge hazard: re-add issuance tier after w16)
T(10,"P2","Issuance route + SDK","POST /v1/a2a/socialfi/issue authenticated + rate-limited; SincorAgentSDK.issue_creator_token.","401/403 unauth; 429 over-limit; 400 policy-violation", "M", status="done")
t10m = T(10,"P2","Merge-time: re-add issuance rate tier","w16 rewrite drops w08 issuance tier in a2a_rate_limits.py; re-add tier + endpoint mapping + per-agent keying after w16.","issuance tier present in merged a2a_rate_limits.py; test pins it", "S")

# Item 11: skill (done w11)
T(11,"P2","Issuance agent skill","Skill drives OnboardingAgent.register -> issuance route; enforcement stays in onboarding.py::register.","e2e test: screened->issued or rejected with logged rule", "M", status="done")

# Item 12: wiring tests (done w12)
T(12,"P2","P24 wiring tests","test_defi_p24_wiring.py + Foundry access-control (admin-only issue, screened gate, symbol uniqueness).","all green, recorded in ledger", "M", status="done")
T(12,"P2","Zero-creator factory guard","CreatorTokenFactory rejects zero creator address.","test green (hole fixed in w12)", "S", status="done")

# Item 13: founder decisions (blocked)
T(13,"P2","Decision: factory admin custody","Secure Vault/forwarder vs relayer; EOA vs multisig.","recorded decision + runbook updated", "S", status="blocked", blocked_by="Founder decision: P24 factory-admin key custody model.")
T(13,"P2","Decision: ContentPolicyGuard screener key","Who holds the screener key, distinct from admin.","recorded decision", "S", status="blocked", blocked_by="Founder decision: P24 content-policy screener identity/key.")
T(13,"P2","Decision: live_block release valve","Release or retain P24 live block (gates.py:290-298).","recorded decision; gates updated", "S", status="blocked", blocked_by="Founder decision: release or retain P24 live block.")
T(13,"P2","Apply custody decisions to deploy script","Wire decided custody into deploy ceremony args.","script matches decisions", "S", status="blocked", blocked_by="Blocked on the three P24 custody/screener/live-block decisions above.")

# ============ P3 — A2A hardening ============
# Item 14: caller ownership (done w06)
T(14,"P3","Cancel/read restricted to creating caller","Cross-caller cancel/read -> 403.","35/35 new + 311 neighbors green", "M", status="done")
T(14,"P3","Server-bound poster identity","Spoofed poster_id ignored; server binds identity.","test green", "S", status="done")
T(14,"P3","Merge-time ownership regression","Re-run ownership tests on merged tree.","green", "S")

# Item 15: registration proof (done w07; w32 in flight extends)
T(15,"P3","Signed re-registration","Wallet overwrite without signature -> 403.","9/9 new + 181 neighbors green", "M", status="done")
T(15,"P3","First-registration wallet-control proof","Require wallet-control proof at first registration (closes squatting residual).","unverified claim rejected or marked untrusted; tests green", "M")

# Item 16: heartbeat auth (done w09)
T(16,"P3","Verify heartbeat signature","Unsigned heartbeat -> 401; spoofed agent_id liveness impossible.","16/16 new + 167 neighbors green", "M", status="done")
T(16,"P3","Deploy config: AGENT_HEARTBEAT_TOKEN sync","Same token in Railway + liveness-runner host.","heartbeat 200 in prod", "S", status="blocked", blocked_by="Requires production-auth change authorization (Railway + liveness host env).")

# Item 17: settlement proofs (done w10)
T(17,"P3","Real settlement receipts","settle returns verifiable proof or docstring claim removed; tx_hash bound to task payment.","proof verifies offline; mismatched tx_hash -> 400", "M", status="done")

# Item 18: payment amounts (done w13)
T(18,"P3","Reconcile axmPaidWei vs chain","Inflated client amount corrected or rejected.","20/20 new + 81 neighbors green", "M", status="done")

# Item 19: quota identity (done w18 + w24)
T(19,"P3","Quota keyed on verified identity","ID rotation does not reset quota; mandatory wallet-claim equality check.","21/21 new + 199 neighbors green", "M", status="done")
T(19,"P3","Reputation keyed on verified identity","wallet: vs id: trust-domain namespacing; ghost wallet reset.","19/19 new + 121 neighbors green", "M", status="done")
t19m = T(19,"P3","Merge-time: dedupe identity helpers","Fold w18 _resolve_quota_identity + w24 _resolve_verified_wallet into one shared helper.","single helper; both suites green", "M")

# Item 20: idempotency (done w14; settle residual verified covered)
for ep in IDEMP_ENDPOINTS:
    T(20,"P3",f"Idempotency-Key on {ep}","Retried POST with same key returns original result, no double-record.",f"race test green for {ep}", "S", status="done")
T(20,"P3","Settle double-record verification","Confirm @idempotent('settle') survives merge (w30 verified on w14 branch).","decorator present in merged tree", "S", status="done")
T(20,"P3","Merge-time: adopt shared idempotency store","Apply SHARED_STATE_ADOPTION.md: keep w14 protocol (fingerprint/inflight/TTL), optional shared backend.","adapters wired; tests green", "M", deps=[t19m])

# Item 21: durable state (done w15; adoption guide w30)
T(21,"P3","Shared-state interface","Memory/SQLite/Redis drivers; production default durable SQLite under SINCOR_DATA_DIR.","26/26 new + 58 neighbors green", "M", status="done")
T(21,"P3","Merge-time: adopt shared rate-limit store","SharedRateLimitStore adapter on w16 module; wall-clock not monotonic; fail-closed 503 on store failure.","guide applied; tests green", "M")
T(21,"P3","Merge-time: adopt shared quota store","SharedQuotaStore via atomic claim_window_hit, 10y window.","guide applied; tests green", "M")
T(21,"P3","Rolling-restart state survival test","Restart preserves task state and quotas; two workers agree.","test green on merged tree", "M")

# Item 22: rate-limit coverage (done w16; merge hazard re-add)
for r in RATELIMIT_ROUTES:
    T(22,"P3",f"Rate-limit tier: {r}","Explicit tier in ENDPOINT_POLICY; 429 + Retry-After enforced.",f"limit test green for {r}", "S", status="done")
T(22,"P3","Merge-time: keep w15+w16 halves","Never take either a2a_rate_limits.py side wholesale; port w15 enforcer hunks onto w16 module.","both halves present; tests green", "M", deps=[t10m])

# Item 23: stream hardening (done w16)
T(23,"P3","SSE auth + throttle","Unauthenticated stream -> 401; per-caller connection caps; leak-safe slot release.","30/30 new incl. stream tests green", "M", status="done")

# Item 24: error envelopes (done w17)
T(24,"P3","JSON error envelopes","errorhandlers on both A2A blueprints; numeric query validation; generic 500s.","garbage ?limit=abc -> JSON 400", "M", status="done")

# Item 25: admin credential unification (done w17)
T(25,"P3","Single admin credential","ADMIN_PASSWORD via X-Admin-Key; request-body keys retired; pool-release failures loud 500.","15/15 new + 211 neighbors green", "M", status="done")
T(25,"P3","Deploy config: ADMIN_PASSWORD on Railway","Pool-admin routes 503 without it.","configured; route 200 with key", "S", status="blocked", blocked_by="Requires production-auth change authorization (Railway ADMIN_PASSWORD).")

# Item 26: dormant secrets (done w23)
T(26,"P3","Remove DEMO_SECRET default","sign_payload requires explicit secret; no hardcoded signing secrets.","87/87 green", "S", status="done")
T(26,"P3","Idempotent blueprint mounting","Double register() safe.","test green", "S", status="done")

# ============ P4 — DeFi gates ============
# Item 27: invariant tests (done w20 + w21)
t27 = T(27,"P4","Record invariant tests for all 26 products","Run on-disk suites; record invariant_test entries (passed>0, failed==0) via ledger API.","26 products hold passing invariant_test entries", "L", status="done")
for pid, name, sku in PRODUCTS:
    T(27,"P4",f"Invariant ledger entry: {name}",f"Genuine seeded invariant/fuzz suite green for {sku}; entry recorded.",
      f"entry present and _passing_entries finds it", "S", status="done", deps=[t27])
T(27,"P4","Merge-time ledger entry-ID concatenation","w20->w21 entries merged by entry-ID concat, never text-merge; JSON validated.","53+ entries; zero dupes", "S")

# Item 28: fork-sim harness (done w25; 9 honest findings blocked on address pinning)
t28 = T(28,"P4","Fork-simulation harness","Read-only replay vs Base mainnet at pinned block; real product invariants on live data.","17/17 sims green; harness gate-check passes", "L", status="done")
FORK_GREEN = {1,3,4,5,7,8,9,12,15,16,18,20,21,22,24,25,26}
for pid, name, sku in PRODUCTS:
    if pid in FORK_GREEN:
        T(28,"P4",f"Fork sim: {name}",f"fork_sim entry recorded for {sku} from live Base data.",f"entry present; passed=1", "S", status="done", deps=[t28])
    else:
        T(28,"P4",f"Fork sim: {name} (needs address pinning)",f"No onchain counterpart pinned for {sku}; pin official protocol addresses then sim.",
          f"fork_sim entry recorded", "M", status="blocked", deps=[t28],
          blocked_by="Founder/auditor must pin official protocol/oracle addresses before an honest fork sim exists.")
T(28,"P4","Treasury contract characterization","Treasury 0x09E2...9612Ac confirmed contract (23 bytes); USDC 733 wei documented as sim bound.","noted in sim scopes", "S", status="done")

# Item 29: ledger hygiene (done w26)
T(29,"P4","Supersede stale-SKU entries","Mark 3 stale entries superseded; 9 already resolved; canonical SKU doc published.","0 active stale entries; gates unaffected", "M", status="done")
T(29,"P4","SKU canon documentation","docs/DEFI_LEDGER_SKU_CANON.md: canonical mint_sku rule + supersede procedure.","doc committed", "S", status="done")

# Item 30: audit artifacts (done w28)
t30 = T(30,"P4","Internal audit-report entries","Genuine manual review of all 26 modules; 6 low/info findings; 0 open criticals; entries carry not_a_third_party_audit=True.","audit->product gate evaluable", "L", status="done")
for pid, name, sku in PRODUCTS:
    T(30,"P4",f"Audit entry: {name}",f"audit_report entry for {sku} with scope/checklist/findings/gate decision.",
      f"entry present; 0 open criticals", "S", status="done", deps=[t30])
T(30,"P4","Third-party audit engagement decision","Founder decides whether to engage external auditor before mainnet.","recorded decision", "S", status="blocked", blocked_by="Founder decision: engage third-party auditor (and which firm) before mainnet.")

# Item 31: live-block decisions (blocked: 19 products)
for pid, name, sku in PRODUCTS:
    if pid in LIVE_BLOCKED:
        T(31,"P4",f"Live-block decision: {name}",f"Founder: unblock or confirmed keep-blocked for {sku}; if unblocked, re-audit + pinned addresses + oracle verification.",
          f"recorded decision in products_state.json", "M", status="blocked",
          blocked_by="Founder decision: release live_block on 19 products (incl. P24) or keep blocked.")
    else:
        T(31,"P4",f"Live-block decision: {name}",f"{sku} not blocked; confirm product-stage readiness path.",f"decision recorded", "S", status="pending")
T(31,"P4","Address pinning + oracle verification","Pin official protocol/oracle addresses per spec for any unblocked product.","pinned addresses committed", "L", status="blocked", blocked_by="Blocked on live-block unblock decisions (19 products).")

# ============ P5 — AXM money path ============
# Item 32: arm executor (blocked: founder arming ceremony)
T(32,"P5","Pin AXM->USDC/WETH V4 pool","Identify and verify the real pool the executor will route through.","pool address pinned + verified", "M", status="blocked", blocked_by="Founder decision: arm conversion executor (item 32) — pool pinning is step 1 of the arming ceremony.")
T(32,"P5","Fork-simulate conversion path","Simulate swap on fork before arming; slippage/fees within bounds.","sim green", "M", status="blocked", blocked_by="Blocked on item-32 arming decision.")
T(32,"P5","Forwarder key into Secure Vault","Key stored via approved secure flow; executor reads from vault only.","vault check green", "S", status="blocked", blocked_by="Blocked on item-32 arming decision.")
T(32,"P5","Arming ceremony: armed=True","Set armed=True only after pool pin + sim + vault key + founder sign-off.","first real onchain conversion settles; treasury receipt verifiable", "S", status="blocked", blocked_by="Founder must authorize arming the fee-conversion executor (money movement).")
T(32,"P5","Reconcile listener obligations before arming","Pending conversion obligations from fee listener reconciled against receipts.","reconciliation report", "M")

# Item 33: listener (done w19)
T(33,"P5","Fee-event listener","Polls confirmed AXM transfers to fee recipient; creates 5% obligations; cursor persists; reorg-safe.","75/75 green; executor stays disarmed", "L", status="done")
T(33,"P5","Residual: pin exact billing address","Every transfer to fee recipient treated as payment until billing address pinned.","address pinned; obligations reconciled vs receipts", "M")

# Item 34: dangerous signal (done w19)
T(34,"P5","Fail-closed treasury policy","convert_before_treasury_if_needed() returns converted=False with reason until armed.","no path believes conversion happened", "M", status="done")

# Item 35: money-path copy (done w22)
T(35,"P5","Honest money-path copy","Remove live-on-V4/80%-routing fiction from /axiom, privacy.html, x402 labels, settlement.py docstring.","no public copy implies unperformed onchain movement", "M", status="done")
T(35,"P5","x402 billing denomination decision","Keep SINC default or move to AXM (product decision).","recorded decision", "S", status="blocked", blocked_by="Founder decision: x402 billing default — SINC or AXM.")

# Item 36: onchain stake/slash (design done w27; deploy blocked)
T(36,"P5","Stake/slash contract design","StakeSlashManager.sol: ERC-20 deposits, 7-day timelock, adjudicator-signed EIP-191 slash, rotation.","12/12 bridge tests green; undeployed", "L", status="done")
T(36,"P5","Deploy-ceremony checklist review","Review docs/ops/STAKE_SLASH_DEPLOY_RUNBOOK.md: AXM collateral pin, treasury/admin, minStakeWei.","checklist complete", "S", status="done")
T(36,"P5","Deploy StakeSlashManager (Sepolia)","Broadcast; verify on Basescan+Sourcify; smoke matrix.","verified; smoke green", "M", status="blocked", blocked_by="Founder must authorize contract broadcast + fund deployer wallet.")
T(36,"P5","Wire stake_ledger to deployed contract","Set STAKE_SLASH_ADDRESS/RPC_URL/CHAIN_ID; dry_run green before real deposits.","dry-run green", "S", status="blocked", blocked_by="Blocked on StakeSlashManager deployment.")
T(36,"P5","posterReAuctionCredits draw-down","Implement poster fast-path draw-down of slash proceeds (deferred follow-up).","test green", "M")

# Item 37: price-floor coherence (blocked)
T(37,"P5","Price-floor single source of truth","Resolve $0.15 (served) vs $1.50 (whitepaper): one figure everywhere, with owner.","one figure; owner recorded", "S", status="blocked", blocked_by="Founder decision: $0.15 vs $1.50 price floor — single source of truth.")
T(37,"P5","Propagate decided figure","Update api_price_official, whitepaper reference, value_engine copy to the decided figure.","grep: single figure", "S", status="blocked", blocked_by="Blocked on price-floor decision.")

# ============ Cross-cutting: merge, push, verify, deploy ============
# Milestone: PAT + push batch (human-gated)
t_pat = T(0,"X","Milestone: request one-shot PAT","Request fresh one-shot PAT only when reviewed batch is ready; one-shot use, never stored.","PAT obtained and discarded after use", "S", status="blocked", blocked_by="Human gate: PAT request needs founder go-ahead when the reviewed batch is ready.")
prev = None
for w in MERGE_ORDER:
    b = BRANCH_OF[w]
    deps = [prev] if prev else []
    m = T(0,"X",f"Merge {b}","Human merges {b} in merge-order position; resolve per MERGE_PLAN.md conflict notes.".format(b=b),f"{b} merged; no conflicts unaddressed", "M", deps=deps, status="blocked", blocked_by="Human-gated: merges are never autonomous. Blocked until founder merges PRs.")
    v = T(0,"X",f"Verify {b} post-merge","Run neighboring suites for {b}'s touch areas on the merged tree.".format(b=b),"neighbor suites green", "S", deps=[m])
    prev = v
T(0,"X","Post-merge full test suite","Run the entire pytest suite on the merged tree; triage failures vs base.","suite green or failures triaged as pre-existing", "L", deps=[prev])
T(0,"X","Post-merge gate evaluation","evaluate() all 26 products on merged ledger: test->audit opens where evidence complete.","gate report; 0 unexpected refusals", "M", deps=[prev])
T(0,"X","Post-merge secret scan","Re-scan merged diff for keys/PATs/connection strings.","clean", "S", deps=[prev])
T(0,"X","Post-merge burn/copy grep sweep","Re-grep merged tree for burn fiction, live-on-Base claims, stale agent counts.","zero hits", "S", deps=[prev])
T(0,"X","Deploy-config checklist application","AGENT_HEARTBEAT_TOKEN, ADMIN_PASSWORD, A2A_STATE_STORE, SINCOR_FEE_EXECUTOR_ARMED=false, DEMO_SECRET absence, CreatorTokenFactory source.","checklist signed off", "M", deps=[prev], status="blocked", blocked_by="Requires production-auth change authorization.")
# Push batch: push 32 branches + open human-gated PRs (grouped)
PUSH_GROUPS = [("P1 housekeeping",4),("P2 P24 wiring",4),("P3 A2A hardening",11),("P4 DeFi gates",5),("P5 AXM money path",3),("planning/docs",5)]
for gname, count in PUSH_GROUPS:
    T(0,"X",f"Push batch: {gname}","Push the grouped branches with the one-shot PAT; open human-gated PRs, never merge.",f"{count} branches pushed; PRs opened", "M", deps=[t_pat], status="blocked", blocked_by="Human gate: push/PR batch needs founder go-ahead + one-shot PAT.")
# Sepolia deploy ceremony + test matrix (blocked on founder)
t_deploy = T(0,"X","Milestone: Base Sepolia deploy ceremony","Deploy auction + P24 + stake/slash contracts per runbooks; founder authorizes each broadcast.","contracts verified onchain", "L", status="blocked", blocked_by="Founder must authorize each contract broadcast + fund wallets.")
T(0,"X","Six-phase test matrix on Sepolia","Run docs/ops/AUCTION_TEST_MATRIX.md against deployed contracts.","100% green", "L", deps=[t_deploy], status="blocked", blocked_by="Blocked on Sepolia deployment.")
T(0,"X","Outreach unblock evaluation","Until Sepolia + 6-phase matrix 100% green, copy stays proof-gated.","matrix green -> outreach unlocked", "S", deps=[t_deploy], status="blocked", blocked_by="Blocked on Sepolia deployment + 100% green matrix.")

with open("TASK_DECOMPOSITION.json","w") as f:
    json.dump({"generated":"2026-09-30","base":"fd96801","tasks":tasks}, f, indent=1)
print(f"TOTAL TASKS: {len(tasks)}")
from collections import Counter
print("by phase:", dict(Counter(t['phase'] for t in tasks)))
print("by status:", dict(Counter(t['status'] for t in tasks)))
print("by size:", dict(Counter(t['est_size'] for t in tasks)))
