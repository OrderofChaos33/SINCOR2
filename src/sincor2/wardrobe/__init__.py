"""SINCOR agent wardrobe: schema, cards, checkout quotes, heartbeat.

Package import must stay cheap. Eager imports of schema/quote here took
down the entire Railway process (missing modules) while /health stayed 200.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "WARDROBE_SCHEMA",
    "validate_wardrobe",
    "trust_score",
    "quote_sku",
    "SKU_CANON",
    "record_heartbeat",
    "stale_ids",
]


def __getattr__(name: str) -> Any:
    if name in {"WARDROBE_SCHEMA", "validate_wardrobe"}:
        from sincor2.wardrobe.schema import WARDROBE_SCHEMA, validate_wardrobe

        return WARDROBE_SCHEMA if name == "WARDROBE_SCHEMA" else validate_wardrobe
    if name in {"quote_sku", "SKU_CANON"}:
        from sincor2.wardrobe.quote import SKU_CANON, quote_sku

        return quote_sku if name == "quote_sku" else SKU_CANON
    if name == "trust_score":
        from sincor2.wardrobe.trust import trust_score

        return trust_score
    if name in {"record_heartbeat", "stale_ids"}:
        from sincor2.wardrobe import heartbeat

        return getattr(heartbeat, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
