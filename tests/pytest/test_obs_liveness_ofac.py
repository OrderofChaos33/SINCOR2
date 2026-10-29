"""Observability builds #6 + #7 — OFAC freshness badge + liveness mount.

#6: ``GET /api/compliance/ofac-status`` returns ONLY snapshot provenance
(publication date, entry count, age, fresh/stale) fed by
``sanctions_screen.load_screen_snapshot()`` + ``_provenance()``. No paid
data, no screening verdicts. It fails soft — 200 with an unknown state,
never a 500 — when the snapshot cannot be loaded.

#7: ``GET /.well-known/liveness-agents.json`` must resolve on the REAL
production app (``sincor2.mvp_app``). It 404'd in production because
``liveness.attach_liveness`` was only ever called in the legacy
``sincor2.app`` module, which production does not run. These tests pin
both routes against the production app object itself — the exact class
of bug that shipped because earlier tests used throwaway Flask apps.
"""
from __future__ import annotations

import datetime

import pytest

import sincor2.sanctions_screen as ss
from sincor2.ofac_status import ofac_status


def _fixture_snapshot(*, age_seconds: float) -> dict:
    fetched = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        seconds=age_seconds)
    return {
        "source": "test-source",
        "source_url": "https://example.invalid/sdn.xml",
        "publication_date": "2026-10-28",
        "fetched_at": fetched.isoformat(),
        "entry_count": 19484,
        "record_count": 19484,
        "entries": [],
    }


# ---------------------------------------------------------------------------
# #6 unit layer: payload builder against injected snapshots
# ---------------------------------------------------------------------------

def test_ofac_status_fresh_snapshot(monkeypatch):
    monkeypatch.setattr(
        ss, "load_screen_snapshot",
        lambda: _fixture_snapshot(age_seconds=60))
    payload = ofac_status()
    assert payload["ok"] is True
    assert payload["status"] == "fresh"
    assert payload["fresh"] is True
    assert payload["stale"] is False
    assert payload["snapshot_publication_date"] == "2026-10-28"
    assert payload["snapshot_entry_count"] == 19484
    assert 0 <= payload["snapshot_age_seconds"] < 3600


def test_ofac_status_stale_snapshot(monkeypatch):
    monkeypatch.setattr(
        ss, "load_screen_snapshot",
        lambda: _fixture_snapshot(age_seconds=10 * 24 * 3600))
    payload = ofac_status()
    assert payload["ok"] is True
    assert payload["status"] == "stale"
    assert payload["fresh"] is False
    assert payload["stale"] is True
    assert payload["snapshot_age_seconds"] >= 10 * 24 * 3600 - 60


def test_ofac_status_missing_snapshot_fails_soft(monkeypatch):
    def _boom():
        raise RuntimeError("snapshot file gone")

    monkeypatch.setattr(ss, "load_screen_snapshot", _boom)
    payload = ofac_status()  # must not raise
    assert payload["status"] == "unknown"
    assert payload["fresh"] is False
    assert payload["stale"] is True


def test_ofac_status_is_provenance_only(monkeypatch):
    monkeypatch.setattr(
        ss, "load_screen_snapshot",
        lambda: _fixture_snapshot(age_seconds=60))
    payload = ofac_status()
    # No snapshot entries, no screening surface of any kind.
    for forbidden in ("entries", "verdict", "status_code", "match",
                      "screened", "address"):
        assert forbidden not in payload


# ---------------------------------------------------------------------------
# #6 + #7 production layer: routes resolve on the real production app
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def prod_client():
    from sincor2.mvp_app import app

    return app.test_client()


def test_production_app_serves_liveness_disclosure(prod_client):
    r = prod_client.get("/.well-known/liveness-agents.json")
    assert r.status_code == 200, r.data
    body = r.get_json() or {}
    agents = body.get("agents") or []
    assert len(agents) == 6
    ids = {a.get("agent_id") for a in agents}
    assert "sincor-liveness-01" in ids
    for a in agents:
        assert str(a.get("wallet", "")).startswith("0x")
        assert "SINCOR-operated" in a.get("note", "")
    assert "genesis-liveness" in body.get("policy", "")


def test_production_app_serves_ofac_status(prod_client):
    r = prod_client.get("/api/compliance/ofac-status")
    assert r.status_code == 200, r.data
    body = r.get_json() or {}
    assert body.get("snapshot_publication_date")
    assert isinstance(body.get("snapshot_entry_count"), int)
    assert body["snapshot_entry_count"] > 15000
    assert isinstance(body.get("snapshot_age_seconds"), int)
    assert body["snapshot_age_seconds"] >= 0
    assert isinstance(body.get("fresh"), bool)
    assert body.get("stale") is (not body["fresh"])
    assert body.get("status") in {"fresh", "stale"}


def test_production_ofac_status_fails_soft_without_snapshot(
        prod_client, monkeypatch):
    def _boom():
        raise RuntimeError("snapshot file gone")

    monkeypatch.setattr(ss, "load_screen_snapshot", _boom)
    r = prod_client.get("/api/compliance/ofac-status")
    assert r.status_code == 200, r.data
    body = r.get_json() or {}
    assert body.get("status") == "unknown"
    assert body.get("fresh") is False
