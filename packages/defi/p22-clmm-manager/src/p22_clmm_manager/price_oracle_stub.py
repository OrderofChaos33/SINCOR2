"""PACK-1 stub: minimal vendored surface of the platform price oracle.

The platform module ``sincor2.defi.clmm_manager`` reaches into the shared
platform oracle in exactly one place: the lazy import inside
``clmm_manager.price_feed_for``::

    from .price_oracle import wiring_for
    return wiring_for("P02_CLMM", oracle)

That is the module's ONLY platform-internal dependency. To make this
package standalone, the extracted copy redirects that one import to this
stub (see PACKAGING.md). This stub vendors the minimal contract of the
platform's ``price_oracle.wiring_for`` / ``ProductPriceFeed`` as used by
this module:

- ``wiring_for(product_key, oracle)`` binds an oracle to a product key.
- The returned feed exposes ``product_key``, ``assets``, ``price(asset,
  now=None, strict=False)`` and ``require_all(now=None, strict=False)``.
- ``price()`` delegates to ``oracle.get_price(asset, now, strict=strict)``
  and returns its ``price_fp`` attribute; it raises on any oracle failure
  (fail-closed), exactly like the platform original.
- The declared assets for ``P02_CLMM`` are ``["ETH/USD", "USDC/USD"]``,
  matching the platform's ``PRODUCT_PRICE_NEEDS`` entry and the module's
  own ``PRICE_ASSETS`` constant.

This stub performs NO price fetching of its own and wires NO live feed.
Inside a signed engagement, the counterparty's real oracle adapter is
injected by the deployer (see DEPLOY.md / deploy/DEPLOY.md).
"""

from __future__ import annotations

from typing import Dict, List, Optional


class OracleStubError(RuntimeError):
    """Raised when the stub feed cannot serve a price (fail-closed)."""


# Declared price needs, mirroring the platform PRODUCT_PRICE_NEEDS entry
# for this product and clmm_manager.PRICE_ASSETS.
PRODUCT_PRICE_NEEDS: Dict[str, List[str]] = {
    "P02_CLMM": ["ETH/USD", "USDC/USD"],
}


class ProductPriceFeed:
    """Per-product view over an injected oracle (duck-typed).

    The injected oracle must expose
    ``get_price(asset, now, strict=False)`` returning an object with a
    ``price_fp`` attribute. Any exception from the oracle propagates so
    consumers stay fail-closed.
    """

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
        return {asset: self.price(asset, now, strict=strict)
                for asset in self._assets}


def wiring_for(product_key: str, oracle) -> ProductPriceFeed:
    """Bind the injected oracle to one product's declared price needs."""
    return ProductPriceFeed(oracle, product_key)
