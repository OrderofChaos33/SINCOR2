"""SINCOR observability + audit SKU registry (Track B, builder B1).

Commercial registry for the six OBS/AUD SKUs: pricing, stage labels,
lifecycle gates, spec locations, and draft-page locations.

STANDING RULE (founder, 2026-09-29): working products only. Nothing in this
package is customer-facing: the product-page templates live under
``templates/products/drafts/`` and are NOT wired to any route, nav, pricing
page, or sitemap. A draft page may only be promoted to the public surface
after its SKU's publish gate (see ``gates.evaluate_publish``) passes on
verified end-to-end evidence. ``mvp_blueprints/pages.py`` must never import
this package for storefront rendering.
"""

from .registry import (
    SKUS,
    SKU,
    SKU_BY_ID,
    SIBLING_MODULE_PATHS,
    assert_registry_complete,
    sibling_module_path,
)
from .gates import ObsProofLedger

__all__ = [
    "SKUS",
    "SKU",
    "SKU_BY_ID",
    "SIBLING_MODULE_PATHS",
    "assert_registry_complete",
    "sibling_module_path",
    "ObsProofLedger",
]
