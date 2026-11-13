"""Hunt 30 W-110 — free-ration per-IP cap must not trust X-Forwarded-For.

ProxyFix(x_for=1) rewrites ``request.remote_addr`` from the first
``X-Forwarded-For`` value, which is fully client-controlled unless
the edge overwrites it. The Wave 1 per-IP caps (30 free serves /
60 peeks per IP per day) are the stated defence against identity
rotation, so they must key on :func:`sincor2.free_ration.abuse_client_ip`:
the original TCP peer recorded by ProxyFix, unless that peer is an
operator-pinned trusted proxy (``SINCOR_TRUSTED_PROXY_IPS``).

Run: PYTHONPATH=src:. <venv>/python -m pytest \
  tests/pytest/test_hunt30_w110_trusted_proxy.py -q
"""
from __future__ import annotations

import pytest
from eth_account import Account
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

import sincor2.mvp_app  # noqa: F401  (import order: billing binding)
import sincor2.x402_payments as x402
from sincor2.free_ration import (
    abuse_client_ip,
    daily_ration_tracker,
    trusted_proxy_ips,
)
from sincor2.mvp_blueprints import billing

RESOURCE = "toa-micro-decision"
WINNER = {
    "options": [
        {"id": "strong", "label": "strong performer",
         "signals": [0.80, 0.82, 0.81, 0.83, 0.84]},
        {"id": "weak", "label": "weak performer",
         "signals": [0.20, 0.21, 0.19, 0.22, 0.20]},
    ],
    "context": {"horizon": 4},
}


@pytest.fixture(autouse=True)
def _clean_env_and_ration(monkeypatch):
    monkeypatch.delenv("SINCOR_TRUSTED_PROXY_IPS", raising=False)
    daily_ration_tracker.clear()
    yield
    daily_ration_tracker.clear()


@pytest.fixture()
def x402_db(tmp_path, monkeypatch):
    db = tmp_path / "orders.db"
    monkeypatch.setenv("ORDERS_DB_PATH", str(db))
    x402.init_x402_db()
    return db


@pytest.fixture()
def proxied_client(tmp_path, monkeypatch, x402_db):
    """Billing + A2A app wrapped in ProxyFix exactly like mvp_app does
    behind Railway (``ProxyFix(x_for=1)``), so ``remote_addr`` is the
    client-controlled first X-Forwarded-For value."""
    monkeypatch.setenv("TOA_DECISION_LOG", str(tmp_path / "toa.jsonl"))
    from sincor2.a2a_inbound import register as register_inbound
    from sincor2.a2a_inbound import reset_fabric
    from sincor2.a2a_integration import A2ARouter
    from sincor2.a2a_rate_limits import reset_a2a_limits

    reset_fabric()
    reset_a2a_limits()
    daily_ration_tracker.clear()
    app = Flask("hunt30_w110")
    app.register_blueprint(billing.bp)
    app.register_blueprint(A2ARouter().blueprint)
    register_inbound(app)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)
    app.config.update(TESTING=True)
    yield app.test_client()
    daily_ration_tracker.clear()
    reset_fabric()


def _orig(peer):
    return {"werkzeug.proxy_fix.orig": {"REMOTE_ADDR": peer}}


# ---------------------------------------------------------------------------
# Unit: abuse_client_ip / trusted_proxy_ips
# ---------------------------------------------------------------------------

class TestAbuseClientIp:
    def test_no_orig_returns_remote_addr(self):
        assert abuse_client_ip("1.2.3.4") == "1.2.3.4"
        assert abuse_client_ip("1.2.3.4", environ={}) == "1.2.3.4"
        assert abuse_client_ip(
            "1.2.3.4", environ={"REMOTE_ADDR": "5.5.5.5"}) == "1.2.3.4"

    def test_orig_present_untrusted_returns_orig_peer(self):
        # remote_addr here is the spoofed XFF-derived value; it must
        # be ignored when the TCP peer is not a pinned trusted proxy.
        assert abuse_client_ip(
            "9.9.9.9", environ=_orig("10.0.0.9")) == "10.0.0.9"

    def test_orig_present_trusted_returns_remote_addr(self, monkeypatch):
        monkeypatch.setenv("SINCOR_TRUSTED_PROXY_IPS", "10.0.0.9")
        assert abuse_client_ip(
            "9.9.9.9", environ=_orig("10.0.0.9")) == "9.9.9.9"

    def test_trusted_peer_with_empty_remote_returns_peer(self, monkeypatch):
        monkeypatch.setenv("SINCOR_TRUSTED_PROXY_IPS", "10.0.0.9")
        assert abuse_client_ip("", environ=_orig("10.0.0.9")) == "10.0.0.9"
        assert abuse_client_ip(None, environ=_orig("10.0.0.9")) == "10.0.0.9"

    def test_empty_everything_returns_unknown(self):
        assert abuse_client_ip() == "unknown"
        assert abuse_client_ip(None, environ={}) == "unknown"
        assert abuse_client_ip("", environ={}) == "unknown"
        assert abuse_client_ip(
            "", environ=_orig("")) == "unknown"
        assert abuse_client_ip(
            None, environ=_orig(None)) == "unknown"

    def test_env_parsing_tolerates_spaces_and_case(self, monkeypatch):
        monkeypatch.setenv(
            "SINCOR_TRUSTED_PROXY_IPS", " 10.0.0.9 , EXAMPLE.com ,, 192.168.0.1 ")
        assert trusted_proxy_ips() == {
            "10.0.0.9", "example.com", "192.168.0.1"}
        # Case-insensitive trust check on the peer as well.
        assert abuse_client_ip(
            "9.9.9.9", environ=_orig("Example.COM")) == "9.9.9.9"

    def test_env_is_read_at_call_time(self, monkeypatch):
        environ = _orig("10.0.0.9")
        assert abuse_client_ip("9.9.9.9", environ=environ) == "10.0.0.9"
        monkeypatch.setenv("SINCOR_TRUSTED_PROXY_IPS", "10.0.0.9")
        assert abuse_client_ip("9.9.9.9", environ=environ) == "9.9.9.9"
        monkeypatch.delenv("SINCOR_TRUSTED_PROXY_IPS")
        assert abuse_client_ip("9.9.9.9", environ=environ) == "10.0.0.9"


