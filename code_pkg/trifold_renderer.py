# trifold_renderer.py  6-panel tri-fold (US Letter landscape), two HTMLs: outside + inside
# Expects assets:
#   assets\trifold_outside.jpg   (outside artwork)
#   assets\trifold_inside.jpg    (inside artwork)
# Optional: assets\logo.png

import json
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
OUT  = ROOT / "output" / "trifold"
AS   = ROOT / "assets"
WF   = ROOT / "runtime" / "write_manifest.json"

OUT.mkdir(parents=True, exist_ok=True)

def css():
    return """
    @page { size:279.4mm 215.9mm; margin:0; } /* 11in x 8.5in */
    html,body{margin:0;padding:0;background:#f7f9fc;color:#0b132b;font-family:Inter,-apple-system,Segoe UI,Roboto,Arial,sans-serif}
    .sheet{width:279.4mm;height:215.9mm;position:relative;overflow:hidden;background:#fff}
    .print-safe{position:absolute;inset:8mm;display:grid;grid-template-columns:1fr 1fr 1fr;gap:0}
    .panel{position:relative;overflow:hidden;border-left:.25mm solid rgba(0,0,0,.06)}
    .panel:first-child{border-left:none}
    .bg{position:absolute;inset:0;background-size:cover;background-position:center}
    .overlay{position:absolute;inset:0;padding:8mm;display:flex;flex-direction:column;justify-content:flex-end;background:linear-gradient(180deg,rgba(0,0,0,0) 40%,rgba(0,0,0,.35) 100%);color:#fff}
    .tag{font-size:11pt;letter-spacing:.3px;opacity:.9}
    h1{margin:4mm 0 1mm;font-size:26pt;line-height:1.1}
    .price{font-size:28pt;font-weight:900;margin-top:1mm}
    .cta{margin-top:4mm;display:inline-block;background:#ff3b30;color:#fff;padding:4mm 6mm;border-radius:3mm;font-weight:800;text-decoration:none}
    .guide{position:absolute;top:0;bottom:0;width:.3mm;background:rgba(20,23,31,.10)}
    .guide.g1{left:calc(279.4mm/3)} .guide.g2{left:calc(279.4mm*2/3)}
    .logo{position:absolute;top:6mm;right:6mm;height:14mm}
    """

def file_uri(p: Path|None):
    if not p: return ""
    try: return p.resolve().as_uri()
    except Exception: return ""

def stage(path: Path, text: str):
    write = {"path": str(path), "text": text}
    existing = []
    if WF.exists():
        try: existing = json.loads(WF.read_text(encoding="utf-8")).get("writes", [])
        except Exception: existing = []
    WF.write_text(json.dumps({"writes": existing + [write]}, indent=2), encoding="utf-8")

def side_html(name, bg_url, logo_url=None, text=None):
    t = text or {}
    panels_html = []
    for i in range(3):
        d = t.get(str(i), {})
        tag = d.get("tag", "")
        h1  = d.get("h1", "")
        price = d.get("price", "")
        cta = d.get("cta", "")
        cta_href = d.get("cta_href", "#")
        cta_html = f'<a class="cta" href="{cta_href}">{cta}</a>' if cta else ""
        logo_html = f'<img class="logo" src="{logo_url}" alt="logo">' if logo_url else ""
        panels_html.append(
            f'<div class="panel"><div class="bg" style="background-image:url(\'{bg_url}\');"></div>'
            f'{logo_html}<div class="overlay"><div class="tag">{tag}</div><h1>{h1}</h1>'
            f'<div class="price">{price}</div>{cta_html}</div></div>'
        )
    guides = '<div class="guide g1"></div><div class="guide g2"></div>'
    return f'<!doctype html><html><meta charset="utf-8"><title>{name}</title><style>{css()}</style>' \
           f'<body><div class="sheet"><div class="print-safe">{"".join(panels_html)}</div>{guides}</div></body></html>'

def main():
    outside = AS / "trifold_outside.jpg"
    inside  = AS / "trifold_inside.jpg"
    logo    = AS / "logo.png"

    if not outside.exists():
        for alt in [AS/"1.jpg", AS/"outside.jpg", AS/"front.jpg", AS/"brochure_out.jpg"]:
            if alt.exists(): outside = alt; break
    if not inside.exists():
        for alt in [AS/"2.jpg", AS/"inside.jpg", AS/"back.jpg", AS/"brochure_in.jpg"]:
            if alt.exists(): inside = alt; break

    out_uri = file_uri(outside) if outside.exists() else "about:blank"
    in_uri  = file_uri(inside)  if inside.exists()  else "about:blank"
    logo_uri= file_uri(logo)    if logo.exists()    else None

    outside_text = {
      "0": {"tag":"About Us","h1":"Trusted Local Detailers"},
      "1": {"tag":"Services","h1":"Interior  Exterior  Ceramic"},
      "2": {"tag":"Offer","h1":"Showroom Shine This Week","price":"$275","cta":"Book Now","cta_href":"#"}
    }
    inside_text = {
      "0": {"tag":"Process","h1":"Deep Clean  Decon  Protect"},
      "1": {"tag":"Results","h1":"Before  After In One Day"},
      "2": {"tag":"Guarantee","h1":"Satisfaction Guaranteed"}
    }

    html_out = side_html("TriFold  Outside", out_uri, logo_uri, outside_text)
    html_in  = side_html("TriFold  Inside",  in_uri,  logo_uri, inside_text)

    stage(OUT / "trifold_outside.html", html_out)
    stage(OUT / "trifold_inside.html",  html_in)
    print("[trifold_renderer] staged outside+inside HTML")

if __name__ == "__main__":
    main()
