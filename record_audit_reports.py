"""Wave 28: record structured INTERNAL audit_report entries for the 26 DeFi products.

Each product module under src/sincor2/defi/ was actually read (2026-09-30,
xioix/buildout-28-audit-artifacts) and reviewed against a manual checklist:
money math (conservation, integer dust, share issuance), access control /
live-block enforcement, input validation, replay/double-spend guards, error
handling, and consistency with the documented load-bearing invariants.

These are INTERNAL review artifacts. They are NOT CertiK Token Scan scores,
NOT Skynet scans, and NOT formal third-party audits. Findings are honest:
real issues are recorded, products with none say so explicitly, and any stub
would say exactly that (no stubs were found: every product has a real
reference implementation on this base).

One audit_report entry per product, under the canonical SKU minted by
products.mint_sku. Gates evaluate _check_audit_report on details.open_critical.
"""
from __future__ import annotations

import subprocess
import sys

sys.path.insert(0, "src")

from sincor2.defi.proof_ledger import ProofLedger  # noqa: E402
from sincor2.defi.products import mint_sku  # noqa: E402

COMMIT = (
    subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    .stdout.strip()
)
RECORDED_BY = "xioix/buildout-28-audit-artifacts"

# protocol_id -> dict describing the internal review that actually happened
AUDITS = {
    "P01_YIELD_AGG": {
        "modules": ["src/sincor2/defi/yield_aggregator.py (397 lines, read in full)"],
        "checks": [
            "weight cap water-fill: conservation of weights verified; cap-yield-to-conservation precedence documented",
            "fail-safe path: _safest_cash picks lowest-risk enabled cash row (prior fuzz-found bug fixed in comments)",
            "EXECUTE_LIVE only emits intents; signing/broadcast external; default dry_run",
            "eligible-set risk filter + min-liquidity gating",
        ],
        "findings": [
            {"severity": "low",
             "finding": "env-derived numeric params (YIELD_MAX_SINGLE_STRATEGY_PCT, "
                        "YIELD_MAX_SLIPPAGE_BPS, YIELD_MIN_CAPITAL_USD) are parsed "
                        "without validation; a negative or zero "
                        "YIELD_MAX_SINGLE_STRATEGY_PCT would break the cap "
                        "water-fill logic. Harden: clamp/validate at import.",
             "status": "open"},
        ],
        "invariant_evidence": "tests/pytest/test_p01_audit_invariants.py green (wave 20)",
        "note": "Disabled SharedLiquidityVault strategy correctly excluded from eligible set; MORPHO_USDC_VAULT is the live-intent vault.",
    },
    "P02_CLMM": {
        "modules": ["src/sincor2/defi/clmm_manager.py (499 lines, read in full)"],
        "checks": [
            "CaptureDistributor.distribute: exact conservation assert "
            "(distributed + treasury_total == proceeds); dust floors to treasury",
            "claim(): pull-based, double-claim returns 0, never pushes",
            "VolRangeAgent.open_live_position: hard-blocked unless auditor gate flips; else NotImplementedError",
            "JIT detection math + fee surcharge caps; AccessControl role-gated pause",
        ],
        "findings": [],
        "invariant_evidence": "tests/pytest/test_p02_audit_invariants.py green (wave 20)",
    },
    "P03_INTENT_DARK": {
        "modules": ["src/sincor2/defi/intent_dark_pool.py (531 lines, read in full)"],
        "checks": [
            "Settlement.settle: per-asset conservation assert (paid_out + fees == gross, exact wei)",
            "batch replay protection: _settled_batches set + mark_settling/mark_settled lifecycle",
            "claim(): pull-based; failed transfer never bricks (funds stay claimable, error recorded)",
            "settlement-asset gate enforced before any state change; fee cap + pause sequence",
        ],
        "findings": [],
        "invariant_evidence": "tests/pytest/test_p03_audit_invariants.py green (wave 20)",
    },
    "P04_MEV": {
        "modules": ["src/sincor2/defi/mev_capture.py (597 lines, read in full)"],
        "checks": [
            "LiveGate: live capture blocked until founder-signed release; release() requires non-empty marker",
            "Bidder: proceeds-funded only, min float floor, 2x gas-reserve stand-down, 3x bid shading",
            "submit_live: gate check then NotImplementedError (execution path absent by construction)",
            "sandwich/backrun detectors + SignerRegistry fail-closed construction",
        ],
        "findings": [],
        "invariant_evidence": "tests/pytest/test_p04_audit_invariants.py green (wave 21)",
    },
    "P05_INSURANCE": {
        "modules": ["src/sincor2/defi/insurance_mutual.py (516 lines, read in full)"],
        "checks": [
            "deposit_capital: 1:1 genesis share math; subsequent deposits pro-rata on reserves",
            "redeem_capital: 130% reserve-floor guard (redeem blocked on breach)",
            "buy_cover: fresh risk-score requirement; claim flow assessor quorum + slashing",
            "buy_cover reserve check; pending-payout pull pattern",
        ],
        "findings": [],
        "invariant_evidence": "tests/pytest/test_p05_audit_invariants.py green (wave 20)",
    },
    "P06_PERPS": {
        "modules": ["src/sincor2/defi/perp_hedge_swarm.py (463 lines, read in full)"],
        "checks": [
            "isolated margin only; cross-margin constructor arg rejected",
            "opens_paused when liq buffer < 1.5x; $250 min; 15% swarm-cap sizing; funding-sign gate",
            "price feed staleness raises (fail-static); conversion proof gate before live",
            "fee on realized PnL only",
        ],
        "findings": [],
        "invariant_evidence": "tests/pytest/test_p06_audit_invariants.py green (wave 20)",
    },
    "P07_BRIDGE": {
        "modules": ["src/sincor2/defi/bridge_optimizer.py (493 lines, read in full)"],
        "checks": [
            "settle(): conservation assert (payout + treasury_fee == net_out, exact wei)",
            "settle deadline enforced; idempotent on already-settled route",
            "storage-proof verification against route commitment before payout",
            "non-bricking reverting-recipient path: funds parked, pull-claimable; LiveGate release-gated",
        ],
        "findings": [],
        "invariant_evidence": "tests/pytest/test_p07_audit_invariants.py green (wave 20)",
    },
    "P08_RWA": {
        "modules": ["src/sincor2/defi/rwa_vaults.py (437 lines, read in full)"],
        "checks": [
            "ERC-4626 inflation-attack guard: DEAD_SHARES floor minted to burn address on first deposit",
            "accrue_yield: gate-open + promoted + oracle-unpaused required; 15 bps treasury fee; pro-rata over eligible supply",
            "KYC-revoked yield frozen (never seized); principal always redeemable (T+2)",
            "NAV oracle pause circuit breaker; compliance gate pack evaluation",
        ],
        "findings": [],
        "invariant_evidence": "tests/pytest/test_p08_audit_invariants.py green (wave 20)",
    },
    "P09_DAO_GOV": {
        "modules": ["src/sincor2/defi/dao_governance.py (289 lines, read in full)"],
        "checks": [
            "VoteBroadcaster.broadcast_vote always raises BroadcastBlockedError (sim-only, fail-closed)",
            "proposal execute: quorum check + 48h timelock enforced; snapshot immutable after close (StaleVoteError)",
            "bribe plan: 25% max per gauge, $10 epoch floor, 5 bps treasury fee on captured rewards",
            "net-yield computed after bribe/vote-rental/bridge-slippage/gas",
        ],
        "findings": [],
        "invariant_evidence": "tests/pytest/test_p09_audit_invariants.py green (wave 21)",
    },
    "P10_FLASH_ARB": {
        "modules": ["src/sincor2/defi/flash_arbitrage.py (391 lines, read in full)"],
        "checks": [
            "OS RUNTIME CONTRACT: scan-only; ScanOnlyGate makes any live execution path revert by construction",
            "safety dry-run aborts unsafe candidates; callback bound to provider (CallbackTheftError)",
            "money in integer cents; profit floor $50 + 0.15% notional; $25 gas ceiling; 2-block TTL",
            "settlement accounting: 30 bps treasury fee on net profit",
        ],
        "findings": [],
        "invariant_evidence": "tests/pytest/test_p10_audit_invariants.py green (wave 20)",
    },
    "P11_DELTA_NEUTRAL": {
        "modules": ["src/sincor2/defi/delta_neutral.py (235 lines, read in full)"],
        "checks": [
            "HedgeSizer validates max_ltv in (0,1); LTV-breach sizing refused",
            "net_delta_units exact by construction (lst - short); funding-flip kill switch",
            "basis-negative refused; live venue calls blocked (simulation only)",
        ],
        "findings": [],
        "invariant_evidence": "tests/pytest/test_p11_audit_invariants.py green (wave 20)",
    },
    "P12_TWAMM": {
        "modules": ["src/sincor2/defi/twamm.py (200 lines, read in full)"],
        "checks": [
            "slice schedule: min 4 slices enforced (single-block dumps rejected)",
            "impact-cap auto-extension with 512-slice ceiling; ImpactCapError when unsatisfiable",
            "integer remainder distributed to earliest slices; schedule sums exactly to parent",
            "8 bps treasury fee; LiveOrderBlocker.submit always raises",
            "re-ran wave-21 floor-bound reasoning: per-slice flooring can make "
            "total_output up to n_slices cents below dump output; fuzz held the bound",
        ],
        "findings": [
            {"severity": "low",
             "finding": "module docstring still states the slicing invariant as "
                        "absolute ('never underperforms a single dump'), but "
                        "integer per-slice flooring can make total_output up to "
                        "n_slices cents below dump output (wave-21 fuzz: 12c on "
                        "21 slices; bound held). Tighten docstring to state the "
                        "flooring bound.",
             "status": "open"},
        ],
        "invariant_evidence": "tests/pytest/test_p12_audit_invariants.py green (wave 21)",
    },
    "P13_AVS": {
        "modules": ["src/sincor2/defi/avs_tranching.py (235 lines, read in full)"],
        "checks": [
            "SlashOracle: staleness limit raises on stale reads",
            "junior-cap + cover-breach guards on allocation; slashing applies to tranches per oracle",
            "LiveRestakeBlocker: live restake raises; yield distribution pro-rata",
        ],
        "findings": [],
        "invariant_evidence": "tests/pytest/test_p13_audit_invariants.py green (wave 21)",
    },
    "P14_PREDICTION": {
        "modules": ["src/sincor2/defi/prediction_markets.py (532 lines, read in full)"],
        "checks": [
            "KellySizer: quarter-Kelly x confidence^2, hard-clamped to 10% bankroll; zero size on no edge",
            "Brier score tracking with beats-naive gate; category risk halt",
            "edge veto on insufficient confidence; stale-data rejection",
            "PolyclawWalletAdapter: ONLY live path; treasury key label hard-blocked; dry_run default",
        ],
        "findings": [],
        "invariant_evidence": "tests/pytest/test_p14_audit_invariants.py green (wave 20)",
    },
    "P15_LENDING": {
        "modules": ["src/sincor2/defi/lending_optimizer.py (692 lines, read in full)"],
        "checks": [
            "_accrue_interest: largest-remainder distribution so sum(balances) == total_supply exactly; fee 10 bps pull-claimable",
            "utilization band gate on borrow; solvency guard on withdraw (supply - withdrawn >= borrows)",
            "allowlisted venues only; live execution blocked; rehypothecation caps + liquidity buffer",
            "wallet feature model + health-factor checks",
        ],
        "findings": [
            {"severity": "low",
             "finding": "LendingPool.set_band() has no access control in the "
                        "reference model (any caller can tighten/loosen the "
                        "utilization band). Code comments mark it as an "
                        "'audited admin path in production'. Deployment must "
                        "gate this behind the admin role.",
             "status": "open"},
        ],
        "invariant_evidence": "tests/pytest/test_p15_audit_invariants.py green (wave 20)",
    },
    "P16_DEX_AGG": {
        "modules": ["src/sincor2/defi/dex_aggregator.py (503 lines, read in full)"],
        "checks": [
            "SplitOptimizer: net-out math with per-venue fee deduction; renormalization keeps legs summing to request",
            "min-out enforcement on simulated execution; allowlisted venues only",
            "live execution blocked; forecast adapter injects scores without changing math",
        ],
        "findings": [],
        "invariant_evidence": "tests/pytest/test_p16_audit_invariants.py green (wave 20)",
    },
    "P17_OPTIONS": {
        "modules": ["src/sincor2/defi/options_protocol.py (378 lines, read in full)"],
        "checks": [
            "covered-only invariant: minted <= lockedCollateral asserted on every write; NakedShortAttempt raised",
            "series OI cap 10k + per-writer cap; expiry band 7d-90d fixed tenors",
            "premium rails (floor/cap) + $1 dust floor reject bad BS quotes; stale-oracle reverts",
            "permissionless settle exactly once per series; exercise within window",
            "re-ran wave-21 float-dust reasoning: BS model can return slightly negative dust; premium rails reject it downstream (verified)",
        ],
        "findings": [
            {"severity": "info",
             "finding": "Black-Scholes float dust can be slightly negative "
                        "(wave-21 fuzz finding); premium rails reject it "
                        "downstream. No action needed; recorded for the audit trail.",
             "status": "accepted"},
        ],
        "invariant_evidence": "tests/pytest/test_p17_audit_invariants.py green (wave 21)",
    },
    "P18_STRUCTURED": {
        "modules": ["src/sincor2/defi/structured_products.py (504 lines, read in full)"],
        "checks": [
            "TrancheLedger.check_conservation on deposit; PT/YT minted 1:1 with deposit",
            "yield sources allowlisted per product; harvest non-bricking on source failure",
            "param changes behind lister role + timelock; pause gating",
            "protection sleeve math: floor x discount factor; fee exceeds-sleeve refused",
        ],
        "findings": [],
        "invariant_evidence": "tests/pytest/test_p18_audit_invariants.py green (wave 20)",
    },
    "P19_CREDIT": {
        "modules": ["src/sincor2/defi/credit_underwriting.py (367 lines, read in full)"],
        "checks": [
            "validate_ltv_table: monotonicity enforced on updates (descending score, non-decreasing LTV)",
            "score floor: below-floor scores ineligible (LTV 0); attestation freshness + registry",
            "unsecured draw refused; concentration caps; health-factor circuit actions",
            "re-ran wave-21 findings: still present as documented (see findings)",
        ],
        "findings": [
            {"severity": "low",
             "finding": "validate_ltv_table range-checks LTV only from the "
                        "second row onward; the first row's LTV is not "
                        "range-validated (wave-21 fuzz finding, still present).",
             "status": "open"},
            {"severity": "info",
             "finding": "documented 850 score ceiling is unreachable: feature "
                        "weights cap the score at 800 (wave-21 fuzz finding). "
                        "Doc mismatch only; scoring behaves as weighted.",
             "status": "accepted"},
        ],
        "invariant_evidence": "tests/pytest/test_p19_audit_invariants.py green (wave 21)",
    },
    "P20_COMPLIANCE": {
        "modules": ["src/sincor2/defi/compliance_automation.py (596 lines, read in full)"],
        "checks": [
            "KYC verify: issuer allowlist, HMAC compare_digest, nonce-replay rejection, 90d validity cap, expiry -> STALE",
            "AML screen: mixer/darknet/scam tags, hop-limit, fail-closed on unreachable provider",
            "geo registry timelock on updates; reentrancy guard pattern on deposit path",
            "HMAC is explicitly demo-only per repo policy (real attestations use secp256k1 elsewhere)",
        ],
        "findings": [
            {"severity": "low",
             "finding": "_consumed_nonces is per-process memory: nonce-replay "
                        "protection does not survive a process restart. "
                        "Production must back this with durable storage "
                        "(wave-15 shared-state interface).",
             "status": "open"},
        ],
        "invariant_evidence": "tests/pytest/test_p20_audit_invariants.py green (wave 20)",
    },
    "P21_TREASURY_DAO": {
        "modules": ["src/sincor2/defi/treasury_dao.py (308 lines, read in full)"],
        "checks": [
            "Executor.broadcast always raises BroadcastForbidden; even with EXECUTE_LIVE_ENV only reviewed+approved holds dispatch to an approved external executor",
            "publish: review required; unreviewed holds > 72h raise StaleHold; content-id verified against payload",
            "RiskOfficer band validation on every publish; 8 bps fee on positive realized yield only",
        ],
        "findings": [],
        "invariant_evidence": "tests/pytest/test_p21_audit_invariants.py green (wave 20)",
    },
    "P22_STABLE_YIELD": {
        "modules": ["src/sincor2/defi/p22/*.py (optimizer, gate, twap, scanner, adapters, fees, interfaces; read in full)"],
        "checks": [
            "optimize(): per-venue caps on allocation; verify_plan re-checks conservation",
            "rotation decisions require threshold breach; gate module blocks live moves",
            "TWAP scanner fixture-injected; fee accounting to treasury",
        ],
        "findings": [],
        "invariant_evidence": "tests/pytest/test_p22_audit_invariants.py green (wave 20)",
    },
    "P23_NFTFI": {
        "modules": ["src/sincor2/defi/p23/*.py (vault, registry, oracle, manager, fees, live_block; read in full)"],
        "checks": [
            "live_block.guard_live raises on every live-intent entrypoint (live-blocked by catalog design)",
            "FractionalVault.deposit: ERC-4626 inflation-attack guard (minimum shares to burn address on first deposit)",
            "oracle deposits-frozen + no-price paths raise; collection allowlist required",
            "lending sleeve utilization-capped; absorb_default books losses; epoch snapshots",
        ],
        "findings": [],
        "invariant_evidence": "tests/pytest/test_p23_audit_invariants.py green (wave 20)",
    },
    "P24_SOCIALFI": {
        "modules": ["src/sincor2/defi/p24/*.py (factory, curve, policy, accrual, split, fees, onboarding, live_block, primitives; read in full)"],
        "checks": [
            "factory.issue: requires screened=True (content-policy screen); name/symbol required; symbol uniqueness",
            "transfer_creator_tokens: only vested tokens move (transferable_wei)",
            "curve: exact-sum marginal pricing on buy/sell; inventory cap 500M; one-way graduation",
            "fee split + revenue accrual mirror; live_block by catalog design",
        ],
        "findings": [],
        "invariant_evidence": "tests/pytest/test_p24_audit_invariants.py green (wave 20)",
    },
    "P25_PORTFOLIO": {
        "modules": ["src/sincor2/defi/p25/*.py (allocator, rebalancer, guards, risk, ingestor, fees, api; read in full)"],
        "checks": [
            "guards.require_cash_floor: rebalance refused below cash floor",
            "plan_rebalance: delta trades sized within risk bands; no leverage beyond configured max",
            "ingestor fixture-injected; risk module drawdown circuit",
        ],
        "findings": [],
        "invariant_evidence": "tests/pytest/test_p25_audit_invariants.py green (wave 20)",
    },
    "P26_DEFI_OS": {
        "modules": ["src/sincor2/defi/p26/*.py (ranker, killswitch, toa_loop, proof_hooks, registry_wrap, telemetry, api; read in full)"],
        "checks": [
            "ranker: reads the proof ledger by canonical SKU (gate_stage_evidence); ranks on real evidence only",
            "killswitch: ROI/loss thresholds trigger kill decisions with reason records",
            "TOA feedback loop appends telemetry; proof_hooks record evidence entries",
            "no self-modification of product code; registry wrapper is read-only over catalog",
        ],
        "findings": [],
        "invariant_evidence": "tests/pytest/test_p26_audit_invariants.py green (wave 21)",
    },
}

