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


# ---------------------------------------------------------------------------
# Enterprise redesign v2 (founder report 2026-10-28 + live-probe defects).
#
# One dominant provocative CTA per surface, the locked brand copy restored,
# a slim fact strip built only from verified facts, an enterprise depth
# system (layered glow + grid + a pure-CSS auction-flow visual), and the
# live-probe fixes: no blurred page-ghost behind the gate, no clipped gate
# headline, no stats tile that reads as broken, ticker folded into the
# system. Banned claims stay banned on both public surfaces.
# ---------------------------------------------------------------------------

STATIC = REPO_ROOT / "static"

HOME_PRIMARY_COPY = "Put your first agent to work"
GATE_PRIMARY_COPY = "Claim your free pass before the gate closes"
LOCKED_HEADLINE = "You Found the Edge"
LOCKED_SUBLINE = "Unbridled. Autonomous. Relentless. Flawless. Recursive."
LOCKED_POSITIONING = (
    "SINCOR Business Solutions is the Execution Layer "
    "for the Autonomous Machine Economy."
)
BANNED_CLAIMS = ("audited", "$0.15", "$150M", "Stop hiring", "mainnet")

ANCHOR = re.compile(r'<a [^>]*href="([^"]*)"[^>]*>(.*?)</a>', re.S)


def _anchors(html):
    """(href, visible text, raw tag) for every anchor in a fragment."""
    out = []
    for m in re.finditer(r"(<a [^>]*>)(.*?)</a>", html, re.S):
        text = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        href = re.search(r'href="([^"]*)"', m.group(1)).group(1)
        out.append((href, text, m.group(0)))
    return out


def _hero(html):
    return html.split('<header class="sp-hero">', 1)[1].split("</header>", 1)[0]


def _gate_copy(html):
    return html.split('<section class="copy">', 1)[1].split("</section>", 1)[0]


def _pro_css():
    return (STATIC / "sincor-pro.css").read_text(encoding="utf-8")


def _gate_source():
    return (TEMPLATES / "genesis_gate.html").read_text(encoding="utf-8")


def test_no_banned_claims_on_public_surfaces(client):
    for path in ("/", "/?inner=1"):
        html = client.get(path)
        for banned in BANNED_CLAIMS:
            assert banned not in html, (path, banned)


def test_home_hero_has_one_dominant_cta(client):
    hero = _hero(client.get("/?inner=1"))
    assert hero.count("sp-cta-primary") == 1
    primaries = [a for a in _anchors(hero) if "sp-cta-primary" in a[2]]
    assert len(primaries) == 1
    href, text, raw = primaries[0]
    assert href == "/signup"
    assert HOME_PRIMARY_COPY in text
    assert "sp-btn-xl" in raw and "sp-btn-gold" in raw
    row = hero.split('class="sp-cta-row"', 1)[1].split("</div>", 1)[0]
    assert len(ANCHOR.findall(row)) <= 2, "hero may hold primary + one ghost"


def test_home_hero_button_sprawl_demoted(client):
    hero = _hero(client.get("/?inner=1"))
    for demoted in ("Query TOA", "Register your agent", "Whitepaper", "⚡"):
        assert demoted not in hero, demoted


def test_home_closing_band_uses_same_primary(client):
    html = client.get("/?inner=1")
    primaries = [a for a in _anchors(html) if "sp-cta-primary" in a[2]]
    assert len(primaries) == 2, "hero + closing band, same primary CTA"
    assert {a[0] for a in primaries} == {"/signup"}
    assert all(HOME_PRIMARY_COPY in a[1] for a in primaries)


def test_home_locked_copy_present(client):
    html = client.get("/?inner=1")
    assert LOCKED_HEADLINE in html
    assert LOCKED_SUBLINE in html
    assert LOCKED_POSITIONING in html


def test_home_fact_strip_uses_only_safe_facts(client):
    hero = _hero(client.get("/?inner=1"))
    assert 'class="sp-facts"' in hero
    facts = hero.split('class="sp-facts"', 1)[1].split('class="sp-specs"', 1)[0]
    for fact in ("17", "Sealed-bid", "x402", "Base", "Genesis pass"):
        assert fact in facts, fact


