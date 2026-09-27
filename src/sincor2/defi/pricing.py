"""Pricing model for speculative DeFi products.

Base fee comes from the catalog ``fee_bps`` (the protocol cut concept). Every
product carries standard + performance tiers priced in AXM/SINC. Status is
``draft`` for all 26 until the product reaches the ``product`` lifecycle
stage — pricing goes live only as part of the product -> catalog gate.
"""

from __future__ import annotations

from typing import Any, Dict

from .catalog import PROTOCOL_BY_ID

STATUS_DRAFT = "draft"
STATUS_LIVE = "live"
CURRENCY = "AXM"


def price_for(protocol_id: str, pricing_status: str = STATUS_DRAFT) -> Dict[str, Any]:
    spec = PROTOCOL_BY_ID[protocol_id]
    base = spec.fee_bps
    return {
        "protocol_id": protocol_id,
        "base_fee_bps": base,
        "currency": CURRENCY,
        "status": pricing_status,
        "tiers": {
            "standard": {
                "fee_bps": base,
                "description": "Flat protocol fee on settled volume / yield.",
            },
            "performance": {
                "fee_bps": base * 2,
                "description": "Higher fee tier only against a stated hurdle "
                               "(hurdle set at product stage).",
                "hurdle": None,
            },
        },
    }


def activate_pricing(product: Dict[str, Any]) -> Dict[str, Any]:
    """Mark pricing live. Refuses unless the product is at product/catalog stage.

    Pricing is set (numbers on file) at product stage; it goes live as part of
    the product -> catalog gate. Activating earlier is refused.
    """
    if product.get("stage") not in ("product", "catalog"):
        return {"ok": False,
                "reason": f"pricing activates at product stage; {product['sku']} "
                          f"is at '{product.get('stage')}'"}
    product["pricing_status"] = STATUS_LIVE
    return {"ok": True, "reason": "pricing live"}


def pricing_status_line(product: Dict[str, Any]) -> str:
    return price_for(product["protocol_id"],
                     product.get("pricing_status", STATUS_DRAFT))["status"]
