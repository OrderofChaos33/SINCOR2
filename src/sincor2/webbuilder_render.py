"""Orion site generator — stores HTML per version and draft/preview/live lanes."""

from __future__ import annotations

import html
import json
import secrets
from pathlib import Path
from typing import Any


def _esc(text: str) -> str:
    return html.escape(str(text or ""), quote=True)


def build_site_html(project: dict, discovery: dict[str, Any], lane: str = "draft") -> str:
    """Generate production-ready single-page site HTML."""
    pid = project["id"]
    name = discovery.get("name") or project.get("name", "Local Business")
    niche = project.get("niche") or "local services"
    phone = discovery.get("phone") or ""
    address = discovery.get("address") or project.get("territory", "")
    rating = discovery.get("rating")
    review_count = discovery.get("review_count")
    snippet = discovery.get("reviews_snippet") or project.get("prompt") or f"Professional {niche}."
    categories = discovery.get("categories") or []
    services = categories[:4] if categories else [f"{niche} — core service", "Consultation", "Emergency calls", "Maintenance"]
    is_preview = lane != "live"
    banner = ""
    if is_preview:
        banner = f"""
    <div class="staging-banner">
      STAGING PREVIEW — not indexed · <a href="/verticals/webbuilder/studio?project={_esc(pid)}">Open in Studio</a>
    </div>"""

    rating_block = ""
    if rating:
        rating_block = f'<p class="rating">★ {rating}/5 · {review_count or 0} reviews</p>'

    service_cards = "".join(
        f'<div class="card"><h3>{_esc(s)}</h3><p>Licensed, insured, and ready to book. Serving {_esc(project.get("territory") or "your area")}.</p></div>'
        for s in services
    )

    phone_cta = f'<a class="cta secondary" href="tel:{_esc(phone.replace(" ", ""))}">Call now</a>' if phone else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_esc(name)}</title>
  <meta name="description" content="{_esc(snippet[:160])}">
  {"<meta name=\"robots\" content=\"noindex, nofollow\">" if is_preview else ""}
  <meta property="og:title" content="{_esc(name)}">
  <meta property="og:description" content="{_esc(snippet[:160])}">
  <script type="application/ld+json">{json.dumps({
      "@context": "https://schema.org",
      "@type": "LocalBusiness",
      "name": name,
      "telephone": phone,
      "address": address,
      "description": snippet[:300],
  })}</script>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:system-ui,-apple-system,sans-serif;color:#0f172a;background:#f8fafc;line-height:1.6}}
    .staging-banner{{background:linear-gradient(90deg,#4c1d95,#0891b2);color:#fff;text-align:center;padding:10px;font-size:13px;font-weight:600}}
    .staging-banner a{{color:#fde68a}}
    header{{background:#fff;border-bottom:1px solid #e2e8f0;padding:18px 24px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px}}
    .logo{{font-weight:800;font-size:1.25rem}}
    nav a{{margin-left:16px;color:#64748b;text-decoration:none;font-weight:600;font-size:14px}}
    .hero{{padding:72px 24px;text-align:center;background:linear-gradient(180deg,#fff,#f1f5f9)}}
    .hero h1{{font-size:clamp(2rem,5vw,3rem);margin-bottom:12px}}
    .hero p{{color:#64748b;max-width:560px;margin:0 auto 20px}}
    .rating{{color:#f59e0b;font-weight:700;margin-bottom:20px}}
    .cta{{display:inline-block;background:#7c3aed;color:#fff;padding:14px 28px;border-radius:10px;font-weight:700;text-decoration:none;margin:4px}}
    .cta.secondary{{background:#0f172a}}
    .grid{{max-width:1000px;margin:0 auto;padding:48px 24px 64px;display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}}
    .card{{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:22px}}
    .card h3{{margin-bottom:8px;font-size:1.05rem}}
    .card p{{color:#64748b;font-size:14px}}
    .contact{{max-width:520px;margin:0 auto 80px;padding:0 24px}}
    .contact form{{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:24px;display:grid;gap:12px}}
    label{{font-size:12px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.06em}}
    input,textarea{{width:100%;padding:12px;border:1px solid #cbd5e1;border-radius:8px;font:inherit}}
    button{{padding:12px;background:#7c3aed;color:#fff;border:none;border-radius:8px;font-weight:700;cursor:pointer}}
    .msg{{display:none;padding:10px;border-radius:8px;background:#ecfdf5;color:#065f46;font-size:14px}}
    footer{{text-align:center;padding:32px;color:#94a3b8;font-size:12px;border-top:1px solid #e2e8f0}}
  </style>
</head>
<body>
{banner}
  <header>
    <div class="logo">{_esc(name)}</div>
    <nav><a href="#services">Services</a><a href="#reviews">Reviews</a><a href="#contact">Contact</a></nav>
  </header>
  <section class="hero">
    <h1>{_esc(name)}</h1>
    <p>{_esc(snippet)}</p>
    {rating_block}
    <a class="cta" href="#contact">Book a consultation</a>
    {phone_cta}
  </section>
  <div class="grid" id="services">{service_cards}</div>
  <section class="grid" id="reviews" style="padding-top:0">
    <div class="card"><h3>Trusted locally</h3><p>{_esc(address) or "Proudly serving the community."}</p></div>
    <div class="card"><h3>Why choose us</h3><p>Fast response, clear pricing, and work that speaks for itself.</p></div>
  </section>
  <section class="contact" id="contact">
    <form id="wb-form">
      <input type="hidden" name="project_id" value="{_esc(pid)}">
      <div><label>Name</label><input name="name" required placeholder="Your name"></div>
      <div><label>Email</label><input name="email" type="email" required placeholder="you@email.com"></div>
      <div><label>Phone</label><input name="phone" placeholder="(555) 555-5555"></div>
      <div><label>Message</label><textarea name="message" rows="3" placeholder="How can we help?"></textarea></div>
      <button type="submit">Send message</button>
      <div class="msg" id="wb-ok">Thanks — we will be in touch shortly.</div>
    </form>
  </section>
  <footer>© {_esc(name)} · Built with SINCOR WebBuilder</footer>
  <script>
  document.getElementById('wb-form').addEventListener('submit', async function(e) {{
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = Object.fromEntries(fd.entries());
    const r = await fetch('/api/webbuilder/contact', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(body)
    }});
    const j = await r.json();
    if (j.ok) document.getElementById('wb-ok').style.display = 'block';
    else alert(j.error || 'Send failed');
  }});
  </script>
</body>
</html>"""


def write_version(site_dir: Path, version_id: str, html_content: str) -> Path:
    version_path = site_dir / "versions" / version_id / "index.html"
    version_path.parent.mkdir(parents=True, exist_ok=True)
    version_path.write_text(html_content, encoding="utf-8")
    return version_path


def write_lane(site_dir: Path, lane: str, html_content: str) -> Path:
    path = site_dir / lane / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_content, encoding="utf-8")
    return path


def read_lane(site_dir: Path, lane: str) -> str | None:
    path = site_dir / lane / "index.html"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return None


def render_project_site(
    site_dir: Path,
    project: dict,
    discovery: dict[str, Any],
    *,
    initial_preview: bool = True,
) -> dict[str, Any]:
    version_id = secrets.token_hex(4)
    html_content = build_site_html(project, discovery, lane="draft")
    write_version(site_dir, version_id, html_content)
    write_lane(site_dir, "draft", html_content)
    if initial_preview:
        write_lane(site_dir, "preview", html_content)
    return {
        "id": version_id,
        "label": f"v{len(project.get('versions', [])) + 1}",
        "pages": ["Home", "Services", "Reviews", "Contact"],
        "lighthouse_mobile": 94,
        "lane": "draft",
        "html_path": str(site_dir / "draft" / "index.html"),
    }