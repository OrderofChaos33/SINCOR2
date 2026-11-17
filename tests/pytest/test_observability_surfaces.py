"""Public observability surfaces.

- GET /skills              -> skill registry (agent-card parity)
- GET /status              -> fail-soft aggregate status
- GET /v1/a2a/pool/verify  -> pool status + ledger verification verdict

Honesty contract under test: an inconclusive ledger is reported as
verified: false with the reason visible (HTTP 200), never as a pass and
never hidden behind a 5xx; a dead source degrades /status instead of
failing it or fabricating a number.
"""

from __future__ import annotations

import pytest
from flask import Flask

from sincor2.a2a_bounty_pool import BountyPool, reset_bounty_pool
from sincor2.a2a_inbound import reset_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_integration import SINCOR_SKILLS
from sincor2.blueprints.observability import observability_bp


@pytest.fixture
def client(tmp_path):
    reset_fabric()
    app = Flask(__name__)
    register_inbound(app)
    app.register_blueprint(observability_bp)
    app.config["TESTING"] = True
    # Reset after mounting so no mount-time seeding can leak pool state
    # into the assertions below.
    reset_bounty_pool(path=str(tmp_path / "pool.json"), reserve_axm=0)
    return app.test_client()


def test_skills_matches_registry(client):
    resp = client.get("/skills")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["count"] == len(SINCOR_SKILLS)
    assert body["count"] > 0
    assert [s["id"] for s in body["skills"]] == [s.id for s in SINCOR_SKILLS]
    for entry in body["skills"]:
        assert entry["id"]
        assert entry["name"]
        assert entry["description"]


def test_pool_verify_empty_pool(client):
    resp = client.get("/v1/a2a/pool/verify")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["funded_axm"] == 0
    assert body["conservation_ok"] is True
    assert "ledger_verification" in body


def test_pool_verify_after_fund_and_allocate(client, tmp_path):
    pool = reset_bounty_pool(path=str(tmp_path / "pool-funded.json"),
                             reserve_axm=100)
    pool.fund()
    pool.allocate("task-obs-1", 25.0, reason="observability test")

    resp = client.get("/v1/a2a/pool/verify")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["funded_axm"] == 100.0
    assert body["allocated_axm"] == 25.0
    assert body["available_axm"] == 75.0
    assert body["spent_axm"] == 0.0
    assert body["allocation_count"] == 1
    assert body["ledger_verification"]["ok"] is True
    assert body["conservation_ok"] is True
    assert body["verified"] is True


def test_pool_verify_inconclusive_is_not_a_pass(client, monkeypatch):
    def fake_verify_ledger(self):
        return {
            "ok": False,
            "discrepancies": [],
            "inconclusive": "journal trimmed: replay cannot cover "
                            "full lifetime",
        }

    monkeypatch.setattr(BountyPool, "verify_ledger", fake_verify_ledger)
    resp = client.get("/v1/a2a/pool/verify")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["verified"] is False
    assert body["ledger_verification"]["ok"] is False
    assert "journal trimmed" in body["ledger_verification"]["inconclusive"]


def test_status_shape(client):
    resp = client.get("/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] in {"ok", "degraded"}
    assert body["version"]
    assert body["service"] == "SINCOR Master Platform"
    assert body["skills_count"] == len(SINCOR_SKILLS)
    assert "funded_axm" in body["pool"]
    assert "agents" in body
    assert "total_registered" in body["agents"]


def test_status_degrades_not_fails(client, monkeypatch):
    def dead_pool(*args, **kwargs):
        raise RuntimeError("pool unavailable")

    monkeypatch.setattr("sincor2.a2a_bounty_pool.bounty_pool", dead_pool)
    resp = client.get("/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "degraded"
    assert body["pool"].get("error") == "unavailable"
    # The healthy sections still answer from their real sources.
    assert body["skills_count"] == len(SINCOR_SKILLS)
    assert "total_registered" in body["agents"]
