"""Axiom (/axiom) copy regression tests — MEDIUM-2 compliance fix.

The /axiom page must use plain utility-token language, consistent with the
site's utility-token framing: AXM pays for agent skills, backs task
bounties, settles agent-to-agent commerce, and a 5% platform fee is charged
on SINCOR agent commerce. It must not use value-accumulation / investment
framing, and it must not display a live price fetched from Dexscreener.

Renders templates/axiom.html with the same _render helper pattern used by
tests/pytest/test_site_consistency_fixes.py (the /axiom route itself is
served by src/sincor2/blueprints/pages.py and
src/sincor2/mvp_blueprints/pages.py; no new route is added here).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

AXM = "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a"

BANNED_STRINGS = (
    "value accumulation",
    "captures value",
    "value creation",
    "stronger the foundation",
    "long-term value",
    "only invest",
)


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


@pytest.fixture(scope="module")
def body() -> str:
    return _render("axiom.html")


def test_no_banned_value_or_investment_framing(body):
    lowered = body.lower()
    for banned in BANNED_STRINGS:
        assert banned not in lowered, banned


def test_utility_language_present(body):
    lowered = body.lower()
    # AXM pays for agent skills and/or backs task bounties.
    assert "pays for agent skills" in lowered or "pay for agent skills" in lowered
    assert "backs task bounties" in lowered or "back task bounties" in lowered
    # The 5% platform fee mechanic is stated.
    assert "5% platform fee" in lowered


def test_no_live_price_stat_or_fetch_script(body):
    assert 'id="axm-price"' not in body
    assert "axm-price" not in body
    assert "fetchAXMPrice" not in body
    # The price-fetch API endpoint is gone; the Dexscreener *link* card in
    # Links & Resources is an external market link and stays.
    assert "api.dexscreener.com" not in body
    assert "dexscreener.com/base/" in body


def test_disclaimer_uses_use_not_invest(body):
    assert "Only use what you can afford to lose" in body
    assert "Only invest" not in body
    # Registered disclaimer sentence (claim register CR-010) kept exactly.
    assert "Past performance does not guarantee future results" in body


def test_factual_anchors_still_present(body):
    assert "1,000,000,000" in body or "1B" in body
    assert AXM in body
    assert "5%" in body
    # Remaining stats row entries stay.
    assert "Total Supply" in body
    assert "Ticker" in body
    assert "Base" in body
