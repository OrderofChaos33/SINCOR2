from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .runtime import UnderwriteRuntime, boot
from .viewer import render_demo
from .x402_seller import handle_echo


def make_handler(rt: UnderwriteRuntime):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, code: int, body: dict) -> None:
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _read(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n == 0:
                return {}
            return json.loads(self.rfile.read(n).decode() or "{}")

        def log_message(self, fmt: str, *args) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path == "/underwrite/demo":
                html = render_demo(rt.store).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
                return
            if path == "/a2a/skills/underwrite.echo":
                env = self.headers.get("X-SINCOR-ENVELOPE")
                sig = self.headers.get("PAYMENT-SIGNATURE")
                code, body = handle_echo(rt.facilitator, envelope_id=env, payment_sig=sig)
                self._json(code, body)
                return
            if path.startswith("/underwrite/envelopes/"):
                env = rt.store.get_envelope(path.rsplit("/", 1)[-1])
                if not env:
                    return self._json(404, {"error": "missing"})
                return self._json(200, env.to_dict())
            if path.startswith("/underwrite/mandates/"):
                m = rt.store.get_mandate(path.rsplit("/", 1)[-1])
                if not m:
                    return self._json(404, {"error": "missing"})
                return self._json(200, m.to_dict())
            if path == "/underwrite/audit":
                qs = parse_qs(parsed.query)
                rows = list(rt.store.iter_audit())
                if qs.get("agent_id"):
                    rows = [r for r in rows if r.get("agent_id") == qs["agent_id"][0]]
                return self._json(200, {"events": rows[-200:]})
            if path in ("/health", "/underwrite/health"):
                return self._json(200, {"ok": True, "tap": rt.tap_name})
            self._json(404, {"error": "not_found"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            body = self._read()
            if path == "/underwrite/mandates":
                m = rt.mandates.issue(
                    operator_id=body.get("operator_id") or self.headers.get("X-SINCOR-OPERATOR") or "court",
                    controller_wallet=body.get("controller_wallet", "0x" + "11" * 20),
                    agent_id=body["agent_id"],
                    max_notional_usd=str(body.get("max_notional_usd", "25.00")),
                    max_single_tx_usd=str(body.get("max_single_tx_usd", "5.00")),
                    hours=float(body.get("hours", 2)),
                    allowed_skills=body.get("allowed_skills") or ["underwrite.echo"],
                    allowed_payees=body.get("allowed_payees") or ["sincor:skill:underwrite.echo"],
                )
                return self._json(200, m.to_dict())
            if path.endswith("/revoke") and "/underwrite/mandates/" in path:
                mid = path.split("/underwrite/mandates/")[1].split("/")[0]
                m = rt.mandates.revoke(mid, body.get("by", "operator"))
                return self._json(200, m.to_dict())
            if path == "/underwrite/envelopes":
                agent_id = body["agent_id"]
                mandate = rt.mandates.active_for(agent_id)
                if mandate is None:
                    return self._json(400, {"error": "DENY_NO_MANDATE"})
                env = rt.envelopes.request(
                    mandate,
                    requested_usd=str(body.get("requested_usd", "5.00")),
                    skill_id=body.get("skill_id", "underwrite.echo"),
                    payee=body.get("payee", "sincor:skill:underwrite.echo"),
                    extra_context=body.get("context") or {},
                )
                return self._json(200, env.to_dict())
            if path == "/underwrite/kill":
                if body.get("mandate_id"):
                    m = rt.mandates.revoke(body["mandate_id"], body.get("by", "operator"))
                    return self._json(200, m.to_dict())
                if body.get("agent_id"):
                    m = rt.mandates.active_for(body["agent_id"])
                    if not m:
                        return self._json(404, {"error": "no_active_mandate"})
                    m = rt.mandates.revoke(m.mandate_id, body.get("by", "operator"))
                    return self._json(200, m.to_dict())
                return self._json(400, {"error": "need agent_id or mandate_id"})
            self._json(404, {"error": "not_found"})

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8787, data_dir: str | None = None) -> None:
    rt = boot(data_dir)
    httpd = ThreadingHTTPServer((host, port), make_handler(rt))
    print(f"underwrite listening http://{host}:{port}/underwrite/demo tap={rt.tap_name}")
    httpd.serve_forever()


if __name__ == "__main__":
    serve()