METHOD = (
    "internal manual review by xioix/buildout-28-audit-artifacts (2026-09-30): "
    "each product module read in full; checklist: money math (conservation, "
    "integer dust, share issuance), access control / live-block enforcement, "
    "input validation, replay and double-spend guards, error handling, and "
    "consistency with documented load-bearing invariants. Cross-checked "
    "against the wave-20/wave-21 invariant and fuzz suites already on record "
    "in the proof ledger. NOT a CertiK Token Scan, NOT a Skynet score, NOT a "
    "formal third-party audit."
)

ledger = ProofLedger()
print(f"appending to {ledger.path} (base commit {COMMIT})\n")

for pid in sorted(AUDITS):
    a = AUDITS[pid]
    sku = mint_sku(pid)
    open_critical = sum(1 for f in a["findings"] if f["severity"] == "critical")
    entry = ledger.append(
        sku=sku,
        kind="audit_report",
        details={
            "report_type": "internal_review",
            "not_a_third_party_audit": True,
            "scope": a["modules"],
            "method": METHOD,
            "checks": a["checks"],
            "findings": a["findings"],
            "open_critical": open_critical,
            "open_high": sum(1 for f in a["findings"] if f["severity"] == "high"),
            "open_low_or_info": sum(
                1 for f in a["findings"]
                if f["severity"] in ("low", "info") and f["status"] == "open"
            ),
            "invariant_evidence": a["invariant_evidence"],
            "gate_decision": (
                "stays in test: audit clean (0 open criticals); product-stage "
                "promotion still requires invariant/fuzz evidence, fork-sim "
                "evidence, and founder live-block decisions per the lifecycle gates"
            ),
            "note": a.get("note", ""),
        },
        commit=COMMIT,
        recorded_by=RECORDED_BY,
    )
    nf = len(a["findings"])
    print(f"{sku}: {entry['entry_id']} findings={nf} open_critical={open_critical}")

# verify the audit->product gate is evaluable for every product
from sincor2.defi.gates import _check_audit_report  # noqa: E402
from sincor2.defi.products import PROTOCOL_BY_ID  # noqa: E402
from sincor2.defi import gates as gates_mod  # noqa: E402

print("\ngate check (_check_audit_report per product):")
root = "."
all_ok = True
for pid in sorted(AUDITS):
    sku = mint_sku(pid)
    product = {"sku": sku, "protocol_id": pid}
    res = _check_audit_report(product, root, ledger)
    ok = bool(res.ok)
    all_ok = all_ok and ok
    print(f"  {sku}: {'PASS' if ok else 'FAIL'} — {res.reason}")
print(f"\n{'ALL 26 AUDIT GATES EVALUABLE AND PASSING' if all_ok else 'GATE FAILURES PRESENT'}")
