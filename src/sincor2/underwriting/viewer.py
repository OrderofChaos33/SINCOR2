from __future__ import annotations

from html import escape

from .store import UnderwriteStore


def render_demo(store: UnderwriteStore) -> str:
    events = list(store.iter_audit())[-80:]
    rows = []
    for ev in reversed(events):
        p = ev.get("payload") or {}
        rows.append(
            "<tr>"
            f"<td>{escape(str(ev.get('ts','')))}</td>"
            f"<td>{escape(str(ev.get('kind','')))}</td>"
            f"<td>{escape(str(ev.get('agent_id','')))}</td>"
            f"<td>{escape(str(ev.get('envelope_id') or ''))[:8]}</td>"
            f"<td>{escape(str(p.get('amount_usd', p.get('requested_usd', ''))))}</td>"
            f"<td>{escape(str(p.get('reason', p.get('reasons', ''))))}</td>"
            f"<td>{escape(str(p.get('rationale', p.get('remaining', ''))[:120]))}</td>"
            "</tr>"
        )
    body = "\n".join(rows) or "<tr><td colspan=7>no events yet — run a mandate, then convert</td></tr>"
    return f"""<!doctype html>
<html><head><meta charset=\"utf-8\"><title>SINCOR Underwrite</title>
<style>
body{{font-family:ui-monospace,Menlo,Consolas,monospace;background:#0b0d10;color:#e8edf2;margin:24px}}
h1{{font-size:16px;letter-spacing:.04em}}
.sub{{color:#8b98a5;margin-bottom:18px}}
table{{border-collapse:collapse;width:100%;font-size:12px}}
th,td{{border-bottom:1px solid #222;padding:6px 8px;text-align:left;vertical-align:top}}
th{{color:#8b98a5;font-weight:500}}
.cta{{margin:22px 0;padding:16px;border:1px solid #1f6f4a;background:#0d2418;border-radius:8px}}
.cta a{{color:#7dffa8;margin-right:16px}}
.legacy{{color:#8b98a5;font-size:12px;margin-top:10px}}
</style></head>
<body>
<h1>SINCOR AGENT UNDERWRITING</h1>
<div class=\"sub\">mandate → envelope → x402 spend → deny/kill. Trust stack: KYA register, underwriting score, AXM/x402 settle. Passport is the badge only.</div>
<div class=\"cta\">
  Demo complete. Paid conversion is the canon checkout — Stripe USDC fallback when token spot is unavailable.
  <div style=\"margin-top:10px\">
    <a href=\"/underwrite/complete?plan=starter\">Starter $297</a>
    <a href=\"/underwrite/complete?plan=professional\">Professional $997</a>
    <a href=\"/underwrite/complete?plan=enterprise\">Enterprise $2997</a>
  </div>
</div>
<table>
<thead><tr><th>time</th><th>kind</th><th>agent</th><th>env</th><th>amount</th><th>reason</th><th>toa / remaining</th></tr></thead>
<tbody>{body}</tbody>
</table>
<div class=\"legacy\">Duplicate identity surfaces are marked legacy. Canonical registration is KYA, not this page.</div>
</body></html>"""
