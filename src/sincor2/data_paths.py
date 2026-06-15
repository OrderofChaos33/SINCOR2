"""Persistent data paths — mount Railway volume at /data."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def data_dir() -> Path:
    explicit = os.environ.get("SINCOR_DATA_DIR", "").strip()
    if explicit:
        base = Path(explicit)
    elif os.environ.get("RAILWAY_ENVIRONMENT") and Path("/data").is_dir():
        base = Path("/data")
    else:
        base = project_root() / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base


def orders_db_path() -> Path:
    override = os.environ.get("ORDERS_DB_PATH", "").strip()
    if override:
        p = Path(override)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    return data_dir() / "orders.db"


def agent_burn_log_path() -> Path:
    p = data_dir() / "agent_burn_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def compliance_log_dir() -> Path:
    p = data_dir() / "logs" / "compliance"
    p.mkdir(parents=True, exist_ok=True)
    return p


def quarantine_dir() -> Path:
    p = data_dir() / "quarantine"
    p.mkdir(parents=True, exist_ok=True)
    return p


def migrate_legacy_orders_db() -> Path:
    """Copy legacy project-root orders.db into the persistent volume on first boot."""
    target = orders_db_path()
    if target.exists():
        return target
    for legacy in (project_root() / "orders.db", project_root() / "data" / "orders.db"):
        if legacy.is_file() and legacy.resolve() != target.resolve():
            shutil.copy2(legacy, target)
            return target
    target.parent.mkdir(parents=True, exist_ok=True)
    return target