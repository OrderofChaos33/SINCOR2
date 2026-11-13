"""Hermetic tests for sincor2.ofac_auto_refresh + railway_start wiring.

No network: sincor2.refresh_ofac_sdn.refresh is monkeypatched with
fakes in every test.
"""
from __future__ import annotations

import threading
import time

import pytest

import railway_start
from sincor2 import ofac_auto_refresh, refresh_ofac_sdn

_FAKE_RESULT = {
    "status": "refreshed",
    "publication_date": "2026-10-21",
    "previous_publication_date": "2026-10-21",
    "entry_count": 19471,
    "fetched_at": "2026-10-25T20:23:12+00:00",
    "path": "/tmp/p20_ofac_sdn.json",
}


class _Recorder:
    def __init__(self, fail_first: int = 0):
        self.calls = 0
        self._fail_first = fail_first
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            self.calls += 1
            n = self.calls
        if n <= self._fail_first:
            raise RuntimeError("simulated download failure")
        return dict(_FAKE_RESULT)


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.delenv("SINCOR_OFAC_AUTO_REFRESH", raising=False)
    ofac_auto_refresh._reset_for_tests()
    yield
    ofac_auto_refresh._reset_for_tests()


def test_refresh_once_success_returns_ok(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(refresh_ofac_sdn, "refresh", rec)
    result = ofac_auto_refresh.refresh_once()
    assert result["ok"] is True
    assert result["status"] == "refreshed"
    assert result["entry_count"] == 19471
    assert rec.calls == 1


def test_refresh_once_failure_never_raises(monkeypatch):
    def boom():
        raise ValueError("bad snapshot")

    monkeypatch.setattr(refresh_ofac_sdn, "refresh", boom)
    result = ofac_auto_refresh.refresh_once()
    assert result["ok"] is False
    assert "ValueError" in result["error"]


def test_boot_refresh_runs_once_on_start(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(refresh_ofac_sdn, "refresh", rec)
    assert ofac_auto_refresh.start_auto_refresh(interval_seconds=3600.0) is True
    assert _wait_for(lambda: rec.calls >= 1)
    time.sleep(0.15)
    assert rec.calls == 1


def test_loop_repeats_with_tiny_interval(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(refresh_ofac_sdn, "refresh", rec)
    assert ofac_auto_refresh.start_auto_refresh(interval_seconds=0.05) is True
    assert _wait_for(lambda: rec.calls >= 2)


def test_raising_fake_does_not_kill_thread(monkeypatch):
    rec = _Recorder(fail_first=2)
    monkeypatch.setattr(refresh_ofac_sdn, "refresh", rec)
    assert ofac_auto_refresh.start_auto_refresh(interval_seconds=0.05) is True
    # Two failures, then the loop must still be alive and succeeding.
    assert _wait_for(lambda: rec.calls >= 3)


def test_start_is_idempotent_single_thread(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(refresh_ofac_sdn, "refresh", rec)
    assert ofac_auto_refresh.start_auto_refresh(interval_seconds=3600.0) is True
    assert ofac_auto_refresh.start_auto_refresh(interval_seconds=3600.0) is False
    threads = [t for t in threading.enumerate()
               if t.name == "sincor2-ofac-auto-refresh"]
    assert len(threads) == 1


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "False"])
def test_kill_switch_disables_start(monkeypatch, value):
    rec = _Recorder()
    monkeypatch.setattr(refresh_ofac_sdn, "refresh", rec)
    monkeypatch.setenv("SINCOR_OFAC_AUTO_REFRESH", value)
    assert ofac_auto_refresh.start_auto_refresh(interval_seconds=0.05) is False
    time.sleep(0.15)
    assert rec.calls == 0


def test_kill_switch_unset_or_on_still_starts(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(refresh_ofac_sdn, "refresh", rec)
    monkeypatch.setenv("SINCOR_OFAC_AUTO_REFRESH", "1")
    assert ofac_auto_refresh.start_auto_refresh(interval_seconds=3600.0) is True
    assert _wait_for(lambda: rec.calls >= 1)


def test_railway_start_helper_swallows_start_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(ofac_auto_refresh, "start_auto_refresh", boom)
    # Must not raise even though start_auto_refresh raises.
    railway_start._start_ofac_auto_refresh()