# ---------------------------------------------------------------------------
# Integration: XFF rotation cannot bypass the per-IP free-serve cap
# ---------------------------------------------------------------------------

def test_rotating_xff_does_not_bypass_per_ip_cap(proxied_client, monkeypatch):
    monkeypatch.setattr(daily_ration_tracker, "per_ip_daily_cap", 3)
    client = proxied_client
    wallets = [Account.create().address for _ in range(4)]
    # The hunt PoC shape: rotating XFF per request, fresh wallet
    # identities (identity rotation is what the per-IP cap bounds).
    for i, wallet in enumerate(wallets[:3]):
        resp = client.post(
            f"/api/paid/{RESOURCE}?wallet={wallet}", json=WINNER,
            headers={"X-Forwarded-For": f"9.9.9.{i + 1}"})
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["x402_free_ration"] is True
    # Cap is keyed on the real TCP peer (test client: 127.0.0.1), not
    # on any of the spoofed XFF values.
    assert daily_ration_tracker.ip_used("127.0.0.1") == 3
    assert daily_ration_tracker.ip_used("9.9.9.1") == 0
    # The next rotated identity is NOT served free — it falls through
    # to the normal paid challenge.
    resp = client.post(
        f"/api/paid/{RESOURCE}?wallet={wallets[3]}", json=WINNER,
        headers={"X-Forwarded-For": "9.9.9.99"})
    assert resp.status_code == 402, resp.get_json()
    assert resp.get_json().get("x402_free_ration") is not True


def test_trusted_proxy_peer_lets_xff_define_cap_bucket(
        proxied_client, monkeypatch):
    # When the immediate peer IS pinned trusted, the ProxyFix-derived
    # address is attested by the edge and keys the cap as before.
    monkeypatch.setenv("SINCOR_TRUSTED_PROXY_IPS", "127.0.0.1")
    monkeypatch.setattr(daily_ration_tracker, "per_ip_daily_cap", 2)
    client = proxied_client
    wallets = [Account.create().address for _ in range(3)]
    for i, wallet in enumerate(wallets):
        resp = client.post(
            f"/api/paid/{RESOURCE}?wallet={wallet}", json=WINNER,
            headers={"X-Forwarded-For": f"9.9.8.{i + 1}"})
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["x402_free_ration"] is True
    assert daily_ration_tracker.ip_used("9.9.8.1") == 1
    assert daily_ration_tracker.ip_used("127.0.0.1") == 0


def test_distinct_real_peers_have_distinct_caps(proxied_client, monkeypatch):
    monkeypatch.setattr(daily_ration_tracker, "per_ip_daily_cap", 2)
    client = proxied_client
    wallets = [Account.create().address for _ in range(4)]
    peer_a = {"REMOTE_ADDR": "10.11.0.1"}
    peer_b = {"REMOTE_ADDR": "10.11.0.2"}
    for wallet in wallets[:2]:
        resp = client.post(
            f"/api/paid/{RESOURCE}?wallet={wallet}", json=WINNER,
            environ_overrides=peer_a)
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["x402_free_ration"] is True
    # Peer A's cap is exhausted for a third fresh identity...
    resp = client.post(
        f"/api/paid/{RESOURCE}?wallet={wallets[2]}", json=WINNER,
        environ_overrides=peer_a)
    assert resp.status_code == 402, resp.get_json()
    # ...while a distinct real peer still has its own bucket.
    resp = client.post(
        f"/api/paid/{RESOURCE}?wallet={wallets[3]}", json=WINNER,
        environ_overrides=peer_b)
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["x402_free_ration"] is True


# ---------------------------------------------------------------------------
# Integration: peek cap under XFF rotation (billing + A2A surfaces)
# ---------------------------------------------------------------------------

def test_rotating_xff_does_not_reset_billing_peek_cap(
        proxied_client, monkeypatch):
    monkeypatch.setattr(daily_ration_tracker, "peek_per_ip_daily_cap", 2)
    path = "/api/x402/peek/market-data-read"
    for i in range(2):
        resp = proxied_client.get(
            path, headers={"X-Forwarded-For": f"9.9.7.{i + 1}"})
        assert resp.status_code == 200, resp.get_json()
    resp = proxied_client.get(
        path, headers={"X-Forwarded-For": "9.9.7.99"})
    assert resp.status_code == 429, resp.get_json()
    assert resp.get_json()["error"] == "peek_rate_limited"
    assert daily_ration_tracker.peek_used("127.0.0.1") == 2


def test_rotating_xff_does_not_reset_a2a_peek_cap(
        proxied_client, monkeypatch):
    monkeypatch.setattr(daily_ration_tracker, "peek_per_ip_daily_cap", 2)
    path = "/api/a2a/peek/lead-enrichment"
    for i in range(2):
        resp = proxied_client.get(
            path, headers={"X-Forwarded-For": f"9.9.6.{i + 1}"})
        assert resp.status_code == 200, resp.get_json()
    resp = proxied_client.get(
        path, headers={"X-Forwarded-For": "9.9.6.99"})
    assert resp.status_code == 429, resp.get_json()
    assert resp.get_json()["error"] == "peek_rate_limited"
    assert daily_ration_tracker.peek_used("127.0.0.1") == 2
