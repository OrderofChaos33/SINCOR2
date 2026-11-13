"""Products index status tests (MEDIUM-1 compliance fix).

The /products index must carry the same not-live status that the DeFi
detail pages disclose ("In development — not live — no funds accepted")
onto each of the 26 DeFi cards. The 6 observability / audit SKU cards
must NOT be labelled not-live (their detail pages describe private-beta
/ service engagements, not unlaunched DeFi products).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

NOT_LIVE_CHIP = "In development — not live — no funds accepted"

DEFI_HREFS = [
    "/products/p01-yield-aggregator",
    "/products/p02-clmm-manager",
    "/products/p03-intent-dark-pool",
    "/products/p04-mev-capture",
    "/products/p05-defi-insurance",
    "/products/p06-perp-dex",
    "/products/p07-bridge-optimizer",
    "/products/p08-rwa-vaults",
    "/products/p09-dao-governance",
    "/products/p10-flash-arbitrage",
    "/products/p11-delta-neutral",
    "/products/p12-twamm-engine",
    "/products/p13-avs-restaking",
    "/products/p14-prediction-markets",
    "/products/p15-lending-optimizer",
    "/products/p16-dex-aggregator",
    "/products/p17-options-protocol",
    "/products/p18-structured-products",
    "/products/p19-credit-underwriting",
    "/products/p20-compliance-automation",
    "/products/p21-treasury-dao",
    "/products/p22-stablecoin-yield",
    "/products/p23-nftfi-pools",
    "/products/p24-socialfi-revenue",
    "/products/p25-agent-portfolio",
    "/products/p26-defi-os",
]

SKU_HREFS = [
    "/products/sku-obs-01-agent-vitals",
    "/products/sku-obs-02-agent-audit-trail",
    "/products/sku-obs-03-drift-quality-watch",
    "/products/sku-aud-01-agent-forensic-audit",
    "/products/sku-aud-02-compliance-pack",
    "/products/sku-obs-ent-enterprise-mesh",
]


def _render(name: str) -> str:
    pytest.importorskip("flask")
    from flask import Flask, render_template
    from sincor2.onchain.constants import AXIOM_TOKEN, SINC_TOKEN
    from sincor2.social_links import SOCIAL_LINKS

    app = Flask(
        __name__,
        template_folder=str(ROOT / "templates"),
        static_folder=str(ROOT / "static"),
    )

    @app.context_processor
    def _inject():
        return {
            "sinc_token": SINC_TOKEN,
            "axiom_token": AXIOM_TOKEN,
            "treasury_address": "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac",
            "social_links": SOCIAL_LINKS,
            "is_admin": False,
            "admin_username": "",
            "is_customer": False,
            "username": "",
        }

    with app.app_context():
        return render_template(
            name,
            sinc_spot_usd=None,
            sinc_spot_label="$0.15 floor",
            walletconnect_project_id="",
        )


def _card_blocks(body: str) -> dict[str, str]:
    """Map each sp-card href to its full anchor block."""
    blocks = {}
    for match in re.finditer(
        r'<a class="sp-card" href="(?P<href>[^"]+)">(?P<block>.*?)</a>',
        body,
        flags=re.DOTALL,
    ):
        blocks[match.group("href")] = match.group("block")
    return blocks


def test_products_index_has_all_32_cards():
    blocks = _card_blocks(_render("products.html"))
    assert len(blocks) == 32
    for href in DEFI_HREFS + SKU_HREFS:
        assert href in blocks, href


def test_each_defi_card_carries_not_live_status():
    blocks = _card_blocks(_render("products.html"))
    for href in DEFI_HREFS:
        block = blocks[href]
        assert "not live" in block, href
        assert NOT_LIVE_CHIP in block, href


def test_products_index_total_not_live_markers():
    body = _render("products.html")
    assert body.count("not live") >= 26
    assert body.count(NOT_LIVE_CHIP) >= 26


def test_sku_cards_do_not_carry_not_live_status():
    blocks = _card_blocks(_render("products.html"))
    for href in SKU_HREFS:
        block = blocks[href]
        assert "not live" not in block, href
        assert NOT_LIVE_CHIP not in block, href


def test_products_heading_strings_still_present():
    body = _render("products.html")
    assert "32 listings" in body
    assert "26 DeFi products" in body
    assert "6 observability" in body
