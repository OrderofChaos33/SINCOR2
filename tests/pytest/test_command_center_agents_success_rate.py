"""Data-honesty tests for GET /api/command-center/agents (Command Center roster).

Builder B's adversarial review of the Command Center mount found that
``_build_agent_roster`` defaulted a missing reputation ``success_rate`` to
1.0, so every roster agent with no reputation data rendered as "100.0%"
success while having completed 0 tasks — a fabricated-looking number on a
surface that is supposed to show real payloads.

These tests pin the honest behavior:

* an agent with NO reputation data reports ``success_rate`` 0.0 (numeric —
  the command-center template interpolates ``${a.success_rate}%`` verbatim,
  so the field must stay a number), never 100.0;
* an agent whose reputation record exists but lacks the ``success_rate``
  key also reports 0.0;
* an agent with real reputation data passes its stored value through
  unchanged (0.75 stored -> 75.0 in the payload).
"""

from __future__ import annotations

import pytest
from flask import Flask

from sincor2.blueprints import command_center as cc


FAKE_AGENTS = [
    {"id": "E-nodata-01", "name": "No Data One"},
    {"id": "E-real-02", "name": "Real Data Two"},
]


class _StubMarket:
    def __init__(self, reputation):
        self.agent_reputation = reputation


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(cc, "_load_agent_yamls", lambda *a, **k: list(FAKE_AGENTS))
    app = Flask(__name__)
    app.register_blueprint(cc.command_center_bp)
    app.config["TESTING"] = True
    return app, app.test_client()


def _agents_by_id(payload):
    return {a["agent_id"]: a for a in payload["agents"]}


def test_agents_without_any_reputation_report_zero_success_rate(client):
    """No task market at all -> no reputation data -> 0.0, never 100.0."""
    _app, http = client
    resp = http.get("/api/command-center/agents")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["count"] == 2
    for entry in payload["agents"]:
        assert entry["tasks_completed"] == 0
        assert isinstance(entry["success_rate"], (int, float))
        assert entry["success_rate"] == 0.0
        assert entry["success_rate"] != 100.0


def test_agent_with_real_reputation_passes_through_unchanged(client):
    """A stored success_rate of 0.75 must surface as exactly 75.0."""
    app, http = client
    app.extensions["sincor_platform"] = {
        "task_market": _StubMarket(
            {
                "E-real-02": {
                    "tasks_completed": 4,
                    "success_rate": 0.75,
                    "average_quality": 0.8,
                    "total_merit_earned": 120,
                    "competencies": ["research"],
                }
            }
        )
    }
    resp = http.get("/api/command-center/agents")
    assert resp.status_code == 200
    agents = _agents_by_id(resp.get_json())
    assert agents["E-real-02"]["success_rate"] == 75.0
    assert agents["E-real-02"]["tasks_completed"] == 4
    # The roster-mate with no reputation entry stays honest in the same payload.
    assert agents["E-nodata-01"]["success_rate"] == 0.0


def test_reputation_record_missing_success_rate_key_reports_zero(client):
    """A rep record that exists but has no success_rate key -> 0.0, not 100.0."""
    app, http = client
    app.extensions["sincor_platform"] = {
        "task_market": _StubMarket(
            {
                "E-real-02": {
                    "tasks_completed": 3,
                    "average_quality": 0.8,
                    "total_merit_earned": 40,
                    "competencies": [],
                }
            }
        )
    }
    resp = http.get("/api/command-center/agents")
    assert resp.status_code == 200
    agents = _agents_by_id(resp.get_json())
    assert agents["E-real-02"]["tasks_completed"] == 3
    assert agents["E-real-02"]["success_rate"] == 0.0
