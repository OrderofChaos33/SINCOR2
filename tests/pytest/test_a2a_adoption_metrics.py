from __future__ import annotations

import os

import pytest

from sincor2 import a2a_adoption_metrics as metrics


@pytest.fixture
def isolated_adoption_events(tmp_path, monkeypatch):
    path = tmp_path / "a2a_adoption_events.jsonl"
    monkeypatch.setattr(metrics, "_event_path", lambda: path)
    return path


def test_weekly_kpi_counts_active_external_agents(isolated_adoption_events):
    metrics.record_paid_settlement(
        task_id="task-1",
        caller_id="agent-alpha",
        skill_id="lead-enrichment",
        tx_hash="0xabc1",
        axm_paid_wei=10**18,
        platform_fee_axm=0.05,
    )
    metrics.record_paid_settlement(
        task_id="task-2",
        caller_id="agent-beta",
        skill_id="competitor-intel",
        tx_hash="0xabc2",
        axm_paid_wei=2 * 10**18,
        platform_fee_axm=0.10,
    )
    metrics.record_paid_settlement(
        task_id="task-3",
        caller_id="anonymous",
        skill_id="toa-decision",
        tx_hash="0xabc3",
        axm_paid_wei=10**18,
        platform_fee_axm=0.05,
    )

    payload = metrics.weekly_adoption_kpi(window_days=7)

    assert payload["weekly_active_external_agents"] == 2
    assert payload["paid_a2a_tasks"] == 3
    assert payload["paid_a2a_volume_axm"] == 4.0
    assert payload["platform_fee_axm"] == 0.2


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


def test_adoption_kpi_endpoint_returns_north_star_metric(mvp_client, isolated_adoption_events):
    metrics.record_paid_settlement(
        task_id="task-kpi-1",
        caller_id="builder-1",
        skill_id="lead-enrichment",
        tx_hash="0xfeed1",
        axm_paid_wei=10**18,
        platform_fee_axm=0.05,
    )
    response = mvp_client.get("/api/ops/a2a/adoption-kpi")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["a2a_adoption"]["north_star_metric"] == "weekly_active_external_agents_settling_paid_axm_tasks"
    assert body["a2a_adoption"]["weekly_active_external_agents"] >= 1
