"""Security page truth tests (observability build #9, 2026-10-28).

/security was an 813-byte "Coming soon" stub on an off-brand Tailwind CDN
page, linked from the pro footer and the sitemap. These tests pin the
replacement: a real page in the site's own design system (sincor-pro.css
+ the shared pro nav/footer partials) whose claims are limited to the
posture that is verifiable in this repo today:

  * OFAC/SDN sanctions screening on paid surfaces, vendored snapshot,
    fail-closed 48h freshness (src/sincor2/sanctions_screen.py)
  * sealed-bid commit/reveal auctions (src/sincor2/a2a_inbound_market.py)
  * EIP-191 payer-wallet binding for payment challenges
    (src/sincor2/x402_payments.py, src/sincor2/rail_routes.py)
  * claim register + claim-blocklist discipline
    (data/claim_register.json, scripts/claim-blocklist.txt,
    scripts/pre-commit-sincor)
  * disclosed liveness cohort (src/sincor2/liveness.py)
  * responsible disclosure per SECURITY.md (GitHub Security Advisories)

Banned on the page: every claim-scanner forbidden pattern (which includes
"audited", certifications, guarantees, SOC 2, price/FDV figures) and any
stub language.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from sincor2 import claim_scan

REPO_ROOT = Path(__file__).resolve().parents[2]
SECURITY_TEMPLATE = REPO_ROOT / "templates" / "security.html"


@pytest.fixture(scope="module")
def client():
    from sincor2.mvp_app import app

    app.config.update(TESTING=True)
    return app.test_client()


@pytest.fixture(scope="module")
def security_html(client):
    resp = client.get("/security")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_security_page_is_not_the_stub(security_html):
    assert "Coming soon" not in security_html
    assert "coming soon" not in security_html.lower()
    assert "tailwindcss" not in security_html  # off-brand CDN stub is gone
    assert len(security_html) > 3000  # a real page, not an 813-byte stub


def test_security_page_uses_pro_design_system(security_html):
    assert "/static/sincor-pro.css" in security_html
    # shared pro nav + footer partials render the hornet lockup
    assert "sincor_hornet_logo.jpg" in security_html
    assert "sincor_logo.svg" not in security_html


def test_security_page_covers_the_actual_posture(security_html):
    text = re.sub(r"<[^>]+>", " ", security_html)
    text = re.sub(r"\s+", " ", text)
    for anchor in (
        "OFAC",                # sanctions screening
        "sealed",              # sealed-bid commit/reveal auctions
        "commit",
        "reveal",
        "EIP-191",             # payer-wallet binding
        "claim register",      # claim discipline
        "blocklist",
        "liveness",            # disclosed liveness cohort
        "Security Advisories", # responsible disclosure per SECURITY.md
        "security.txt",        # published contact/policy pointer
    ):
        assert anchor.lower() in text.lower(), f"security page is missing: {anchor}"


def test_security_page_has_no_forbidden_claims():
    source = SECURITY_TEMPLATE.read_text(encoding="utf-8-sig")
    prose = " ".join(p for _, p in claim_scan.html_prose_lines(source))
    normalized = claim_scan.normalize(prose)
    for name, pattern in claim_scan.FORBIDDEN_PATTERNS.items():
        assert not pattern.search(normalized), (
            f"security page prose trips the claim scanner pattern: {name}"
        )
    lowered = prose.lower()
    assert "audited" not in lowered
    assert "third-party audit" not in lowered
    assert "$0.15" not in prose
    assert "mainnet" not in lowered
