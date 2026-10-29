"""Site-consistency regression tests (2026-10-28 crawl fixes).

Covers:
- canonical SINC/AXM contract addresses on the served homepage, and the
  two wrong (invalid-length) addresses absent from every template;
- the served tokenlist carrying BOTH canonical tokens;
- no doubled percent signs ("%%") in served p-series product pages;
- plan pages describing the real wallet checkout (no credit-card copy)
  and no "paid in SINC" plan-payment claims on home/whitepaper;
- /buy copy not claiming AXM has no DEX spot / no live pair;
- no mojibake sequences left in the starter plan template;
- the professional page not claiming "50+ marketplace agents";
- the /products heading stating the honest 26 + 6 = 32 listing split.
"""
from __future__ import annotations

import json
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

# Canonical addresses — TOKEN_CANON.json (locked 2026-09-19).
SINC = "0xe1D836087F6573b665d25CE088793E916D7892f8"
AXM = "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a"
WRONG_SINC = "0x6E2E79DD87fFD6a3D5eB09C2ee4F8E1E38A4E3a86"
WRONG_AXM = "0xF3E3986E3F3D2bB6E3c98Cf2a56d8Db6E3f9a8b5f"

MOJIBAKE_MARKERS = ("â€", "âœ", "Â©", "ðŸ", "ï¸", "â†", "â€“", "â€”")


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


def test_homepage_serves_canonical_addresses():
    body = _render("home.html")
    assert SINC in body
    assert AXM.lower() in body.lower()
    assert WRONG_SINC not in body
    assert WRONG_AXM not in body


def test_no_template_contains_wrong_addresses():
    offenders = []
    for path in (ROOT / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if WRONG_SINC in text or WRONG_AXM in text:
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == [], f"wrong SINC/AXM addresses remain: {offenders}"


def test_served_tokenlist_has_both_canonical_tokens():
    for rel in ("static/tokenlists/sincor.tokenlist.json", "tokenlists/sincor.tokenlist.json"):
        listing = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        by_symbol = {t["symbol"]: t for t in listing["tokens"]}
        assert set(by_symbol) == {"SINC", "AXM"}, rel
        assert by_symbol["SINC"]["address"] == SINC, rel
        assert by_symbol["SINC"]["decimals"] == 8, rel
        assert by_symbol["AXM"]["address"] == AXM, rel
        assert by_symbol["AXM"]["decimals"] == 18, rel
        assert by_symbol["AXM"]["name"] == "AXIOM", rel
        assert by_symbol["AXM"]["chainId"] == 8453, rel


def test_product_pages_have_no_doubled_percent():
    pages = sorted((ROOT / "templates" / "products").glob("p*.html"))
    assert len(pages) == 26
    for path in pages:
        body = _render(f"products/{path.name}")
        assert "%%" not in body, path.name


def test_plan_pages_have_no_credit_card_copy():
    for name in ("product_starter.html", "product_professional.html", "product_enterprise.html"):
        body = _render(name)
        assert "credit card" not in body.lower(), name


def test_home_and_whitepaper_do_not_claim_plans_paid_in_sinc():
    for name in ("home.html", "whitepaper.html"):
        body = _render(name)
        assert "paid in SINC" not in body, name
        assert "Pay in SINC" not in body, name
        assert "SINC at $0.15 floor" not in body, name


def test_buy_page_does_not_claim_axm_has_no_dex_spot():
    body = _render("buy_tokens.html")
    assert "no DEX spot" not in body
    assert "no live pair" not in body


def test_starter_template_has_no_mojibake():
    text = (ROOT / "templates" / "product_starter.html").read_text(encoding="utf-8-sig")
    for marker in MOJIBAKE_MARKERS:
        assert marker not in text, marker
    # The repaired characters render as themselves.
    assert "🔍" in text
    assert "© 2026" in text


def test_professional_page_has_no_false_marketplace_agent_count():
    body = _render("product_professional.html")
    assert "50+ marketplace agents" not in body
    assert "17 live skills" in body


def test_products_heading_states_honest_split():
    body = _render("products.html")
    assert "32 listings" in body
    assert "26 DeFi products" in body
    assert "6 observability" in body
