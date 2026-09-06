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
    body = "\n".join(rows) or "<tr><td colspan=7>no events yet</td></tr>"
    return f"""<!doctype html>
<html><head><meta charset=\"utf-8\"><title>SINCOR Underwrite</title>
<style>
body{{font-family:ui-monospace,Menlo,Consolas,monospace;background:#0b0d10;color:#e8edf2;margin:24px}}
h1{{font-size:16px;letter-spacing:.04em}}
.sub{{color:#8b98a5;margin-bottom:18px}}
table{{border-collapse:collapse;width:100%;font-size:12px}}
th,td{{border-bottom:1px solid #222;padding:6px 8px;text-align:left;vertical-align:top}}
th{{color:#8b98a5;font-weight:500}}
</style></head>
<body>
<h1>SINCOR AGENT UNDERWRITING</h1>
<div class=\"sub\">mandate → envelope → x402 spend → deny/kill. This page is the handshake artifact.</div>
<table>
<thead><tr><th>time</th><th>kind</th><th>agent</th><th>env</th><th>amount</th><th>reason</th><th>toa / remaining</th></tr></thead>
<tbody>{body}</tbody>
</table>
</body></html>"""
