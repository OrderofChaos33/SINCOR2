"""Vendored minimal price-oracle surface for the standalone package.

PACK-1 stub record: the platform module lazily imported exactly one
symbol, ``wiring_for``, from ``sincor2.defi.price_oracle`` (29,726 bytes
in the platform tree at the base commit). Copying that whole module
would drag in the platform's oracle, adapters, and transport code, so
this file vendors only the surface ``perp_hedge_swarm.price_feed_for``
actually uses, matching the platform semantics:

- ``PRODUCT_PRICE_NEEDS["P06_PERPS"] == ["ETH/USD", "BTC/USD"]``
- ``ProductPriceFeed.price(asset, now)`` refuses undeclared assets and
  otherwise delegates to ``oracle.get_price(asset, now, strict=...)``
  and returns its ``.price_fp`` — any oracle failure propagates
  (fail-closed).
- ``wiring_for(product_key, oracle)`` returns a ``ProductPriceFeed``
  and raises ``KeyError`` for a product with no declared price needs.

An oracle object supplied by an engagement must provide
``get_price(asset, now, strict=False)`` returning an object with a
``price_fp`` attribute (the platform ``PriceResult`` shape). No oracle
implementation, feed adapter, or network transport is included here.
"""

from __future__ import annotations

from typing import Dict, List, Optional


class OracleError(Exception):
    """Base error for the vendored oracle surface."""


PRODUCT_PRICE_NEEDS: Dict[str, List[str]] = {
    "P06_PERPS": ["ETH/USD", "BTC/USD"],
}


class ProductPriceFeed:
    """Per-product view over a supplied oracle (platform-matching)."""

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
            raise OracleError(
                f"{self._product_key} did not declare price need for {asset}")
        return self._oracle.get_price(asset, now, strict=strict).price_fp

    def require_all(self, now: Optional[float] = None,
                    strict: bool = False) -> Dict[str, int]:
        return {a: self.price(a, now, strict=strict) for a in self._assets}


def wiring_for(product_key: str, oracle) -> ProductPriceFeed:
    """Bind a supplied oracle to one product's declared price needs."""
    return ProductPriceFeed(oracle, product_key)
