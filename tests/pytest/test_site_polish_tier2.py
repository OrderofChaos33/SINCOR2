"""Site professional polish Tier-2 (genesis launch 2026-11-09).

Tier-1 (test_site_professional_polish.py) unified the main public
surfaces to the black/gold pro design system (bg #0a0c10, gold
#d4a94e/#f0c664, Inter body + Sora display). The secondary templates
still ran the legacy themes — purple/cyan (#0a0e27 / #8b5cf6 / #06b6d4,
Plus Jakarta Sans), the Space Grotesk / #7c3aed third theme, and
Tailwind purple/cyan/blue utility classes. These tests pin the
Tier-2 unification: no legacy palette, font, CSS-var, or utility-class
tokens remain on the Tier-2 surface, the pro tokens are present, and
every retokened template still renders.
"""
from __future__ import annotations

import os
import re

import pytest

TEMPLATES = os.path.join(
    os.path.dirname(__file__), "..", "..", "templates"
)

TIER2 = (
    "_social_links.html",
    "admin_console.html",
    "agent_selector.html",
    "axiom.html",
    "buy.html",
    "buy_converting.html",
    "buy_tokens.html",
    "earn.html",
    "executive_dashboard.html",
    "onboarding.html",
    "operator_dashboard.html",
    "pitch.html",
    "privacy.html",
    "product_enterprise.html",
    "product_intel.html",
    "product_professional.html",
    "product_report.html",
    "product_starter.html",
    "products_enterprise.html",
    "products_professional.html",
    "products_starter.html",
    "refer.html",
    "register_agent.html",
    "sales.html",
    "sinc_acceptance.html",
    "sinc_payment_modal.html",
    "sitemap.html",
    "terms.html",
    "toa.html",
    "vertical_webbuilder.html",
    "webbuilder_preview.html",
    "webbuilder_studio.html",
    "whitepaper.html",
)

# Pages styled with Tailwind utility classes instead of inline <style>.
TAILWIND_FAMILY = (
    "agent_selector.html",
    "buy_converting.html",
    "executive_dashboard.html",
    "privacy.html",
    "terms.html",
)


def _source(name: str) -> str:
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Legacy hex / rgba palette — retired everywhere on the Tier-2 surface
# ---------------------------------------------------------------------------

LEGACY_TOKENS = (
    "#8b5cf6", "#06b6d4", "#0a0e27", "#7c3aed", "#a78bfa", "#c4b5fd",
    "#6d28d9", "#5b21b6", "#4c1d95", "#22d3ee", "#a855f7", "#6366f1",
    "#4338ca", "#050814", "#050508", "#0a0a14", "#1b1b34",
    "#0891b2", "#0ea5e9", "#1d4ed8", "#1e40af", "#1a237e", "#283593",
    "#3949ab", "#3f51b5", "#818cf8", "#a5b4fc", "#bfdbfe", "#0f1629",
    "#08101f", "#050b17", "#111b2f", "#0f172a", "#0f1a2a", "#2a1a4a",
    "#0c1426", "99,102,241", "99, 102, 241",
    "139,92,246", "139, 92, 246", "124,58,237", "124, 58, 237",
    "167,139,250", "167, 139, 250", "168,85,247", "168, 85, 247",
    "6,182,212", "6, 182, 212", "34,211,238", "34, 211, 238",
)


def test_tier2_has_no_legacy_palette_tokens():
    for name in TIER2:
        src = _source(name).lower()
        for token in LEGACY_TOKENS:
            assert token not in src, (name, token)


def test_tier2_has_no_legacy_fonts():
    for name in TIER2:
        src = _source(name)
        assert "Space Grotesk" not in src, name
        assert "Space+Grotesk" not in src, name
        assert "Plus Jakarta Sans" not in src, name
        assert "Plus+Jakarta+Sans" not in src, name


def test_tier2_has_no_legacy_css_vars():
    for name in TIER2:
        src = _source(name)
        assert "var(--purple" not in src, name
        assert "var(--cyan" not in src, name
        assert not re.search(r"--purple[a-z-]*\s*:", src), name
        assert not re.search(r"--cyan\s*:", src), name


def test_tailwind_family_has_no_legacy_color_classes():
    legacy_class = re.compile(r"-(purple|cyan|indigo|pink|blue)-\d+")
    for name in TAILWIND_FAMILY:
        assert not legacy_class.search(_source(name)), name


# ---------------------------------------------------------------------------
# Pro system — present on every Tier-2 template
# ---------------------------------------------------------------------------

PRO_TOKENS = (
    "#d4a94e", "#f0c664", "rgba(212,169,78", "rgba(212, 169, 78",
    "rgba(240,198,100", "rgba(240, 198, 100", "var(--gold", "-yellow-",
)


def test_tier2_uses_pro_tokens():
    for name in TIER2:
        src = _source(name)
        assert any(tok in src for tok in PRO_TOKENS), name


def test_tier2_dark_pages_use_pro_background():
    # Every Tier-2 page that sets the pro dark background token in a
    # :root/body rule must use #0a0c10 (light utility pages excluded).
    for name in TIER2:
        src = _source(name)
        if "--bg" in src:
            assert "#0a0c10" in src, name


# ---------------------------------------------------------------------------
# Rendering — every retokened template still renders without error
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mvp_app_obj():
    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault(
        "JWT_SECRET_KEY", "test-jwt-secret-key-32-char-minimum-ok"
    )
    os.environ.setdefault("ADMIN_USERNAME", "admin")
    os.environ.setdefault(
        "ADMIN_PASSWORD", "admin-password-32-char-minimum-ok"
    )
    from sincor2.mvp_app import app

    app.config["TESTING"] = True
    return app


def test_tier2_templates_render(mvp_app_obj):
    # social_links is the shared per-network URL dict the routes pass;
    # project is the webbuilder preview payload. Both are route-supplied
    # context, stubbed here the way the routes supply them.
    ctx = {
        "social_links": {
            "x": "", "discord": "", "facebook": "",
            "farcaster": "", "telegram": "",
        },
        "project": {},
    }
    with mvp_app_obj.test_request_context():
        for name in TIER2:
            rendered = mvp_app_obj.jinja_env.get_template(name).render(ctx)
            assert rendered.strip(), name
