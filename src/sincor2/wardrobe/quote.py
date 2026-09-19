"""SKU quotes for wardrobe /api/sku/quote. AXM-first, USDC reference."""

from __future__ import annotations

from typing import Any

SKU_CANON = {
    "intel": {"label": "Monthly Intelligence", "usd": 149, "token": "AXM"},
    "starter": {"label": "Starter Agents", "usd": 297, "token": "AXM", "agents": 10},
    "professional": {"label": "Professional Agents", "usd": 997, "token": "AXM", "agents": 25},
    "enterprise": {"label": "Enterprise Agents", "usd": 2997, "token": "AXM", "agents": 45},
}


def quote_sku(sku: str, asset: str = "USDC", axm_spot_usd: float | None = None) -> dict[str, Any]:
    key = (sku or "").strip().lower()
    plan = SKU_CANON.get(key)
    if not plan:
        return {"ok": False, "error": "unknown_sku", "sku": key}
    asset = (asset or "USDC").upper()
    usd = float(plan["usd"])
    token = "AXM" if asset == "AXM" else "USDC"
    amount = usd
    if token == "AXM" and axm_spot_usd and axm_spot_usd > 0:
        amount = round(usd / float(axm_spot_usd), 6)
    return {
        "ok": True,
        "sku": key,
        "label": plan["label"],
        "asset": token,
        "usd_reference": usd,
        "amount": amount,
        "token": token,
    }
