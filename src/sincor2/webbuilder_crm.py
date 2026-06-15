"""WebBuilder CRM — form capture + owner notification on cutover."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("sincor2.webbuilder.crm")


def _crm_db_path(data_dir: Path) -> Path:
    return data_dir / "crm.db"


def init_crm(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(_crm_db_path(data_dir))
    db.execute(
        """CREATE TABLE IF NOT EXISTS webbuilder_contacts (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            project_name TEXT,
            owner_email TEXT,
            name TEXT,
            email TEXT,
            phone TEXT,
            message TEXT,
            source TEXT DEFAULT 'preview_form',
            created_at TEXT NOT NULL,
            synced_at TEXT,
            notified_at TEXT
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS webbuilder_crm_events (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT,
            created_at TEXT NOT NULL
        )"""
    )
    db.commit()
    db.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_contact(
    data_dir: Path,
    *,
    project_id: str,
    project_name: str = "",
    owner_email: str = "",
    name: str,
    email: str,
    phone: str = "",
    message: str = "",
    source: str = "preview_form",
) -> dict:
    init_crm(data_dir)
    cid = str(uuid.uuid4())
    db = sqlite3.connect(_crm_db_path(data_dir))
    db.execute(
        """INSERT INTO webbuilder_contacts
           (id, project_id, project_name, owner_email, name, email, phone, message, source, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (cid, project_id, project_name, owner_email, name, email, phone, message, source, _now()),
    )
    db.commit()
    db.close()
    return {"ok": True, "contact_id": cid}


def list_contacts(data_dir: Path, project_id: str) -> list[dict]:
    init_crm(data_dir)
    db = sqlite3.connect(_crm_db_path(data_dir))
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT * FROM webbuilder_contacts WHERE project_id = ? ORDER BY created_at DESC",
        (project_id,),
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def sync_on_cutover(data_dir: Path, project: dict) -> dict:
    """GHL-style CRM hook: on go-live, notify owner and mark contacts synced."""
    init_crm(data_dir)
    project_id = project["id"]
    owner = project.get("owner_email", "")
    db = sqlite3.connect(_crm_db_path(data_dir))
    db.row_factory = sqlite3.Row
    pending = db.execute(
        "SELECT * FROM webbuilder_contacts WHERE project_id = ? AND synced_at IS NULL",
        (project_id,),
    ).fetchall()
    now = _now()
    for row in pending:
        db.execute(
            "UPDATE webbuilder_contacts SET synced_at = ? WHERE id = ?",
            (now, row["id"]),
        )
    event_id = str(uuid.uuid4())
    payload = {
        "project_name": project.get("name"),
        "primary_url": project.get("primary_url"),
        "contacts_synced": len(pending),
        "owner_email": owner,
    }
    db.execute(
        """INSERT INTO webbuilder_crm_events (id, project_id, event_type, payload, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (event_id, project_id, "cutover_sync", json.dumps(payload), now),
    )
    db.commit()
    db.close()

    notified = False
    if owner and pending:
        notified = _notify_owner(owner, project, len(pending))

    return {
        "ok": True,
        "contacts_synced": len(pending),
        "owner_notified": notified,
        "event_id": event_id,
    }


def _notify_owner(owner_email: str, project: dict, contact_count: int) -> bool:
    try:
        from sincor2.email_sender import get_email_sender

        sender = get_email_sender()
        if sender.mode == "none":
            return False
        name = project.get("name", "your site")
        primary = project.get("primary_url") or project.get("preview_url", "")
        html_body = f"""
        <p>Your WebBuilder site <strong>{name}</strong> is now live.</p>
        <p><strong>{contact_count}</strong> lead(s) from preview forms are synced to CRM.</p>
        <p>Primary URL: <a href="{primary}">{primary}</a></p>
        <p>Studio: <a href="https://getsincor.com/verticals/webbuilder/studio?project={project['id']}">Open project</a></p>
        """
        result = sender.send_email(
            to_email=owner_email,
            to_name=project.get("owner_email", "Site owner").split("@")[0],
            subject=f"[SINCOR] {name} is live — {contact_count} lead(s) synced",
            html_content=html_body,
        )
        return result.get("status") in ("sent", "stub")
    except Exception as e:
        logger.warning("[CRM] owner notify failed: %s", e)
        return False