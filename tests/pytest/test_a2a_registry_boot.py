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


# ---------------------------------------------------------------------------
# W-95: partial-wipe integrity check (expected_agent_count vs restored count)
# ---------------------------------------------------------------------------


def _write_agents_file_with_count(payload: dict):
    path = _agents_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _agent(i):
    return {"agent_id": "agent-%d" % i, "last_heartbeat": 0}


def test_w95_total_wipe_still_alarms_empty(boot_env, monkeypatch, caplog):
    """(a) Pre-W-95 behavior preserved: existing file, 0 restored -> EMPTY alarm."""
    _write_agents_file_with_count({"agents": [], "expected_agent_count": 6})
    _production_env(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        fabric = get_fabric()
    assert len(fabric.agents) == 0
    assert fabric.registry_boot["restore_integrity"]["alarm"] == "empty"
    assert "REGISTRY RESTORE EMPTY" in caplog.text


def test_w95_partial_wipe_six_to_one_alarms_collapse(boot_env, monkeypatch, caplog):
    """(b) The W-95 hole: 6 agents -> 1 (attacker-controlled) must be loud."""
    _write_agents_file_with_count(
        {"agents": [{"agent_id": "attacker"}], "expected_agent_count": 6}
    )
    _production_env(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        fabric = get_fabric()
    assert set(fabric.agents) == {"attacker"}
    boot = fabric.registry_boot["restore_integrity"]
    assert boot["alarm"] == "collapse"
    assert boot["expected"] == 6
    assert boot["missing"] == 5
    assert "REGISTRY RESTORE COLLAPSE" in caplog.text
    # Loud beyond the log line: health snapshot flips critical.
    snap = health_snapshot()
    assert snap["critical"] is True
    assert snap["registry_expected_agents"] == 6
    assert snap["registry_restore_missing"] == 5
    assert snap["registry_restore_alarm"] == "collapse"


def test_w95_partial_wipe_moderate_drop_alarms_short(boot_env, monkeypatch, caplog):
    """A drop beyond tolerance but under half still alarms (error, not critical)."""
    _write_agents_file_with_count(
        {"agents": [_agent(i) for i in range(60)], "expected_agent_count": 100}
    )
    _production_env(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        fabric = get_fabric()
    boot = fabric.registry_boot["restore_integrity"]
    assert boot["alarm"] == "short"
    assert "REGISTRY RESTORE SHORT" in caplog.text
    assert "REGISTRY RESTORE COLLAPSE" not in caplog.text
    assert health_snapshot()["critical"] is False


def test_w95_normal_restore_no_alarm(boot_env, monkeypatch, caplog):
    """(c) 6 -> 6 restores cleanly with no alarm."""
    _write_agents_file_with_count(
        {"agents": [_agent(i) for i in range(6)], "expected_agent_count": 6}
    )
    _production_env(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        fabric = get_fabric()
    assert len(fabric.agents) == 6
    assert fabric.registry_boot["restore_integrity"]["alarm"] is None
    assert "REGISTRY RESTORE" not in caplog.text


def test_w95_first_boot_no_file_no_alarm(boot_env, monkeypatch, caplog):
    """(d) First boot with no prior registry file must not alarm."""
    _production_env(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        fabric = get_fabric()
    assert fabric.registry_boot["restore_integrity"]["alarm"] is None
    assert "REGISTRY RESTORE" not in caplog.text


def test_w95_legacy_file_without_expected_count_no_alarm(boot_env, monkeypatch, caplog):
    """Files predating expected_agent_count can't be compared — stay quiet."""
    _write_agents_file(json.dumps({"agents": [_agent(i) for i in range(3)]}))
    _production_env(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        fabric = get_fabric()
    assert len(fabric.agents) == 3
    assert fabric.registry_boot["restore_integrity"]["alarm"] is None
    assert "REGISTRY RESTORE" not in caplog.text


def test_w95_small_churn_within_tolerance_no_alarm(boot_env, monkeypatch, caplog):
    """A 100 -> 95 drop is within the 10% tolerance: no alarm."""
    _write_agents_file_with_count(
        {"agents": [_agent(i) for i in range(95)], "expected_agent_count": 100}
    )
    _production_env(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        fabric = get_fabric()
    assert fabric.registry_boot["restore_integrity"]["alarm"] is None
    assert "REGISTRY RESTORE" not in caplog.text


def test_w95_save_persists_expected_agent_count(boot_env):
    """_save_agents records the count that boot will compare against."""
    from sincor2.a2a_inbound import _save_agents

    fabric = reset_fabric()
    for i in range(6):
        fabric.agents["agent-%d" % i] = _agent(i)
    fabric.agents["no-id"] = {"no_id": True}  # not restorable; must not count
    _save_agents(fabric)
    raw = json.loads(_agents_path().read_text(encoding="utf-8"))
    assert raw["expected_agent_count"] == 6
    assert len(raw["agents"]) == 7


def test_w95_round_trip_save_then_boot_no_alarm(boot_env, monkeypatch, caplog):
    """Save -> fresh boot with the same records: no alarm (no false positive)."""
    from sincor2.a2a_inbound import _save_agents

    fabric = reset_fabric()
    for i in range(6):
        fabric.agents["agent-%d" % i] = _agent(i)
    _save_agents(fabric)
    inbound._FABRIC = None  # fresh boot
    _production_env(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        fabric = get_fabric()
    assert len(fabric.agents) == 6
    assert fabric.registry_boot["restore_integrity"]["alarm"] is None
    assert "REGISTRY RESTORE" not in caplog.text


@pytest.mark.parametrize(
    "restored,expected,file_existed,alarm",
    [
        (0, 6, True, "empty"),       # total wipe
        (0, None, True, "empty"),    # corrupt/unparseable legacy path
        (0, 0, False, None),         # no file at all (first boot)
        (5, 6, True, None),          # within tolerance (max(1, 6//10)=1)
        (4, 6, True, "short"),       # 2 missing > tol 1, not >50%
        (3, 6, True, "short"),       # exactly half missing -> short, not collapse
        (2, 6, True, "collapse"),    # >50% missing
        (1, 6, True, "collapse"),    # the W-95 attack shape
        (6, 6, True, None),          # clean restore
        (7, 6, True, None),          # growth is fine
        (3, None, True, None),       # legacy file: can't compare
    ],
)
def test_w95_integrity_check_matrix(restored, expected, file_existed, alarm):
    from sincor2.a2a_inbound import _registry_restore_integrity_check

    result = _registry_restore_integrity_check(restored, expected, file_existed)
    assert result["alarm"] == alarm
    if alarm in ("short", "collapse"):
        assert result["missing"] == expected - restored
        assert result["missing"] > 0