def test_home_stats_strip_never_reads_broken(client):
    html = client.get("/?inner=1")
    assert "public snapshot unavailable" not in html
    assert "connecting to Base snapshot" not in html
    specs = html.split('class="sp-specs"', 1)[1].split("</div>\n  </div>", 1)[0]
    assert "17" in specs  # skills tile value, not a dynamic placeholder
    # Founder ruling 2026-10-29: the strip carries exactly 3 live-fed values
    # (the live-wired strip kept by the #562 conflict resolution).
    assert specs.count('class="v live-v"') == 3
    for tile_id in ("live-spot-val", "live-agent-count", "live-treasury-val"):
        assert f'id="{tile_id}"' in specs, tile_id


def test_home_hero_has_structured_visual_and_grid(client):
    hero = _hero(client.get("/?inner=1"))
    assert 'class="sp-flow"' in hero  # pure-CSS auction-flow visual anchor
    for step in ("Post", "bid", "execute", "Settled"):
        assert step.lower() in hero.lower(), step
    css = _pro_css()
    assert ".sp-hero::after" in css  # hairline grid layer over the glows
    assert "linear-gradient" in css.split(".sp-hero::after", 1)[1][:400]


def test_gate_hero_has_one_dominant_cta(client):
    html = client.get("/")
    copy = _gate_copy(html)
    assert copy.count("cta-primary") == 1
    primaries = [a for a in _anchors(copy) if "cta-primary" in a[2]]
    assert len(primaries) == 1
    href, text, _raw = primaries[0]
    assert href == "#claim"
    assert GATE_PRIMARY_COPY in text
    # the claim form's submit stays functional and labelled as before
    assert 'id="go"' in html
    assert 'type="submit"' in html


def test_gate_locked_copy_and_fact_strip(client):
    copy = _gate_copy(client.get("/"))
    assert LOCKED_HEADLINE in copy
    assert LOCKED_SUBLINE in copy
    assert LOCKED_POSITIONING in client.get("/")
    assert 'class="fact-strip"' in copy
    facts = copy.split('class="fact-strip"', 1)[1].split("</ul>", 1)[0]
    for fact in ("17", "Sealed-bid", "x402", "Base"):
        assert fact in facts, fact


def test_gate_has_no_blurred_page_ghost(client):
    html = client.get("/")
    assert "site-layer" not in html
    assert "<iframe" not in html
    assert 'class="veil"' not in html
    source = _gate_source()
    assert "backdrop-filter: blur(12px)" not in source


def test_gate_headline_cannot_clip():
    source = _gate_source()
    clamp = re.search(r"h1 \{[^}]*font-size: clamp\(([\d.]+)rem, ([\d.]+)vw, "
                      r"([\d.]+)rem\)", source)
    assert clamp, "gate h1 must keep fluid clamp type"
    assert float(clamp.group(3)) <= 6.0, "max size must fit the hero column"
    copy_rule = re.search(r"\.copy \{[^}]*max-width: (\d+)px", source)
    assert copy_rule and int(copy_rule.group(1)) >= 660
    assert ".brand img.brand-logo" in source
    logo = re.search(r"\.brand img\.brand-logo \{([^}]*)\}", source).group(1)
    assert "height: 88px" in logo  # refined tile, aligned with nav/footer


def test_flair_and_reduced_motion_hooks():
    css = _pro_css()
    assert ".sp-cta-primary" in css
    sheen = css.split(".sp-cta-primary", 1)[1]
    assert "@keyframes" in css and "ctaSheen" in css
    assert "prefers-reduced-motion" in css
    reduced = css.split("prefers-reduced-motion", 1)[1]
    assert "sp-cta-primary" in reduced  # flair animation disabled there
    gate = _gate_source()
    assert "prefers-reduced-motion" in gate
    assert "cta-primary" in gate.split("prefers-reduced-motion", 1)[1]


def test_responsive_cta_hooks():
    css = _pro_css()
    mobile = css.split("@media (max-width: 640px)", 1)[1]
    assert "sp-cta-primary" in mobile or "sp-cta-row" in mobile
    gate = _gate_source()
    mobile_gate = gate.split("@media (max-width: 640px)", 1)[1]
    assert "cta-primary" in mobile_gate  # full-width-friendly on phones
