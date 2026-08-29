"""P0-4/5/6: A2A discovery 200s on mvp_app, payment-gated dashboard, EXECUTE_LIVE hygiene."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def mvp_client():
    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-32-char-minimum-ok")
    os.environ.setdefault("ADMIN_USERNAME", "admin")
    os.environ.setdefault("ADMIN_PASSWORD", "admin-password-32-char-minimum-ok")
    from sincor2.mvp_app import app

    app.config["TESTING"] = True
    app.config["SERVER_NAME"] = None
    return app.test_client()


def test_a2a_agent_card_200(mvp_client):
    r = mvp_client.get("/.well-known/agent-card.json")
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("name")
    skills = {s["id"] for s in body.get("skills") or []}
    assert "lead-enrichment" in skills


def test_a2a_legacy_agent_json_200(mvp_client):
    r = mvp_client.get("/.well-known/agent.json")
    assert r.status_code == 200
    assert r.get_json()


def test_a2a_agents_200(mvp_client):
    r = mvp_client.get("/api/a2a/agents")
    assert r.status_code == 200
    data = r.get_json()
    assert isinstance(data.get("agents"), list)
    assert data["agents"]


def test_a2a_quote_known_skill_200(mvp_client):
    r = mvp_client.get("/api/a2a/quote?skill_id=lead-enrichment")
    assert r.status_code == 200
    data = r.get_json()
    assert data.get("skill_id") == "lead-enrichment"
    assert data.get("pay_to")
    assert "axm_price_wei" in data


def test_a2a_quote_unknown_not_404(mvp_client):
    r = mvp_client.get("/api/a2a/quote")
    assert r.status_code != 404


def test_dashboard_anonymous_redirects_login(mvp_client):
    r = mvp_client.get("/dashboard", follow_redirects=False)
    assert r.status_code in (301, 302)
    loc = r.headers.get("Location", "")
    assert "/login" in loc


def test_dashboard_logged_in_unpaid_redirects_buy(mvp_client):
    with mvp_client.session_transaction() as sess:
        sess["user_email"] = "unpaid-p0@example.com"
        sess["username"] = "unpaid"
    r = mvp_client.get("/dashboard", follow_redirects=False)
    assert r.status_code in (301, 302)
    loc = r.headers.get("Location", "")
    assert "/buy" in loc
    assert "no_active_subscription" in loc


def test_dashboard_paid_no_fabricated_metrics(mvp_client):
    from sincor2.mvp_app import app, get_db

    email = "paid-p0@example.com"
    with app.app_context():
        db = get_db()
        db.execute(
            """INSERT OR REPLACE INTO orders
               (order_id, customer_email, product_name, amount, currency,
                payment_status, delivery_status, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                "ord-p0-paid",
                email,
                "Starter",
                297,
                "USD",
                "completed",
                "pending",
                datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            ),
        )
        db.commit()
    with mvp_client.session_transaction() as sess:
        sess["user_email"] = email
        sess["username"] = "paid"
    r = mvp_client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "1,247" not in html
    assert "Sample telemetry" not in html
    assert "Identified 47 qualified leads" not in html
    assert "Live telemetry coming soon" in html
    assert "—" in html


def test_execute_live_not_hardcoded_true():
    """Committed Python must never force EXECUTE_LIVE on (env-gated only)."""
    assign = re.compile(r"""(?:^|\n)\s*EXECUTE_LIVE\s*=\s*(True|1)\s*(?:#|$)""")
    environ = re.compile(r"""(?:os\.environ(?:\[|\.setdefault\())\s*['\"]EXECUTE_LIVE['\"][^)\n]{0,40}['\"]1['\"]""")
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if assign.search(text) or environ.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"EXECUTE_LIVE hardcoded on: {offenders}"
