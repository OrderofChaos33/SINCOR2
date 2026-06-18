#!/usr/bin/env python3
"""Push SINC/AXM billing stack and security fixes to OrderofChaos33/SINCOR2 main."""

from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "OrderofChaos33/SINCOR2"

FILES = [
    ("Dockerfile", "Railway /data volume dirs + compliance monitor default on"),
    ("config/railway_add_these.env", "Railway volume + compliance env checklist"),
    ("src/sincor2/data_paths.py", "Centralized persistent paths for Railway /data volume"),
    (".env.example", "Token billing env template — SINC/AXM, fiat disabled"),
    ("config/x402_pricing.yaml", "x402 micropayment pricing config"),
    ("src/sincor2/platform_payments.py", "SINC/AXM platform payments with spot cache"),
    ("src/sincor2/compliance_monitor.py", "Compliance monitor — Resend alerts + /data logs"),
    ("src/sincor2/daily_ops.py", "Daily ops reads persistent orders.db"),
    ("src/sincor2/value_engine.py", "Value engine reads persistent orders.db"),
    ("src/sincor2/agent_billing.py", "Agent revenue burn log"),
    ("src/sincor2/x402_payments.py", "HTTP 402 micropayment challenges"),
    ("src/sincor2/subscription_scheduler.py", "SINC subscription renewal reminders"),
    ("src/sincor2/social_links.py", "Sitewide social links context"),
    ("src/sincor2/token_security.py", "Token security API"),
    ("src/sincor2/mvp_app.py", "Token billing routes, admin auth on launch/webbuilder"),
    ("src/sincor2/content_agent.py", "Wire compliance guardrails on publish"),
    ("src/sincor2/outreach_engine.py", "Wire compliance guardrails on outreach send"),
    ("launch_content_engine/review_queue.py", "Guardrails on launch approve-and-post"),
    ("templates/buy_tokens.html", "Wallet checkout page"),
    ("templates/billing_tokens.html", "Wallet subscription dashboard"),
    ("templates/_social_links.html", "Social links partial"),
    ("templates/home.html", "Homepage — SINC buy CTA"),
    ("templates/pricing.html", "Pricing — SINC/AXM token billing"),
    ("templates/payment_cancel.html", "Payment cancel — token billing copy"),
    ("templates/privacy.html", "Privacy — token billing"),
    ("templates/terms.html", "Terms — token billing"),
    ("templates/earn.html", "Earn page"),
    ("templates/vertical_webbuilder.html", "WebBuilder vertical — SINC/AXM copy"),
    ("templates/launch_review.html", "Launch review — admin login"),
    ("templates/dashboard.html", "Dashboard demo-mode banner"),
]


def pat() -> str:
    for line in (ROOT / "onchain/.env").read_text(encoding="utf-8").splitlines():
        if line.startswith("GITHUB_PAT="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("GITHUB_PAT missing in onchain/.env")


def headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "sincor-billing-push",
    }


def get_sha(token: str, path: str) -> str | None:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/contents/{path}",
        headers=headers(token),
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())["sha"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def put_file(token: str, path: str, text: str, message: str) -> str:
    sha = get_sha(token, path)
    body: dict = {
        "message": message,
        "content": base64.b64encode(text.encode("utf-8")).decode(),
        "branch": "main",
    }
    if sha:
        body["sha"] = sha
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/contents/{path}",
        data=data,
        headers={**headers(token), "Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())["commit"]["html_url"]


def main() -> int:
    token = pat()
    pushed = 0
    skipped = 0
    for rel, msg in FILES:
        path = ROOT / rel
        if not path.exists():
            print("SKIP missing", rel)
            skipped += 1
            continue
        text = path.read_text(encoding="utf-8")
        url = put_file(token, rel.replace("\\", "/"), text, msg)
        print(rel, "->", url)
        pushed += 1
    print(f"\nDone: {pushed} pushed, {skipped} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())