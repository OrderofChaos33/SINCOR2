"""OBS-01 Agent Vitals — "working end-to-end" walkthrough.

Runnable proof that a customer can view REAL vitals and receive a REAL
webhook alert. Every step runs against the actual stack:

1. Seed the live A2A fabric: one healthy agent (fresh heartbeat, real token
   budget usage, real settled task) and one stale agent (old heartbeat).
2. Start a genuine local HTTP receiver (127.0.0.1, ephemeral port) that
   captures webhook deliveries and verifies the HMAC-SHA256 signature.
3. Configure one alert rule (heartbeat_age_s > 300 -> receiver URL) via
   SINCOR_VITALS_ALERTS + SINCOR_VITALS_WEBHOOK_SECRET.
4. As a logged-in customer, GET /api/obs/vitals:
   - assert 200 and the fleet JSON shows live/stale health, real token
     budget numbers, and the measured cost-per-task from the settled task.
   - the read itself evaluates alerts; the dispatcher uses the REAL urllib
     transport (no stub) to POST to the receiver.
5. Assert the receiver captured exactly one delivery for the stale agent
   with a valid signature and the documented payload schema.
6. GET /obs/vitals as the customer: 200, BETA label, both agents rendered.
7. Assert /obs/vitals is not linked from /, /pricing, or /products
   (founder standing rule: beta stays unlisted).

Usage:
    PYTHONPATH=src:. FLASK_ENV=test ENVIRONMENT=test \\
      SECRET_KEY=test-secret-key-with-32-char-minimum-ok \\
      JWT_SECRET_KEY=test-jwt-secret-key-32-char-minimum-ok \\
      ADMIN_USERNAME=admin ADMIN_PASSWORD=admin-password-32-char-minimum-ok \\
      STRIPE_SECRET_KEY=sk_test_123456789012345678901234567890 \\
      ~/.venvs/sincor2/bin/python scripts/obs01_vitals_e2e.py

Exit 0 = the product works end-to-end. Any assertion failure or exception
means it does not, and OBS-01 stays unlisted.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))
sys.path.insert(0, REPO)

STEP = 0


def step(msg: str) -> None:
    global STEP
    STEP += 1
    print(f"[{STEP}] {msg}", flush=True)


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(f"FAILED: {msg}")
    print(f"    ok: {msg}", flush=True)


# ---------------------------------------------------------------------------
# 1. Real local webhook receiver (verifies signatures itself)
# ---------------------------------------------------------------------------


class Receiver(BaseHTTPRequestHandler):
    deliveries: list = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        Receiver.deliveries.append(
            {"path": self.path, "headers": dict(self.headers), "body": body}
        )
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"received":true}')

    def log_message(self, *args):  # keep output clean
        pass


def main() -> int:
    from sincor2.a2a_inbound import get_fabric, reset_fabric
    from sincor2.obs_skus import vitals as V

    reset_fabric()
    fabric = get_fabric()
    now_ms = int(time.time() * 1000)

    step("Seeding the live A2A fabric with two agents + one settled task")
    fabric.agents["E-walk-live"] = {
        "agent_id": "E-walk-live", "name": "Walkthrough Live Agent",
        "origin": "external", "registered_at": now_ms - 86_400_000,
        "last_heartbeat": now_ms, "reputation": 2.0,
    }
    fabric.agents["E-walk-stale"] = {
        "agent_id": "E-walk-stale", "name": "Walkthrough Stale Agent",
        "origin": "external", "registered_at": now_ms - 86_400_000,
        "last_heartbeat": now_ms - 900_000,  # 15 minutes ago -> silent
        "reputation": 0.5,
    }
    fabric.tasks["walk-t1"] = {
        "task_id": "walk-t1", "state": "settled",
        "assigned_to": "E-walk-live", "payout_axm": 2.5, "settled_at": now_ms,
    }

    # Real token-budget usage for the live agent.
    import tempfile
    from sincor2 import token_budget_controller as tbc

    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    ctrl = tbc.TokenBudgetController(ledger_path=__import__("pathlib").Path(tmp.name))
    ctrl.register_agent("E-walk-live", 1000)
    ctrl.record_usage("E-walk-live", 250)
    tbc._controller = ctrl  # singleton the read model consults
    check(True, "seeded 2 agents, 1 settled task, token budget 250/1000")

    step("Starting the real webhook receiver on 127.0.0.1")
    server = HTTPServer(("127.0.0.1", 0), Receiver)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    hook_url = f"http://127.0.0.1:{port}/hooks/vitals"
    print(f"    receiver: {hook_url}", flush=True)

    step("Configuring one alert rule + webhook secret via env")
    secret = "walkthrough-secret-" + os.urandom(8).hex()
    os.environ["SINCOR_VITALS_ALERTS"] = json.dumps([{
        "rule_id": "walk-hb", "metric": "heartbeat_age_s", "op": ">",
        "threshold": 300, "cooldown_s": 3600, "webhook_url": hook_url,
    }])
    os.environ["SINCOR_VITALS_WEBHOOK_SECRET"] = secret
    check(True, "rule: heartbeat_age_s > 300s -> local receiver")

    step("Logging in as a customer and reading /api/obs/vitals")
    from sincor2.mvp_app import app as mvp_app

    mvp_app.config["TESTING"] = True
    client = mvp_app.test_client()
    with client.session_transaction() as sess:
        sess["user_email"] = "customer@example.com"

    r = client.get("/api/obs/vitals")
    check(r.status_code == 200, f"GET /api/obs/vitals -> {r.status_code}")
    body = r.get_json()
    agents = {a["agent_id"]: a for a in body["fleet"]["agents"]}
    check(agents["E-walk-live"]["health"] == "live", "live agent health=live")
    check(agents["E-walk-stale"]["health"] == "silent", "stale agent health=silent")
    tb = agents["E-walk-live"]["token_budget"]
    check(tb["used_today"] == 250 and tb["remaining"] == 750,
          f"real token budget shown ({tb['used_today']}/{tb['daily_ceiling']})")
    check(agents["E-walk-live"]["cost_per_task_axm"] == 2.5,
          "measured cost-per-task = 2.5 AXM from the settled task")
    check(agents["E-walk-stale"]["cost_per_task_axm"] is None,
          "no settled tasks -> honest null, not 0.0")
    check(body["alerts"]["evaluated"] == 1, "one alert evaluated on the read")

    step("Verifying the real webhook delivery (signature + schema)")
    deadline = time.time() + 10
    while not Receiver.deliveries and time.time() < deadline:
        time.sleep(0.1)
    check(len(Receiver.deliveries) == 1,
          f"receiver captured exactly 1 delivery (got {len(Receiver.deliveries)})")
    d = Receiver.deliveries[0]
    check(d["path"] == "/hooks/vitals", "delivered to the configured path")
    headers = d["headers"]
    ts = headers.get("X-Sincor-Timestamp", "")
    sig = headers.get("X-Sincor-Signature", "")
    check(bool(ts) and bool(sig), "timestamp + signature headers present")
    check(V.verify_signature(secret, d["body"], ts, sig),
          "HMAC-SHA256 signature verifies with the shared secret")
    check(not V.verify_signature("wrong-secret", d["body"], ts, sig),
          "signature rejects a wrong secret")
    payload = json.loads(d["body"])
    check(payload["schema"] == "sincor.vitals.alert/1", "documented payload schema")
    check(payload["agent_id"] == "E-walk-stale"
          and payload["metric"] == "heartbeat_age_s"
          and payload["observed_value"] >= 300,
          f"alert names the stale agent (observed {payload['observed_value']}s)")
    check(body["alerts"]["dispatched"] == 1, "API reports the dispatch")

    step("Rendering the customer dashboard page")
    r = client.get("/obs/vitals")
    check(r.status_code == 200, f"GET /obs/vitals -> {r.status_code}")
    html = r.get_data(as_text=True)
    check("BETA" in html, "BETA label visible")
    check("E-walk-live" in html and "E-walk-stale" in html, "both agents rendered")
    check("noindex" in html, "noindex meta present")

    step("Confirming the beta stays unlisted (no public links)")
    for path in ("/", "/pricing", "/products"):
        r = client.get(path)
        if r.status_code == 200:
            check("/obs/vitals" not in r.get_data(as_text=True),
                  f"no link to /obs/vitals from {path}")

    step("Confirming anonymous visitors are bounced to login")
    anon = mvp_app.test_client()
    check(anon.get("/obs/vitals").status_code == 302, "/obs/vitals 302 for anon")
    check(anon.get("/api/obs/vitals").status_code == 401, "/api/obs/vitals 401 for anon")

    server.shutdown()
    print("\nWALKTHROUGH GREEN: customer views real vitals; real signed webhook alert delivered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
