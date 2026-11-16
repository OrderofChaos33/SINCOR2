"""Minimal vendored price-oracle wiring surface for the standalone package.

The platform original (``sincor2.defi.price_oracle``) is a large shared
module. The intent/dark-pool module only uses one function from it:

    wiring_for("P03_INTENT_DARK", oracle) -> ProductPriceFeed

with ``ProductPriceFeed.price(asset, now, strict=False) -> int``,
``ProductPriceFeed.require_all(...)``, ``.product_key`` and ``.assets``.

This stub reproduces exactly that surface for this product's declared
assets (``ETH/USD``, ``BTC/USD`` — copied from the platform module's
``PRODUCT_PRICE_NEEDS["P03_INTENT_DARK"]`` and the source module's
``PRICE_ASSETS``). It delegates price reads to any injected oracle object
exposing ``get_price(asset, now, strict=...)`` returning an object with a
``price_fp`` attribute — the same duck-typed contract the platform
``ProductPriceFeed`` uses. No price data, no feeds, and no chain access
are vendored here. Fail-closed: an undeclared asset raises, and any
oracle failure propagates.

An engagement that needs real feeds must supply a real oracle adapter
under the signed engagement; this stub does not provide one.
"""

from __future__ import annotations

from typing import Dict, List, Optional

PRODUCT_PRICE_NEEDS: Dict[str, List[str]] = {
    "P03_INTENT_DARK": ["ETH/USD", "BTC/USD"],
}


class OracleStubError(Exception):
    """Raised when the stub feed is asked for an undeclared asset."""


class ProductPriceFeed:
    """Per-product view over an injected oracle (platform-compatible)."""

    def __init__(self, oracle, product_key: str) -> None:
        if product_key not in PRODUCT_PRICE_NEEDS:
            raise KeyError(f"{product_key} declares no price needs")
        self._oracle = oracle
        self._product_key = product_key
        self._assets = list(PRODUCT_PRICE_NEEDS[product_key])

    @property
    def product_key(self) -> str:
        return self._product_key

    @property
    def assets(self) -> List[str]:
        return list(self._assets)

    def price(self, asset: str, now: Optional[float] = None,
              strict: bool = False) -> int:
        if asset not in self._assets:
            raise OracleStubError(
                f"{self._product_key} did not declare price need for {asset}")
        return self._oracle.get_price(asset, now, strict=strict).price_fp

    def require_all(self, now: Optional[float] = None,
                    strict: bool = False) -> Dict[str, int]:
        return {a: self.price(a, now, strict=strict) for a in self._assets}


def wiring_for(product_key: str, oracle) -> ProductPriceFeed:
    """Bind an injected oracle to one product's declared price needs."""
    return ProductPriceFeed(oracle, product_key)
