"""Registry boot-integrity hedge: an Oct-11-style registry wipe must be loud.

Covers ``_registry_persistence_check`` + the ``get_fabric()`` boot wiring in
``sincor2.a2a_inbound``:
- on Railway without a mounted volume  -> persistent=False + error logged
- on Railway with a mounted volume     -> persistent=True, no error
- off Railway                          -> persistent=True, no error
- corrupt agents JSON                  -> 0 restored, no exception, error logged
- health_snapshot exposes the new keys (and safe defaults when the
  boot check never ran, e.g. reset_fabric fabrics in tests)
"""

from __future__ import annotations

import json
import logging
import os

import pytest

import sincor2.a2a_inbound as inbound
from sincor2.a2a_inbound import (
    _registry_persistence_check,
    get_fabric,
    health_snapshot,
    reset_fabric,
)


@pytest.fixture
def boot_env(monkeypatch, tmp_path):
    """Isolated data dir + pristine fabric global for boot-path tests."""
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    inbound._FABRIC = None
    yield tmp_path
    inbound._FABRIC = None


def _agents_path():
    from sincor2.data_paths import data_dir

    return data_dir() / "a2a_inbound_agents.json"


def _write_agents_file(payload: str):
    path = _agents_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return path


def _production_env(monkeypatch):
    """Simulate the production boot path (error logging is gated on test env)."""
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("ENVIRONMENT", "production")


def test_railway_without_mount_not_persistent_and_logs_error(boot_env, monkeypatch, caplog):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "1")
    monkeypatch.setattr(os.path, "ismount", lambda p: False)
    _production_env(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        fabric = get_fabric()
    boot = fabric.registry_boot
    assert boot["persistent"] is False
    assert boot["restored_agents"] == 0
    assert "data_dir" in boot and boot["data_dir"]
    assert boot["reason"]
    assert "REGISTRY NOT PERSISTENT" in caplog.text
    assert "mount the /data volume" in caplog.text


def test_railway_with_mounted_volume_persistent_no_error(boot_env, monkeypatch, caplog):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "1")
    monkeypatch.setattr(os.path, "ismount", lambda p: True)
    _production_env(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        fabric = get_fabric()
    assert fabric.registry_boot["persistent"] is True
    assert "REGISTRY NOT PERSISTENT" not in caplog.text


def test_no_railway_env_persistent_true_no_error(boot_env, caplog):
    # FLASK_ENV=test here (conftest autouse) — error logging suppressed anyway,
    # but off-Railway must report persistent=True regardless.
    with caplog.at_level(logging.DEBUG):
        fabric = get_fabric()
    boot = fabric.registry_boot
    assert boot["persistent"] is True
    assert boot["restored_agents"] == 0
    assert "REGISTRY NOT PERSISTENT" not in caplog.text
    assert "REGISTRY RESTORE EMPTY" not in caplog.text


def test_corrupt_agents_json_zero_restored_no_exception(boot_env, monkeypatch, caplog):
    _write_agents_file("{not valid json")
    _production_env(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        fabric = get_fabric()  # must not raise
    assert fabric.registry_boot["restored_agents"] == 0
    assert len(fabric.agents) == 0
    assert "REGISTRY RESTORE EMPTY" in caplog.text


def test_valid_agents_file_restores_and_counts(boot_env, monkeypatch, caplog):
    _write_agents_file(
        json.dumps(
            {
                "agents": [
                    {"agent_id": "a1"},
                    {"agent_id": "a2"},
                    {"no_id": True},
                    "junk",
                ]
            }
        )
    )
    _production_env(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        fabric = get_fabric()
    assert fabric.registry_boot["restored_agents"] == 2
    assert set(fabric.agents) == {"a1", "a2"}
    assert "REGISTRY RESTORE EMPTY" not in caplog.text


def test_health_snapshot_includes_registry_keys(boot_env):
    fabric = get_fabric()
    snap = health_snapshot()
    assert snap["registry_persistent"] is True
    assert snap["registry_restored_agents"] == 0
    assert snap["registered_agents"] == 0
    assert fabric.registry_boot["persistent"] is True


def test_health_snapshot_defaults_when_boot_check_never_ran(boot_env):
    fabric = reset_fabric()  # reset path never runs the boot check
    assert not hasattr(fabric, "registry_boot")
    snap = health_snapshot()  # get_fabric returns the reset fabric as-is
    assert snap["registry_persistent"] is None
    assert snap["registry_restored_agents"] == 0


def test_persistence_check_never_raises(monkeypatch):
    # Even if data_dir itself blows up, the check degrades to persistent=False.
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "1")
    monkeypatch.setattr(os.path, "ismount", lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    result = _registry_persistence_check(3)
    assert result["persistent"] is False
    assert result["restored_agents"] == 3
    assert "boom" in result["reason"]
