"""Real revenue + value metrics — orders DB and on-chain, no simulation."""

from __future__ import annotations

from typing import Any


def fetch_orders_revenue() -> dict[str, Any]:
    from sincor2.daily_ops import _revenue_digest

    return _revenue_digest()


def fetch_live_status() -> dict[str, Any]:
    """Single source of truth for monetization / ops dashboards."""
    from sincor2.monetization_catalog import catalog_summary

    orders = fetch_orders_revenue()
    onchain: dict[str, Any] = {}
    try:
        from sincor2.value_engine import fetch_value_summary

        onchain = fetch_value_summary()
    except Exception as exc:
        onchain = {"ok": False, "error": str(exc)}

    catalog = catalog_summary()
    return {
        "source": "live",
        "simulated": False,
        "orders": orders,
        "onchain_streams": onchain.get("streams", []),
        "official_floor_usd": onchain.get("official_floor_usd"),
        "treasury": onchain.get("treasury"),
        "monetization": catalog,
        "completed_revenue_usd": orders.get("completed_revenue_usd", 0),
        "total_orders": orders.get("total_orders", 0),
    }