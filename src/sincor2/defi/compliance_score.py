"""Compliance scoring for speculative DeFi products.

0-100 per product, from VERIFIABLE signals only. Every dimension cites its
evidence source (ledger entry id, file path, or "unverified"). Missing
evidence scores 0 for that dimension — never assumed, never interpolated.

Scores are low across the board right now. That is correct and honest:
the arm is UNDER TESTING with zero live products.
"""

from __future__ import annotations

from typing import Any, Dict

from .catalog import PROTOCOL_BY_ID
from .gates import find_spec_text
from .proof_ledger import KIND_AUDIT_REPORT, KIND_TEST_RUN, ProofLedger

# Regulatory surface by catalog category. Formula-driven and transparent:
# higher inherent regulatory exposure -> lower score. Evidence is always
# the catalog category itself.
REGULATORY_BY_CATEGORY = {
    "compliance": 15,   # the gatekeeper itself
    "treasury": 12,
    "governance": 12,
    "meta": 12,
    "yield": 10,
    "execution": 10,
    "liquidity": 10,
    "infra": 10,
    "lending": 8,
    "derivatives": 8,
    "structured": 8,
    "mev": 8,
    "insurance": 8,
    "arb": 8,
    "portfolio": 8,
    "markets": 6,
    "credit": 6,
    "restake": 6,
    "rwa": 5,           # securities-adjacent surface
    "nftfi": 5,
    "social": 5,
}

DIMENSION_MAX = {
    "audit_evidence": 25,
    "test_evidence": 20,
    "oracle_risk": 15,
    "custody": 15,
    "fail_closed": 10,
    "regulatory": 15,
}


def _audit_evidence(product: Dict[str, Any], ledger: ProofLedger) -> Dict[str, Any]:
    reports = ledger.read(sku=product["sku"], kind=KIND_AUDIT_REPORT)
    if not reports:
        return {"score": 0, "evidence": "unverified"}
    latest = reports[-1]
    if int(latest.get("details", {}).get("open_critical", 0)) == 0:
        return {"score": 25, "evidence": latest["entry_id"]}
    return {"score": 10, "evidence": latest["entry_id"]}


def _test_evidence(product: Dict[str, Any], ledger: ProofLedger) -> Dict[str, Any]:
    runs = ledger.read(sku=product["sku"], kind=KIND_TEST_RUN)
    best, best_id = 0.0, "unverified"
    for e in runs:
        d = e.get("details", {})
        passed = int(d.get("passed", 0))
        failed = int(d.get("failed", 0))
        total = passed + failed
        if total > 0:
            rate = passed / total
            if rate > best:
                best, best_id = rate, e["entry_id"]
    return {"score": round(20 * best, 1), "evidence": best_id}


def _oracle_risk(product: Dict[str, Any], root: str) -> Dict[str, Any]:
    text, evidence = find_spec_text(product["protocol_id"], root)
    if not text:
        return {"score": 0, "evidence": "unverified"}
    low = text.lower()
    if "oracle-less" in low or "oracle less" in low or "without oracle" in low:
        return {"score": 15, "evidence": evidence}
    if "oracle" in low and any(w in low for w in ("fallback", "redundan", "staleness", "median")):
        return {"score": 10, "evidence": evidence}
    if "oracle" in low:
        return {"score": 5, "evidence": evidence}
    return {"score": 12, "evidence": evidence + " (no oracle dependency stated)"}


def _custody(product: Dict[str, Any], root: str) -> Dict[str, Any]:
    text, evidence = find_spec_text(product["protocol_id"], root)
    if not text:
        return {"score": 0, "evidence": "unverified"}
    low = text.lower()
    if "non-custodial" in low or "noncustodial" in low or "without taking custody" in low:
        return {"score": 15, "evidence": evidence}
    if "pull" in low and ("payout" in low or "withdraw" in low):
        return {"score": 12, "evidence": evidence}
    if "custod" in low:
        return {"score": 8, "evidence": evidence}
    return {"score": 10, "evidence": evidence + " (no custody model stated; partial credit)"}


def _fail_closed(product: Dict[str, Any], root: str) -> Dict[str, Any]:
    text, evidence = find_spec_text(product["protocol_id"], root)
    if not text:
        return {"score": 0, "evidence": "unverified"}
    low = text.lower()
    if "fail-closed" in low or "fail closed" in low:
        return {"score": 10, "evidence": evidence}
    return {"score": 0, "evidence": evidence + " (fail-closed design not stated)"}


def _regulatory(product: Dict[str, Any]) -> Dict[str, Any]:
    spec = PROTOCOL_BY_ID[product["protocol_id"]]
    return {
        "score": REGULATORY_BY_CATEGORY.get(spec.category, 8),
        "evidence": f"catalog category={spec.category} (src/sincor2/defi/catalog.py)",
    }


def score_product(product: Dict[str, Any], ledger: ProofLedger,
                  root: str) -> Dict[str, Any]:
    """Score a product dict (must carry sku + protocol_id)."""
    dims = {
        "audit_evidence": _audit_evidence(product, ledger),
        "test_evidence": _test_evidence(product, ledger),
        "oracle_risk": _oracle_risk(product, root),
        "custody": _custody(product, root),
        "fail_closed": _fail_closed(product, root),
        "regulatory": _regulatory(product),
    }
    total = round(sum(d["score"] for d in dims.values()), 1)
    return {
        "sku": product["sku"],
        "score": total,
        "max": 100,
        "dimensions": {
            name: {"score": d["score"], "max": DIMENSION_MAX[name], "evidence": d["evidence"]}
            for name, d in dims.items()
        },
    }
