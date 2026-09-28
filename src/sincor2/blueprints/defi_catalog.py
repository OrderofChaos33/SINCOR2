"""DeFi product-arm catalog (read-only).

Surfaces the 26-SKU speculative DeFi product arm in the unified console:

  GET /catalog          -> catalog page (data: GET /api/defi/catalog)
  GET /api/defi/catalog -> registry JSON (all 26 SKUs; ?stage= filters)

Presentation only: no money paths, no auth changes, no task-state or
pool-ledger writes. Compliance scores are computed live from
compliance_score.py against the real proof ledger; evidence counts come
straight from proof_ledger.json. Spec-stage SKUs render as specs -- no
"live" badges, no APY/TVL claims (the catalog declares no such fields).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

defi_catalog_bp = Blueprint("defi_catalog", __name__)

STAGES = ("spec", "build", "test", "audit", "product", "catalog")


def _catalog_rows():
    """Assemble the 26-SKU view model from the real registry + ledger."""
    from sincor2.defi import catalog as arm_catalog
    from sincor2.defi.compliance_score import score_product
    from sincor2.defi.products import REPO_ROOT, build_registry
    from sincor2.defi.proof_ledger import ProofLedger

    ledger = ProofLedger()
    by_sku = {}
    for entry in ledger._entries:
        by_sku.setdefault(entry.get("sku"), []).append(entry)

    rows = []
    for product in build_registry():
        spec = arm_catalog.PROTOCOL_BY_ID.get(product["protocol_id"])
        try:
            score = score_product(product, ledger=ledger, root=REPO_ROOT)["score"]
        except Exception:
            score = None
        evidence = [
            {
                "entry_id": e.get("entry_id"),
                "kind": e.get("kind"),
                "timestamp": e.get("timestamp"),
                "suite": (e.get("details") or {}).get("suite"),
                "recorded_by": e.get("recorded_by"),
            }
            for e in by_sku.get(product["sku"], [])
        ]
        rows.append({
            "sku": product["sku"],
            "name": product["name"],
            "protocol_id": product["protocol_id"],
            "category": spec.category if spec else "—",
            "stage": product.get("stage") or "spec",
            "version": product.get("version") or "—",
            "compliance_score": score,
            "evidence_count": len(evidence),
            "evidence": evidence,
            "pricing_status": product.get("pricing_status") or "—",
        })
    rows.sort(key=lambda r: (STAGES.index(r["stage"]) if r["stage"] in STAGES else 99,
                             r["sku"]))
    return rows


@defi_catalog_bp.get("/catalog")
def catalog_page():
    """DeFi catalog - the 26-SKU product arm with lifecycle stages."""
    return render_template("catalog.html", nav_active="catalog")


@defi_catalog_bp.get("/api/defi/catalog")
def catalog_api():
    """GET-only registry dump. ?stage= filters (case-insensitive)."""
    stage = (request.args.get("stage") or "all").strip().lower()
    rows = _catalog_rows()
    if stage != "all":
        rows = [r for r in rows if r["stage"].lower() == stage]
    return jsonify({"count": len(rows), "skus": rows})
