"""Stage gates for the speculative DeFi product pipeline.

Lifecycle order is strict: spec -> build -> test -> audit -> product -> catalog.
No stage may be skipped, and no gate may be bypassed by flag, env var, or
operator override — a refusal is a refusal until its evidence exists.

Evidence is verified from repo files and the proof ledger where possible.
Anything that cannot be verified from repo evidence is an explicit
manual-checklist item in the refusal reasons.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import division
from .catalog import PROTOCOL_BY_ID
from .proof_ledger import (
    KIND_AUDIT_REPORT,
    KIND_FORK_SIM,
    KIND_INVARIANT_TEST,
    KIND_TEST_RUN,
    ProofLedger,
)

STAGES: Tuple[str, ...] = ("spec", "build", "test", "audit", "product", "catalog")

# Auction-task build phases mapped onto lifecycle stages. The mapping lives
# here (not in the task JSON files): completing a project's task phases for
# the mapped stages is what makes a product *eligible* for promotion — the
# gate evidence is still required.
STAGE_MAP: Dict[str, str] = {
    "spec": "spec",
    "scaffold": "build",
    "core": "build",
    "testing": "test",
    "audit": "audit",
    "docs": "product",
    "deploy": "product",
}

# protocol_id -> repo-relative implementation path. P01 (yield_aggregator),
# P04 (mev_capture), P05 (insurance_mutual), P06 (perp_hedge_swarm),
# P08 (rwa_vaults), P09 (dao_governance), P10 (flash_arbitrage),
# P11 (delta_neutral), P12 (twamm), P13 (avs_tranching),
# P14 (prediction_markets) have real strategy math; everything else is
# unimplemented until its build-phase tasks land.
IMPLEMENTATIONS: Dict[str, str] = {
    "P01_YIELD_AGG": os.path.join("src", "sincor2", "defi", "yield_aggregator.py"),
"P02_CLMM": os.path.join("src", "sincor2", "defi", "clmm_manager.py"),
    "P03_INTENT_DARK": os.path.join("src", "sincor2", "defi", "intent_dark_pool.py"),
    "P04_MEV": os.path.join("src", "sincor2", "defi", "mev_capture.py"),
    "P05_INSURANCE": os.path.join("src", "sincor2", "defi", "insurance_mutual.py"),
    "P06_PERPS": os.path.join("src", "sincor2", "defi", "perp_hedge_swarm.py"),
    "P08_RWA": os.path.join("src", "sincor2", "defi", "rwa_vaults.py"),
    "P09_DAO_GOV": os.path.join("src", "sincor2", "defi", "dao_governance.py"),
    "P10_FLASH_ARB": os.path.join("src", "sincor2", "defi", "flash_arbitrage.py"),
    "P11_DELTA_NEUTRAL": os.path.join("src", "sincor2", "defi", "delta_neutral.py"),
    "P12_TWAMM": os.path.join("src", "sincor2", "defi", "twamm.py"),
    "P13_AVS": os.path.join("src", "sincor2", "defi", "avs_tranching.py"),
    "P14_PREDICTION": os.path.join("src", "sincor2", "defi", "prediction_markets.py"),
    "P16_DEX_AGG": os.path.join("src", "sincor2", "defi", "dex_aggregator.py"),
    "P17_OPTIONS": os.path.join("src", "sincor2", "defi", "options_protocol.py"),
    "P19_CREDIT": os.path.join("src", "sincor2", "defi", "credit_underwriting.py"),
}

# protocol_id -> auction spec filename stem (~/workspace/sincor2-auction-tasks/specs/)
AUCTION_SPEC_STEMS: Dict[str, str] = {
    "P01_YIELD_AGG": "p01-yield-aggregator",
    "P02_CLMM": "p02-clmm-manager",
    "P03_INTENT_DARK": "p03-intent-dark-pool",
    "P04_MEV": "p04-mev-capture",
    "P05_INSURANCE": "p05-defi-insurance",
    "P06_PERPS": "p06-perp-dex",
    "P07_BRIDGE": "p07-bridge-optimizer",
    "P08_RWA": "p08-rwa-vaults",
    "P09_DAO_GOV": "p09-dao-governance",
    "P10_FLASH_ARB": "p10-flash-arbitrage",
    "P11_DELTA_NEUTRAL": "p11-delta-neutral",
    "P12_TWAMM": "p12-twamm-engine",
    "P13_AVS": "p13-avs-restaking",
    "P14_PREDICTION": "p14-prediction-markets",
    "P15_LENDING": "p15-lending-optimizer",
    "P16_DEX_AGG": "p16-dex-aggregator",
    "P17_OPTIONS": "p17-options-protocol",
    "P18_STRUCTURED": "p18-structured-products",
    "P19_CREDIT": "p19-credit-underwriting",
    "P20_COMPLIANCE": "p20-compliance-automation",
    "P21_TREASURY_DAO": "p21-treasury-dao",
    "P22_STABLE_YIELD": "p22-stablecoin-yield",
    "P23_NFTFI": "p23-nftfi-pools",
    "P24_SOCIALFI": "p24-socialfi-revenue",
    "P25_PORTFOLIO": "p25-agent-portfolio",
    "P26_DEFI_OS": "p26-defi-os",
}

AUCTION_TASKS_DIR = os.path.expanduser("~/workspace/sincor2-auction-tasks")


@dataclass
class CheckResult:
    check: str
    ok: bool
    reason: str
    evidence: str = "unverified"


@dataclass
class GateResult:
    ok: bool
    reasons: List[CheckResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "reasons": [r.__dict__ for r in self.reasons],
        }


def next_stage(stage: str) -> Optional[str]:
    try:
        idx = STAGES.index(stage)
    except ValueError:
        return None
    return STAGES[idx + 1] if idx + 1 < len(STAGES) else None


# Distinctive keywords locating each core build's deep spec inside
# docs/DEFI_PROJECTS_COORDINATION.md. (The 16 expansion builds carry their
# own spec files under ~/workspace/sincor2-auction-tasks/specs/.)
# Note: XB-LOOP's ve(3,3) bribe-recycling content is the deep spec driving
# P09's auction tasks (established mapping from the task-breakdown work).
SPEC_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "P02_CLMM": ("just in time", "jit protection"),
    "P03_INTENT_DARK": ("dark water", "dark pool"),
    "P07_BRIDGE": ("a-sinc", "storage proof"),
    "P09_DAO_GOV": ("xb-loop", "bribe"),
    "P11_DELTA_NEUTRAL": ("delta-neutral", "lst/short"),
    "P12_TWAMM": ("twammi", "twamm"),
    "P13_AVS": ("davs pro", "avs slashing"),
    "P15_LENDING": ("suc-loops", "under-collateralized"),
    "P18_STRUCTURED": ("ypt", "principal token", "pendle"),
    "P26_DEFI_OS": ("self-improving",),
}


def find_spec_evidence(protocol_id: str, root: str) -> Tuple[bool, str]:
    """Locate a deep spec for the protocol. Returns (found, evidence_path_or_note)."""
    spec = PROTOCOL_BY_ID[protocol_id]
    candidates: List[str] = []
    coord = os.path.join(root, "docs", "DEFI_PROJECTS_COORDINATION.md")
    if os.path.exists(coord):
        try:
            with open(coord, encoding="utf-8") as fh:
                text = fh.read().lower()
            keywords = SPEC_KEYWORDS.get(protocol_id, (spec.name.lower(),))
            if any(k in text for k in keywords):
                candidates.append(coord)
        except OSError:
            pass
    deep = os.path.join(root, "docs", "DEFI_16_DEEP_SPECS.md")
    if os.path.exists(deep):
        try:
            with open(deep, encoding="utf-8") as fh:
                if spec.name.lower() in fh.read().lower():
                    candidates.append(deep)
        except OSError:
            pass
    stem = AUCTION_SPEC_STEMS.get(protocol_id, "")
    if stem:
        for path in glob.glob(os.path.join(AUCTION_TASKS_DIR, "specs", stem + ".md")):
            candidates.append(path)
    if candidates:
        return True, "; ".join(candidates)
    return False, "unverified"


def find_spec_text(protocol_id: str, root: str) -> Tuple[str, str]:
    """Return (spec_text, evidence_path). Empty text when unverified."""
    found, evidence = find_spec_evidence(protocol_id, root)
    if not found:
        return "", "unverified"
    first = evidence.split("; ")[0]
    try:
        with open(first, encoding="utf-8") as fh:
            return fh.read(), first
    except OSError:
        return "", "unverified"


def _passing_entries(ledger: ProofLedger, sku: str, kind: str) -> List[Dict[str, Any]]:
    out = []
    for e in ledger.read(sku=sku, kind=kind):
        d = e.get("details", {})
        if d.get("failed", 0) == 0 and d.get("passed", 0) > 0:
            out.append(e)
    return out


# -- per-transition checks -------------------------------------------------
def _check_spec_exists(product: Dict[str, Any], root: str, ledger: ProofLedger) -> CheckResult:
    found, evidence = find_spec_evidence(product["protocol_id"], root)
    return CheckResult(
        check="spec_exists",
        ok=found,
        reason="deep spec on file" if found else "no deep spec found for protocol",
        evidence=evidence,
    )


def _check_implementation_present(product: Dict[str, Any], root: str,
                                  ledger: ProofLedger) -> CheckResult:
    rel = IMPLEMENTATIONS.get(product["protocol_id"])
    if not rel:
        return CheckResult("implementation_present", False,
                           "no implementation recorded for protocol", "unverified")
    path = os.path.join(root, rel)
    ok = os.path.exists(path)
    return CheckResult("implementation_present", ok,
                       "implementation present" if ok else f"missing: {rel}",
                       path if ok else "unverified")


def _check_unit_tests_passing(product: Dict[str, Any], root: str,
                               ledger: ProofLedger) -> CheckResult:
    runs = _passing_entries(ledger, product["sku"], KIND_TEST_RUN)
    if runs:
        return CheckResult("unit_tests_passing", True,
                           f"{len(runs)} passing test-run(s) in proof ledger",
                           runs[-1]["entry_id"])
    return CheckResult("unit_tests_passing", False,
                       "no passing unit-test evidence in proof ledger "
                       "(manual: run the protocol's test suite and record a test_run entry)",
                       "unverified")


def _check_invariant_tests(product: Dict[str, Any], root: str,
                            ledger: ProofLedger) -> CheckResult:
    runs = _passing_entries(ledger, product["sku"], KIND_INVARIANT_TEST)
    if runs:
        return CheckResult("invariant_tests", True,
                           f"{len(runs)} passing invariant/fuzz run(s)",
                           runs[-1]["entry_id"])
    return CheckResult("invariant_tests", False,
                       "no invariant/fuzz evidence in proof ledger", "unverified")


def _check_fork_sim(product: Dict[str, Any], root: str,
                    ledger: ProofLedger) -> CheckResult:
    runs = _passing_entries(ledger, product["sku"], KIND_FORK_SIM)
    if runs:
        return CheckResult("fork_sim", True,
                           f"{len(runs)} passing fork-simulation run(s)",
                           runs[-1]["entry_id"])
    return CheckResult("fork_sim", False,
                       "no fork-simulation evidence in proof ledger", "unverified")


def _check_audit_report(product: Dict[str, Any], root: str,
                         ledger: ProofLedger) -> CheckResult:
    reports = ledger.read(sku=product["sku"], kind=KIND_AUDIT_REPORT)
    if not reports:
        return CheckResult("audit_report", False,
                           "no audit report in proof ledger", "unverified")
    latest = reports[-1]
    open_critical = int(latest.get("details", {}).get("open_critical", 0))
    if open_critical > 0:
        return CheckResult("audit_report", False,
                           f"audit report has {open_critical} open critical finding(s)",
                           latest["entry_id"])
    return CheckResult("audit_report", True, "audit report clean (0 open criticals)",
                       latest["entry_id"])


def _check_not_live_blocked(product: Dict[str, Any], root: str,
                             ledger: ProofLedger) -> CheckResult:
    spec = PROTOCOL_BY_ID[product["protocol_id"]]
    if spec.live_blocked:
        return CheckResult("not_live_blocked", False,
                           "protocol is live-blocked in catalog metadata; "
                           "cannot become a product until unblocked by founder + re-audit",
                           "src/sincor2/defi/catalog.py")
    return CheckResult("not_live_blocked", True, "not live-blocked",
                       "src/sincor2/defi/catalog.py")


def _check_pricing_live(product: Dict[str, Any], root: str,
                         ledger: ProofLedger) -> CheckResult:
    ok = product.get("pricing_status") == "live"
    return CheckResult("pricing_live", ok,
                       "pricing live" if ok else "pricing still draft",
                       "product registry state")


def _check_marketing_approved(product: Dict[str, Any], root: str,
                               ledger: ProofLedger) -> CheckResult:
    ok = product.get("marketing_status") == "approved"
    return CheckResult("marketing_approved", ok,
                       "marketing copy approved" if ok else "marketing copy not approved",
                       "product registry state")


def _check_compliance_threshold(product: Dict[str, Any], root: str,
                                 ledger: ProofLedger) -> CheckResult:
    from .compliance_score import score_product  # lazy: compliance imports this module
    result = score_product(product, ledger=ledger, root=root)
    score = result["score"]
    ok = score >= 70
    return CheckResult("compliance_gte_70", ok,
                       f"compliance score {score:.1f} {'>=' if ok else '<'} 70",
                       "compliance_score.py")


def _check_proof_ledger_complete(product: Dict[str, Any], root: str,
                                  ledger: ProofLedger) -> CheckResult:
    kinds = {e.get("kind") for e in ledger.read(sku=product["sku"])}
    need = {KIND_TEST_RUN, KIND_AUDIT_REPORT}
    missing = need - kinds
    if missing:
        return CheckResult("proof_ledger_complete", False,
                           f"proof ledger missing: {sorted(missing)}", "unverified")
    return CheckResult("proof_ledger_complete", True,
                       "proof ledger holds test + audit evidence",
                       f"{ledger.count(sku=product['sku'])} entries")


TRANSITION_CHECKS = {
    ("spec", "build"): [_check_spec_exists],
    ("build", "test"): [_check_implementation_present, _check_unit_tests_passing],
    ("test", "audit"): [_check_invariant_tests, _check_fork_sim],
    ("audit", "product"): [_check_audit_report, _check_not_live_blocked],
    ("product", "catalog"): [_check_pricing_live, _check_marketing_approved,
                              _check_compliance_threshold, _check_proof_ledger_complete],
}


def evaluate(product: Dict[str, Any], to_stage: str, ledger: ProofLedger,
             root: str) -> GateResult:
    """Evaluate the gate for product -> to_stage. Machine-readable refusals."""
    reasons: List[CheckResult] = []
    expected = next_stage(product.get("stage", ""))
    if to_stage not in STAGES:
        reasons.append(CheckResult("valid_stage", False,
                                   f"unknown stage: {to_stage}", "unverified"))
        return GateResult(False, reasons)
    if to_stage != expected:
        reasons.append(CheckResult(
            "no_skip", False,
            f"stages are strictly ordered: from '{product.get('stage')}' the only "
            f"allowed next stage is '{expected}', not '{to_stage}'",
            "gates.py STAGES"))
        return GateResult(False, reasons)
    checks = TRANSITION_CHECKS.get((product["stage"], to_stage), [])
    for fn in checks:
        reasons.append(fn(product, root, ledger))
    return GateResult(ok=all(r.ok for r in reasons), reasons=reasons)
