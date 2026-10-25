"""Pricing / buy page wiring tests (audit wave-3 item 7).

Both templates must present the live SINCOR rail offer — B2B skill packs,
digital downloads, WebBuilder, and 30-day wallet subscriptions — and every
CTA must resolve to a /rail/pay/<resource_id> that exists in the server
tables (rail_routes.PACK_SKUS incl. DIGITAL_SKUS merged in, and
rail_subscriptions.SUB_PLANS). No legacy competitive-intelligence copy,
no phantom products, no retired Stripe checkout.
"""
import re
from pathlib import Path

import pytest
from flask import Flask

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "templates"

RAIL_PAY_HREF = re.compile(r'href="(/rail/pay/[^"]+)"')

EXPECTED_RESOURCE_IDS = {
    "rail-pack-credentialing_launch",
    "rail-pack-chroma_compliance",
    "rail-pack-sadas_execution",
    "rail-pack-webbuilder_site",
    "rail-pack-digital_bi_template_pack",
    "rail-pack-digital_ai_agent_bundle",
    "rail-pack-digital_growth_forecast",
    "rail-sub-starter",
    "rail-sub-professional",
    "rail-sub-enterprise",
}


@pytest.fixture(scope="module")
def client():
    from sincor2.blueprints.pages import pages_bp

    app = Flask(__name__, template_folder=str(TEMPLATES))
    app.register_blueprint(pages_bp)
    return app.test_client()


@pytest.fixture(scope="module")
def valid_resource_ids():
    from sincor2.rail_routes import PACK_SKUS
    from sincor2.rail_subscriptions import SUB_PLANS

    ids = {f"rail-pack-{sku}" for sku in PACK_SKUS}
    ids |= {f"rail-sub-{plan}" for plan in SUB_PLANS}
    return ids


def _rail_pay_ids(html):
    return [m.rsplit("/", 1)[-1] for m in RAIL_PAY_HREF.findall(html)]


def _fmt_price(price_cents):
    dollars = price_cents / 100
    return f"${dollars:,.0f}" if dollars == int(dollars) else f"${dollars:,.2f}"


def test_pricing_page_has_no_legacy_branding(client):
    resp = client.get("/pricing")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Competitive Intelligence" not in body
    assert "What's Inside Every Report" not in body
    assert "Satisfaction Guarantee" not in body
    assert "/buy#b2b-packs" not in body


def test_pricing_ctas_resolve_to_real_resources(client, valid_resource_ids):
    body = client.get("/pricing").get_data(as_text=True)
    ids = _rail_pay_ids(body)
    assert ids, "pricing page has no /rail/pay CTAs"
    for rid in ids:
        assert rid in valid_resource_ids, f"unknown rail resource: {rid}"
    # The full live offer is linked: 4 packs + 3 digital + 3 subscriptions,
    # and every one of those ids is itself a real server resource.
    assert EXPECTED_RESOURCE_IDS.issubset(set(ids))
    assert EXPECTED_RESOURCE_IDS.issubset(valid_resource_ids)


def test_pricing_prices_match_server_tables(client):
    from sincor2.rail_routes import PACK_SKUS
    from sincor2.rail_subscriptions import SUB_PLANS

    raw = client.get("/pricing").get_data(as_text=True)
    # Prices are styled as <sup>$</sup>297 — compare against the rendered
    # text a visitor actually sees, with tags stripped.
    body = re.sub(r"<[^>]+>", "", raw)
    for sku, info in PACK_SKUS.items():
        rendered = _fmt_price(info["price_cents"])
        assert rendered in body, f"{sku} price {rendered} missing from /pricing"
    for plan, info in SUB_PLANS.items():
        rendered = _fmt_price(info["price_cents"])
        assert rendered in body, f"sub {plan} price {rendered} missing from /pricing"


def test_buy_page_ctas_resolve_and_no_legacy_checkout(client, valid_resource_ids):
    resp = client.get("/buy")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Competitive Intelligence" not in body
    # The retired Stripe checkout and the mailto pack CTAs are gone.
    assert "/api/stripe/checkout" not in body
    assert "mailto:support@getsincor.com?subject=" not in body
    # Phantom legacy products (the $49 report / $149 monthly intel) are
    # not in any server table and must not be sold.
    assert not re.search(r"\$49(?!\d)", body)
    assert not re.search(r"\$149(?!\d)", body)
    ids = _rail_pay_ids(body)
    assert ids, "buy page has no /rail/pay CTAs"
    for rid in ids:
        assert rid in valid_resource_ids, f"unknown rail resource: {rid}"
    assert EXPECTED_RESOURCE_IDS.issubset(set(ids))
