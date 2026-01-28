# flyer_renderer.py  Persuasive promo flyer with hero, logo, testimonial, guarantee, CTA
import json, base64
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
INBOX   = RUNTIME / "inbox"
WF      = RUNTIME / "write_manifest.json"
OUT     = ROOT / "output"
ASSETS  = ROOT / "assets"

def pick(*names):
    for n in names:
        p = ASSETS / n
        if p.exists(): return p
    return None

jobs = sorted(INBOX.glob("job_*.json"))
if not jobs:
    print("[flyer_renderer] no jobs"); raise SystemExit(0)
job = json.loads(jobs[-1].read_text(encoding="utf-8-sig"))

biz    = job.get("biz","Your Business")
city   = job.get("city","Your City")
price  = job.get("price","")
email  = job.get("email","")
phone  = job.get("phone","")
payURL = job.get("payURL","")
ts     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

ASSETS.mkdir(parents=True, exist_ok=True)
logo = pick("logo.png","logo.jpg","logo.jpeg","logo.svg")
hero = pick("hero.jpg","hero.png","before_after.jpg","car_hero.jpg")

logo_html = f'<img src="{logo.as_uri()}" alt="{biz} logo" style="height:64px;">' if logo else ''
hero_html = f'<img src="{hero.as_uri()}" alt="Detailing hero" style="width:100%; height:auto; border-radius:14px;">' if hero else ''

headline = f"Make your ride look brand-newthis week."
subhead  = f"{city}  Offer generated {ts}"
benefits = [
  "Showroom shine in one afternoon",
  "Interior deep clean + exterior polish",
  "Headlight restoration included",
]
testimonial_quote = "Absolutely the best detail weve hadcar looked new. Booked again for next month."
testimonial_name  = "Local Customer"

html = f"""<!doctype html>
<html lang="en"><meta charset="utf-8">
<title>{biz}  Detailing Offer</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{ --ink:#0B132B; --pri:#112240; --acc:#FF3B30; --ok:#14C38E; --bg:#F5F7FB; }}
  * {{ box-sizing:border-box }}
  body {{ margin:0; font-family: Inter, -apple-system, Segoe UI, Roboto, Arial, sans-serif; background:var(--bg); color:var(--ink); }}
  .wrap {{ max-width: 980px; margin: 36px auto; padding: 0 18px; }}
  .card {{ background:#fff; border:1px solid #E7ECF3; border-radius:20px; box-shadow: 0 12px 30px rgba(16,30,54,.06); overflow:hidden }}
  header.hero {{ background: linear-gradient(135deg, var(--pri), #1F3D7A); color:#fff; padding: 24px 28px; display:flex; justify-content:space-between; align-items:center }}
  h1 {{ margin:4px 0 0; font-size: 36px; letter-spacing:.3px }}
  p.sub {{ margin:6px 0 0; opacity:.9 }}
  .grid {{ display:grid; grid-template-columns: 2fr 1fr; gap:22px; padding: 24px 24px 28px; }}
  .price {{ font-size:56px; font-weight:900; color:var(--acc); margin:10px 0 }}
  .kicker {{ font-weight:700; letter-spacing:.2px; text-transform:uppercase; opacity:.85 }}
  ul.bens {{ margin: 8px 0 0 18px; line-height:1.6 }}
  .cta {{ display:flex; gap:16px; flex-wrap:wrap; margin-top:16px }}
  .btn {{ display:inline-block; background:var(--acc); color:#fff; padding:14px 20px; border-radius:12px; font-weight:800; text-decoration:none }}
  .btn.sec {{ background:#fff; color:var(--pri); border:2px solid #D8E0EF }}
  .pill {{ display:inline-block; padding:6px 10px; background:#0b132b10; border:1px solid #1f3d7a22; border-radius:999px; font-size:12px; margin-right:8px }}
  .ribbon {{ position:relative; top:-12px; display:inline-block; background:#ffedeb; color:#9b1c10; border:1px solid #ffd1cc; padding:6px 10px; border-radius:10px; font-weight:700 }}
  .aside {{ background:#fcfdff; border:1px dashed #e0e7f3; border-radius:14px; padding:16px }}
  .testi {{ background:#fff7f0; border:1px solid #ffe4cc; padding:12px 14px; border-radius:12px; font-style:italic }}
  footer {{ color:#5a6b86; font-size:12px; padding: 14px 20px; }}
  .badges {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:10px }}
  .badge {{ background:#eef8f2; color:#0c7a4a; border:1px solid #cdeedd; padding:6px 10px; border-radius:10px; font-size:12px; font-weight:700 }}
</style>

<div class="wrap">
  <div class="card">
    <header class="hero">
      <div>
        <div>{logo_html}</div>
        <h1>{biz}</h1>
        <p class="sub">{subhead}</p>
      </div>
      <div class="aside" style="min-width:260px">
        <div class="kicker">Todays Offer</div>
        <div class="price">${price}</div>
        <div class="cta">
          <a class="btn" href="{payURL}">Book Now</a>
          <a class="btn sec" href="tel:{phone}">Call {phone}</a>
        </div>
        <div class="badges">
          <span class="badge">Satisfaction Guaranteed</span>
          <span class="badge">Locally Trusted</span>
        </div>
      </div>
    </header>

    <div style="padding:20px 22px 0">{hero_html}</div>

    <section class="grid">
      <div>
        <div class="ribbon">Before  After in one afternoon</div>
        <ul class="bens">
          {"".join(f"<li>{b}</li>" for b in benefits)}
        </ul>
        <div class="testi" style="margin-top:14px">
          {testimonial_quote}  <b>{testimonial_name}</b>
        </div>
      </div>

      <div class="aside">
        <div class="kicker">Contact</div>
        <div style="margin:6px 0 2px;">{email}</div>
        <div>{phone}</div>
        <hr style="border:none; border-top:1px solid #eef2f8; margin:12px 0">
        <div class="kicker">Secure Payment</div>
        <a href="{payURL}">{payURL}</a>
        <div style="margin-top:10px; font-size:12px; color:#5b6a85;">
          We accept major cards. Your deposit secures your booking slot.
        </div>
      </div>
    </section>

    <footer>
      Generated by SINCOR  {ts}
    </footer>
  </div>
</div>
</html>"""

OUT.mkdir(parents=True, exist_ok=True)
target = OUT / "flyer.html"
write = {"path": str(target), "text": html}

existing = []
if WF.exists():
    try:
        existing = json.loads(WF.read_text(encoding="utf-8")).get("writes", [])
    except Exception:
        existing = []
WF.write_text(json.dumps({"writes": existing + [write]}, indent=2), encoding="utf-8")
print(f"[flyer_renderer] staged {target}")
