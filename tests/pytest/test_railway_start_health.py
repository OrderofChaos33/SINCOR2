"""Truthful /health deploy probe (railway_start.py).

Railway's deploy healthcheck hits /health with a 120s timeout. The old
behavior returned an instant stdlib-only 200 from _LIVENESS no matter what,
so a deployment whose mvp_app never finished loading still passed the
healthcheck and went live serving nothing. /health must now go 503 when the
load has failed or exceeded the 90s boot budget, while /api/health and
/healthz keep their instant-200 behavior (they are not the deploy probe).

These tests drive the real wsgi() with controlled module state; no network,
no real mvp_app import.
"""
import json
import time

import pytest

import railway_start


@pytest.fixture()
def boot_state(monkeypatch):
    """Control railway_start's boot state without starting the load thread."""
    monkeypatch.setattr(railway_start, "start_background_load", lambda: None)

    def apply(full=None, error=None, uptime_s=0.0, attempts=1):
        monkeypatch.setattr(railway_start, "_full", full)
        monkeypatch.setattr(railway_start, "_full_error", error)
        monkeypatch.setattr(railway_start, "_started_at", time.time() - uptime_s)
        # A recorded error implies a load attempt happened; boot_status()
        # reports "loading" (not "error") while attempts == 0.
        monkeypatch.setattr(railway_start, "_attempts", attempts)

    return apply


def _call(path):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(
        railway_start.wsgi(
            {"PATH_INFO": path, "REQUEST_METHOD": "GET"}, start_response
        )
    )
    return captured["status"], body


def test_health_200_when_loaded(boot_state):
    boot_state(full=object(), error=None, uptime_s=5)
    status, body = _call("/health")
    assert status == "200 OK"
    assert body == railway_start._HEALTH


def test_health_200_when_loading_fresh_no_error(boot_state):
    # Still inside the boot budget with no recorded failure: don't fail a
    # good deploy mid-boot (mvp_app normally imports in ~10s).
    boot_state(full=None, error=None, uptime_s=5)
    status, body = _call("/health")
    assert status == "200 OK"
    assert body == railway_start._HEALTH


def test_health_503_when_load_failed(boot_state):
    boot_state(full=None, error="ImportError: boom", uptime_s=5)
    status, body = _call("/health")
    assert status == "503 Service Unavailable"
    payload = json.loads(body)
    assert payload["loaded"] is False
    assert payload["error"] == "ImportError: boom"
    # Deploy log shows why.
    assert payload["status"] == "error"


def test_health_503_when_boot_budget_exceeded(boot_state):
    # Loading with no error but stuck past the 90s budget: broken deploy,
    # fail the probe so Railway keeps the previous good deployment live.
    # (90s is _HEALTH_BOOT_BUDGET_S; spelled out so this test also runs
    # against unfixed code, where the constant does not exist.)
    boot_state(full=None, error=None, uptime_s=120.0)
    status, body = _call("/health")
    assert status == "503 Service Unavailable"
    payload = json.loads(body)
    assert payload["loaded"] is False


def test_health_recovers_to_200_once_loaded(boot_state):
    # A transient failure that recovers on retry flips back to 200.
    boot_state(full=object(), error="ImportError: stale", uptime_s=200)
    status, body = _call("/health")
    assert status == "200 OK"
    assert body == railway_start._HEALTH


def test_api_health_unchanged_in_stuck_state(boot_state):
    # /api/health is NOT the deploy probe: instant 200 even when the app
    # never loads.
    boot_state(full=None, error="ImportError: boom", uptime_s=200)
    status, body = _call("/api/health")
    assert status == "200 OK"
    assert body == railway_start._HEALTH


def test_healthz_unchanged_in_stuck_state(boot_state):
    boot_state(full=None, error="ImportError: boom", uptime_s=200)
    status, body = _call("/healthz")
    assert status == "200 OK"
    assert body == railway_start._HEALTH


def test_boot_endpoint_unchanged(boot_state):
    boot_state(full=None, error="ImportError: boom", uptime_s=10)
    status, body = _call("/boot")
    assert status == "200 OK"
    assert json.loads(body)["status"] == "error"


def test_ready_endpoint_unchanged(boot_state):
    boot_state(full=None, error=None, uptime_s=5)
    status, _ = _call("/ready")
    assert status == "503 Service Unavailable"
