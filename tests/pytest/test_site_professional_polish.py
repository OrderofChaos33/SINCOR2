"""Site professional polish (Tier-1, genesis launch 2026-11-09).

The marketing pages run the black/gold pro design system
(static/sincor-pro.css: bg #0a0c10, gold #d4a94e/#f0c664, Inter body +
Sora display). A family of standalone public pages still ran legacy
themes — purple/cyan (#8b5cf6 / #06b6d4, "Plus Jakarta Sans") and a
third Space Grotesk / #7c3aed theme. These tests pin the unification:
no legacy palette or font tokens remain on the Tier-1 public surface,
the error page carries no CSS framework and routes to /products, and
the homepage title leads with the control-plane positioning.
"""
from __future__ import annotations

import os
import re

TEMPLATES = os.path.join(
    os.path.dirname(__file__), "..", "..", "templates"
)


def _source(name: str) -> str:
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# error.html — self-contained pro page, no framework, no legacy theme
# ---------------------------------------------------------------------------

def test_error_page_has_no_tailwind():
    assert "tailwind" not in _source("error.html").lower()


def test_error_page_has_no_purple_gradient_classes():
    src = _source("error.html").lower()
    assert "purple" not in src
    assert "from-purple" not in src
    assert "to-pink" not in src


def test_error_page_links_products_not_buy():
    src = _source("error.html")
    assert 'href="/products"' in src
    assert 'href="/buy"' not in src
    assert "/buy" not in src


def test_error_page_uses_pro_favicon_and_brand():
    src = _source("error.html")
    assert 'type="image/png" href="/static/sincor_favicon.png"' in src
    assert "/static/sincor_hornet_logo.jpg" in src
    assert "#0a0c10" in src


# ---------------------------------------------------------------------------
# Purple/cyan family — no legacy hex or font remains
# ---------------------------------------------------------------------------

PURPLE_FAMILY = (
    "genesis_gate.html",
    "agent_card.html",
    "docs.html",
    "login.html",
    "contact.html",
)


def test_purple_family_has_no_legacy_purple_hex():
    for name in PURPLE_FAMILY:
        assert "#8b5cf6" not in _source(name).lower(), name


def test_purple_family_has_no_plus_jakarta_sans():
    for name in PURPLE_FAMILY:
        assert "Plus Jakarta Sans" not in _source(name), name
        assert "Plus+Jakarta+Sans" not in _source(name), name


def test_purple_family_uses_pro_background():
    for name in PURPLE_FAMILY:
        assert "#0a0c10" in _source(name), name


# ---------------------------------------------------------------------------
# Third legacy theme (pricing / signup) — Space Grotesk / #7c3aed retired
# ---------------------------------------------------------------------------

THIRD_THEME = ("pricing.html", "signup.html")


def test_third_theme_has_no_legacy_purple_hex():
    for name in THIRD_THEME:
        assert "#7c3aed" not in _source(name).lower(), name


def test_third_theme_has_no_space_grotesk():
    for name in THIRD_THEME:
        assert "Space Grotesk" not in _source(name), name
        assert "Space+Grotesk" not in _source(name), name


def test_third_theme_uses_pro_background_and_favicon():
    for name in THIRD_THEME:
        src = _source(name)
        assert "#0a0c10" in src, name
        assert 'type="image/png" href="/static/sincor_favicon.png"' in src, name


# ---------------------------------------------------------------------------
# Homepage positioning — control plane is the lead narrative
# ---------------------------------------------------------------------------

def test_home_title_leads_with_control_plane():
    src = _source("home.html")
    m = re.search(r"<title>(.*?)</title>", src, re.S)
    assert m, "home.html has no <title>"
    assert "control plane" in m.group(1)


def test_home_uses_png_favicon_not_svg_icon():
    src = _source("home.html")
    assert "/static/favicon.svg" not in src
    assert "/static/sincor_favicon.png" in src


# ---------------------------------------------------------------------------
# Docs header nav + footer positioning copy
# ---------------------------------------------------------------------------

def test_docs_header_nav_points_at_pricing_and_products():
    src = _source("docs.html")
    header = src.split("</header>", 1)[0]
    assert '<a href="/pricing">Plans</a>' in header
    assert '<a href="/products">SINC</a>' in header


def test_pro_footer_blurb_is_control_plane_copy():
    src = _source("_pro_footer.html")
    assert (
        "The control plane for autonomous agents — governance, "
        "verification, and settlement for the agent economy."
    ) in src
