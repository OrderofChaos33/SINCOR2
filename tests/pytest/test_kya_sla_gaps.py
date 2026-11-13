"""Gap-closure tests for SLAMonitor: ping-on-unknown, unsubscribe, uptime."""
from __future__ import annotations

import pytest

from sincor2.kya.sla import SLAMonitor


@pytest.fixture()
def monitor(tmp_path, monkeypatch):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    return SLAMonitor()


def test_ping_unknown_kya_id_raises(monitor):
    with pytest.raises(KeyError):
        monitor.ping("ghost-id-1")


def test_subscribe_then_ping_works(monitor):
    monitor.subscribe("kya-a")
    receipt = monitor.ping("kya-a", ok=True, latency_ms=42)
    assert receipt["kya_id"] == "kya-a"
    assert receipt["ok"] is True
    assert "attestation_hash" in receipt


def test_ping_after_unsubscribe_raises(monitor):
    monitor.subscribe("kya-b")
    monitor.ping("kya-b")
    assert monitor.unsubscribe("kya-b") is True
    with pytest.raises(KeyError):
        monitor.ping("kya-b")


def test_unsubscribe_absent_returns_false(monitor):
    assert monitor.unsubscribe("never-subscribed") is False


def test_unsubscribe_absent_after_removal_returns_false(monitor):
    monitor.subscribe("kya-c")
    assert monitor.unsubscribe("kya-c") is True
    assert monitor.unsubscribe("kya-c") is False


def test_uptime_math(monitor):
    monitor.subscribe("kya-d")
    monitor.ping("kya-d", ok=True)
    monitor.ping("kya-d", ok=True)
    monitor.ping("kya-d", ok=True)
    monitor.ping("kya-d", ok=False)
    u = monitor.uptime("kya-d")
    assert u["kya_id"] == "kya-d"
    assert u["ok"] == 3
    assert u["miss"] == 1
    assert u["uptime_pct"] == 0.75
    assert u["last_ts"] > 0
    assert u["interval_s"] == 300


def test_uptime_none_with_no_pings(monitor):
    monitor.subscribe("kya-e")
    u = monitor.uptime("kya-e")
    assert u["uptime_pct"] is None
    assert u["ok"] == 0
    assert u["miss"] == 0


def test_uptime_unknown_raises(monitor):
    with pytest.raises(KeyError):
        monitor.uptime("nope")


def test_receipts_preserved_after_unsubscribe(monitor):
    monitor.subscribe("kya-f")
    monitor.ping("kya-f")
    monitor.ping("kya-f")
    monitor.unsubscribe("kya-f")
    rows = monitor.list_receipts("kya-f")
    assert len(rows) == 2
    assert all(r["kya_id"] == "kya-f" for r in rows)


def test_sub_survives_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    m1 = SLAMonitor()
    m1.subscribe("kya-g")
    m1.ping("kya-g")
    m1.ping("kya-g")
    m2 = SLAMonitor()
    u = m2.uptime("kya-g")
    assert u["ok"] == 2
    assert u["interval_s"] == 300
    assert len(m2.list_receipts("kya-g")) == 2


def test_sla_ping_route_404_for_unknown(app):
    client = app.test_client()
    resp = client.post("/v1/sla/ping", json={"kya_id": "route-ghost"})
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "not subscribed"}


def test_sla_ping_route_200_after_subscribe(app):
    client = app.test_client()
    r = client.post("/v1/sla/subscribe", json={"kya_id": "route-ok"})
    assert r.status_code == 200
    resp = client.post("/v1/sla/ping", json={"kya_id": "route-ok", "ok": True})
    assert resp.status_code == 200
    assert resp.get_json()["kya_id"] == "route-ok"
