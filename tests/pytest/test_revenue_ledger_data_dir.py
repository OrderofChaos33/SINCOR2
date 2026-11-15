"""Revenue-ledger path resolution — canonical data-dir default.

Genspark launch-review finding (verified on pristine dde9f31f): the rail
subscription ledger, the pack-rail ledger, and the intake mirror all
defaulted to the CWD-relative ``data/revenue/revenue_ledger.db`` unless
``SINCOR_REVENUE_DB`` was set, while ``revenue_orchestrator`` rooted its
ledger under ``SINCOR_DATA_DIR``. On Railway the CWD is ephemeral even
when a ``/data`` volume exists, so the ledger could be lost on redeploy.

Canonical resolution (single shared resolver,
``sincor2.data_paths.revenue_ledger_db_path``):

1. ``SINCOR_REVENUE_DB`` set -> it wins, byte-identical to before.
2. Else ``SINCOR_DATA_DIR`` set -> ``<data dir>/revenue/revenue_ledger.db``
   (the exact layout ``revenue_orchestrator.DATA_DIR`` already used).
3. Else -> the historical ``data/revenue/revenue_ledger.db``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clean_revenue_env(monkeypatch):
    monkeypatch.delenv("SINCOR_REVENUE_DB", raising=False)
    monkeypatch.delenv("SINCOR_DATA_DIR", raising=False)
    yield


def _expected(data_dir: Path) -> Path:
    return data_dir / "revenue" / "revenue_ledger.db"


def test_shared_resolver_uses_data_dir(monkeypatch, tmp_path):
    from sincor2.data_paths import revenue_ledger_db_path

    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    assert revenue_ledger_db_path() == _expected(tmp_path)


def test_rail_subscriptions_db_path_uses_data_dir(monkeypatch, tmp_path):
    from sincor2 import rail_subscriptions

    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    resolved = rail_subscriptions._db_path()
    assert resolved == _expected(tmp_path)
    assert resolved.is_absolute()


def test_rail_routes_db_path_uses_data_dir(monkeypatch, tmp_path):
    from sincor2 import rail_routes

    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    resolved = rail_routes._rail_db_path()
    assert resolved == _expected(tmp_path)
    assert resolved.is_absolute()


def test_intake_db_path_uses_data_dir(monkeypatch, tmp_path):
    from sincor2.blueprints import intake

    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    resolved = intake._intake_db_path()
    assert resolved == _expected(tmp_path)
    assert resolved.is_absolute()


def test_revenue_orchestrator_digital_path_uses_data_dir(
        monkeypatch, tmp_path):
    from sincor2 import revenue_orchestrator

    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    assert revenue_orchestrator._digital_intake_db_path() == _expected(
        tmp_path)


def test_all_call_sites_agree(monkeypatch, tmp_path):
    from sincor2 import rail_routes, rail_subscriptions, revenue_orchestrator
    from sincor2.blueprints import intake
    from sincor2.data_paths import revenue_ledger_db_path

    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    resolved = {
        revenue_ledger_db_path(),
        rail_subscriptions._db_path(),
        rail_routes._rail_db_path(),
        intake._intake_db_path(),
        revenue_orchestrator._digital_intake_db_path(),
    }
    assert resolved == {_expected(tmp_path)}


def test_revenue_db_env_override_wins_everywhere(monkeypatch, tmp_path):
    from sincor2 import rail_routes, rail_subscriptions, revenue_orchestrator
    from sincor2.blueprints import intake
    from sincor2.data_paths import revenue_ledger_db_path

    override = tmp_path / "elsewhere" / "custom.db"
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(override))
    assert revenue_ledger_db_path() == override
    assert rail_subscriptions._db_path() == override
    assert rail_routes._rail_db_path() == override
    assert intake._intake_db_path() == override
    assert revenue_orchestrator._digital_intake_db_path() == override


def test_no_env_preserves_historical_relative_default(monkeypatch):
    from sincor2 import rail_routes, rail_subscriptions
    from sincor2.blueprints import intake
    from sincor2.data_paths import revenue_ledger_db_path

    historical = Path("data") / "revenue" / "revenue_ledger.db"
    assert revenue_ledger_db_path() == historical
    assert rail_subscriptions._db_path() == historical
    assert rail_routes._rail_db_path() == historical
    # intake's fallback historically came from revenue_orchestrator's
    # import-time REVENUE_DB; with no env at import it is the same file.
    assert intake._intake_db_path() == historical
    assert "SINCOR_DATA_DIR" not in os.environ
