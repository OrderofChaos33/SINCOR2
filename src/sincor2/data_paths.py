"""Persistent data paths — mount Railway volume at /data.

On Windows a copied .env.example with SINCOR_DATA_DIR=/data resolves to
C:\\data. Remap Unix /data paths to <repo>/data unless RAILWAY_ENVIRONMENT is set.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _is_unix_data_path(value: str) -> bool:
    if not value:
        return False
    normalized = value.replace("\\", "/").strip()
    if normalized.startswith("./"):
        return False
    return normalized == "/data" or normalized.startswith("/data/")


def _windows_remap(value: str) -> Path:
    """Map /data and \\data to repo/data on native Windows."""
    raw = value.replace("/", "\\")
    lower = raw.lower()
    if lower.startswith("\\data"):
        rest = raw[5:].lstrip("\\")
        base = project_root() / "data"
        return (base / rest) if rest else base
    if len(raw) >= 7 and raw[1] == ":" and lower[2:].startswith("\\data"):
        rest = raw[7:].lstrip("\\")
        base = project_root() / "data"
        return (base / rest) if rest else base
    return Path(value)


def data_dir() -> Path:
    explicit = os.environ.get("SINCOR_DATA_DIR", "").strip()
    railway = bool(os.environ.get("RAILWAY_ENVIRONMENT"))
    if explicit:
        if os.name == "nt" and not railway and (
            _is_unix_data_path(explicit) or explicit.replace("/", "\\").lower().startswith("\\data")
        ):
            base = project_root() / "data"
        else:
            base = Path(explicit)
    elif railway and Path("/data").is_dir():
        base = Path("/data")
    else:
        base = project_root() / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base


def orders_db_path() -> Path:
    override = os.environ.get("ORDERS_DB_PATH", "").strip()
    if override:
        if os.name == "nt" and not os.environ.get("RAILWAY_ENVIRONMENT") and (
            _is_unix_data_path(override) or override.replace("/", "\\").lower().startswith("\\data")
        ):
            p = _windows_remap(override)
        else:
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
