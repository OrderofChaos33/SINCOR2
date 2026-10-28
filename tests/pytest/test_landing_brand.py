"""Landing-page brand regression tests (founder report 2026-10-27).

The founder's hornet lockup (static/sincor_hornet_logo.jpg) already contains
the full "SINCOR Business Solutions" lettering. The pro nav/footer must show
that lockup ALONE — the retired sincor_logo.svg plus a second rendered
"SINCOR" text node next to it produced the visible "SINCOR SINCOR" double
brand the founder reported on the live site. These tests pin:

- the inner homepage (/?inner=1) uses the hornet lockup, never the old SVG;
- every .sp-brand anchor is image-only (no duplicate wordmark text);
- the genesis gate (the public landing page at /) shows the hornet lockup,
  the main slogan, and a promoted call-to-action button;
- the homepage hero call-to-action carries the promoted (xl) button style.
"""
import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "templates"

BRAND_ANCHOR = re.compile(r'<a class="sp-brand"[^>]*>(.*?)</a>', re.S)


class _Renderer:
    """Render the real templates exactly as the pages blueprint serves them.

    home.html takes no template variables; genesis_gate.html takes only
    launch_at / genesis_count (see mvp_blueprints/pages.py index()).
    """

    def __init__(self):
        self._env = Environment(loader=FileSystemLoader(str(TEMPLATES)))

    def get(self, path: str) -> str:
        if path.startswith("/?inner=1"):
            return self._env.get_template("home.html").render()
        return self._env.get_template("genesis_gate.html").render(
            launch_at="2026-11-09T00:00:00.000Z", genesis_count=0
        )


@pytest.fixture(scope="module")
def client():
    return _Renderer()


def _brand_text_outside_img(anchor_html: str) -> str:
    """Visible text of a brand anchor with the <img> tag itself removed."""
    without_img = re.sub(r"<img[^>]*>", "", anchor_html)
    return re.sub(r"<[^>]+>", "", without_img).strip()


def test_home_uses_hornet_lockup_not_old_svg(client):
    html = client.get("/?inner=1")
    assert "sincor_hornet_logo.jpg" in html
    assert "sincor_logo.svg" not in html


def test_home_brand_anchors_have_no_duplicate_wordmark(client):
    html = client.get("/?inner=1")
    anchors = BRAND_ANCHOR.findall(html)
    assert anchors, "expected at least one .sp-brand anchor (nav or footer)"
    for anchor in anchors:
        assert anchor.count("<img") == 1
        assert _brand_text_outside_img(anchor) == ""


def test_brand_partials_have_no_duplicate_wordmark():
    for partial in ("_pro_nav.html", "_pro_footer.html"):
        source = (TEMPLATES / partial).read_text(encoding="utf-8")
        assert "sincor_logo.svg" not in source
        for anchor in BRAND_ANCHOR.findall(source):
            assert _brand_text_outside_img(anchor) == ""


def test_gate_shows_logo_slogan_and_cta(client):
    html = client.get("/")
    assert "sincor_hornet_logo.jpg" in html
    assert "Autonomous Machine Economy" in html  # main slogan (h1)
    assert 'id="go"' in html  # claim call-to-action button
    assert "Claim free genesis pass" in html


def test_home_hero_cta_is_promoted(client):
    html = client.get("/?inner=1")
    hero = html.split("sp-cta-band")[0]  # hero block precedes CTA bands
    assert "sp-btn-xl" in hero
