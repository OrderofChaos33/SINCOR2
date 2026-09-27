"""Product registry for the SINCOR Speculative DeFi arm.

One arm, 26 products, each minted a SKU from the canonical catalog — single
source of truth, no drift. Lifecycle: spec -> build -> test -> audit ->
product -> catalog, enforced by gates.py. All 26 start at ``spec`` except P01,
which has real strategy math in the repo (src/sincor2/defi/yield_aggregator.py)
and starts at ``build``.

CLI:
    python -m sincor2.defi.products status
    python -m sincor2.defi.products gates <SKU>
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import division
from .catalog import PROTOCOL_BY_ID, PROTOCOLS, assert_catalog_complete
from .gates import STAGES, evaluate, next_stage
from .pricing import STATUS_DRAFT

REPO_ROOT = str(Path(__file__).resolve().parents[3])

DIVISION = division.DIVISION_ID

SKU_PREFIX = "SINCOR-DEFI"

SLUGS: Dict[str, str] = {
    "P01_YIELD_AGG": "VAULT",
    "P02_CLMM": "CLMM",
    "P03_INTENT_DARK": "DARKPOOL",
    "P04_MEV": "MEV",
    "P05_INSURANCE": "MUTUAL",
    "P06_PERPS": "PERPS",
    "P07_BRIDGE": "BRIDGE",
    "P08_RWA": "RWA",
    "P09_DAO_GOV": "GOV",
    "P10_FLASH_ARB": "FLASHARB",
    "P11_DELTA_NEUTRAL": "DELTANEUTRAL",
    "P12_TWAMM": "TWAMM",
    "P13_AVS": "AVS",
    "P14_PREDICTION": "PREDICT",
    "P15_LENDING": "LEND",
    "P16_DEX_AGG": "AGG",
    "P17_OPTIONS": "OPTIONS",
    "P18_STRUCTURED": "STRUCTURED",
    "P19_CREDIT": "CREDIT",
    "P20_COMPLIANCE": "COMPLY",
    "P21_TREASURY_DAO": "TREASURY",
    "P22_STABLE_YIELD": "STABLEYIELD",
    "P23_NFTFI": "NFTFI",
    "P24_SOCIALFI": "SOCIALFI",
    "P25_PORTFOLIO": "PORTFOLIO",
    "P26_DEFI_OS": "DEFIOS",
}

# Products with code evidence justifying a head start. P01's yield aggregator
# is real, tested strategy math (see src/sincor2/defi/yield_aggregator.py).
INITIAL_STAGE_OVERRIDES: Dict[str, str] = {
    "P01_YIELD_AGG": "build",
}


def mint_sku(protocol_id: str) -> str:
    spec = PROTOCOL_BY_ID[protocol_id]
    return f"{SKU_PREFIX}-P{spec.swarm_id:02d}-{SLUGS[protocol_id]}"


def default_state_path() -> str:
    from .proof_ledger import default_data_dir
    return os.path.join(default_data_dir(), "products_state.json")


def _load_state(path: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    path = path or default_state_path()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state: Dict[str, Dict[str, str]], path: Optional[str] = None) -> None:
    path = path or default_state_path()
    tmp = path + ".tmp"
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, path)


def build_registry(state_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """All 26 products, merged with persisted stage/status overrides."""
    assert_catalog_complete()
    state = _load_state(state_path)
    products = []
    for spec in PROTOCOLS:
        sku = mint_sku(spec.protocol_id)
        saved = state.get(sku, {})
        products.append({
            "sku": sku,
            "protocol_id": spec.protocol_id,
            "name": spec.name,
            "division": DIVISION,
            "stage": saved.get("stage", INITIAL_STAGE_OVERRIDES.get(spec.protocol_id, "spec")),
            "version": saved.get("version", "0.1.0"),
            "pricing_status": saved.get("pricing_status", STATUS_DRAFT),
            "marketing_status": saved.get("marketing_status", "not-started"),
        })
    return products


def get_product(sku: str, state_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    for p in build_registry(state_path):
        if p["sku"] == sku:
            return p
    return None


def _bump_patch(version: str) -> str:
    parts = version.split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except ValueError:
        parts.append("1")
    return ".".join(parts)


def promote(sku: str, to_stage: str, ledger=None,
            root: Optional[str] = None,
            state_path: Optional[str] = None) -> Dict[str, Any]:
    """Promote a product one stage forward. Refuses with reasons on any failure."""
    from .proof_ledger import ProofLedger
    product = get_product(sku, state_path)
    if product is None:
        return {"ok": False, "sku": sku, "to_stage": to_stage,
                "reasons": [{"check": "known_sku", "ok": False,
                             "reason": f"unknown SKU: {sku}", "evidence": "unverified"}]}
    ledger = ledger or ProofLedger()
    result = evaluate(product, to_stage, ledger, root or REPO_ROOT)
    if not result.ok:
        return {"ok": False, "sku": sku, "to_stage": to_stage,
                "reasons": [r.__dict__ for r in result.reasons]}
    state = _load_state(state_path)
    saved = state.get(sku, {})
    saved["stage"] = to_stage
    saved["version"] = _bump_patch(product["version"])
    state[sku] = saved
    _save_state(state, state_path)
    return {"ok": True, "sku": sku, "to_stage": to_stage,
            "version": saved["version"], "reasons": []}


def approve_marketing(sku: str, state_path: Optional[str] = None) -> Dict[str, Any]:
    """Approve marketing copy. Refused before the product stage: no copy is
    written until ``product``, and no copy may claim liveness until ``catalog``."""
    product = get_product(sku, state_path)
    if product is None:
        return {"ok": False, "reason": f"unknown SKU: {sku}"}
    if product["stage"] not in ("product", "catalog"):
        return {"ok": False,
                "reason": f"marketing approves at product stage; {sku} is at "
                          f"'{product['stage']}'"}
    state = _load_state(state_path)
    saved = state.get(sku, {})
    saved["marketing_status"] = "approved"
    state[sku] = saved
    _save_state(state, state_path)
    return {"ok": True, "reason": "marketing approved"}


# Fill the division swarm->SKU roster once SKUs are minted.
for _spec in PROTOCOLS:
    division.SWARM_TO_SKU[_spec.swarm_id] = mint_sku(_spec.protocol_id)


# -- CLI ---------------------------------------------------------------
def _table(products: List[Dict[str, Any]], ledger, root: str) -> str:
    from .compliance_score import score_product
    lines = []
    header = f"{'SKU':28} {'STAGE':8} {'SCORE':6} {'PRICING':8} {'MARKETING':11} PROOFS  NAME"
    lines.append(header)
    lines.append("-" * len(header))
    for p in products:
        score = score_product(p, ledger=ledger, root=root)["score"]
        n_proofs = ledger.count(sku=p["sku"])
        lines.append(
            f"{p['sku']:28} {p['stage']:8} {score:5.1f}  "
            f"{p['pricing_status']:8} {p['marketing_status']:11} {n_proofs:6d}  {p['name']}"
        )
    lines.append("")
    lines.append(f"{len(products)} products — division '{DIVISION}' status: "
                 f"{division.DIVISION_STATUS.upper()} (under testing, zero live products)")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    from .proof_ledger import ProofLedger
    argv = list(argv if argv is not None else sys.argv[1:])
    ledger = ProofLedger()
    if not argv or argv[0] == "status":
        print(_table(build_registry(), ledger, REPO_ROOT))
        return 0
    if argv[0] == "gates":
        if len(argv) < 2:
            print("usage: python -m sincor2.defi.products gates <SKU>", file=sys.stderr)
            return 2
        sku = argv[1]
        product = get_product(sku)
        if product is None:
            print(f"unknown SKU: {sku}", file=sys.stderr)
            return 2
        nxt = next_stage(product["stage"])
        if nxt is None:
            print(f"{sku} is at final stage 'catalog'; nothing blocks.")
            return 0
        result = evaluate(product, nxt, ledger, REPO_ROOT)
        print(f"{sku} ({product['name']}) at '{product['stage']}' -> '{nxt}': "
              f"{'PASS' if result.ok else 'BLOCKED'}")
        for r in result.reasons:
            mark = "ok  " if r.ok else "FAIL"
            print(f"  [{mark}] {r.check}: {r.reason} (evidence: {r.evidence})")
        return 0 if result.ok else 1
    print(f"unknown command: {argv[0]} (status | gates <SKU>)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
