"""Sample BI reports — lead capture + gated access."""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sincor2.data_paths import data_dir, project_root

REPORTS_DIR = project_root() / "content" / "sample_reports"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _db_path() -> Path:
    p = data_dir() / "sample_reports.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_sample_reports_db() -> None:
    with _conn() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS sample_leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                name TEXT DEFAULT '',
                company TEXT DEFAULT '',
                access_token TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )"""
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_sample_leads_email ON sample_leads(email)")
        db.commit()


def list_report_slugs() -> list[str]:
    if not REPORTS_DIR.is_dir():
        return []
    return sorted(p.stem for p in REPORTS_DIR.glob("*.json"))


def load_report(slug: str) -> dict[str, Any] | None:
    path = REPORTS_DIR / f"{slug}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_reports_preview() -> list[dict[str, Any]]:
    rows = []
    for slug in list_report_slugs():
        data = load_report(slug)
        if not data:
            continue
        rows.append(
            {
                "slug": slug,
                "company": data.get("company", slug),
                "ticker": data.get("ticker", ""),
                "sector": data.get("sector", ""),
                "summary": (data.get("executive_summary") or "")[:200] + "…",
                "generated_at": data.get("generated_at", ""),
            }
        )
    return rows


def request_sample_access(email: str, name: str = "", company: str = "") -> dict[str, Any]:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        return {"ok": False, "error": "invalid_email"}
    init_sample_reports_db()
    token = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as db:
        existing = db.execute("SELECT access_token FROM sample_leads WHERE email = ?", (email,)).fetchone()
        if existing:
            token = existing["access_token"]
        else:
            db.execute(
                "INSERT INTO sample_leads (email, name, company, access_token, created_at) VALUES (?,?,?,?,?)",
                (email, (name or "").strip(), (company or "").strip(), token, now),
            )
            db.commit()
    return {
        "ok": True,
        "access_token": token,
        "reports": list_report_slugs(),
        "message": "Access granted — view all sample reports below.",
    }


def validate_access_token(token: str) -> bool:
    if not token:
        return False
    init_sample_reports_db()
    with _conn() as db:
        row = db.execute("SELECT 1 FROM sample_leads WHERE access_token = ?", (token.strip(),)).fetchone()
    return row is not None


def lead_stats() -> dict[str, Any]:
    init_sample_reports_db()
    with _conn() as db:
        total = db.execute("SELECT COUNT(*) AS c FROM sample_leads").fetchone()["c"]
        recent = db.execute(
            "SELECT email, name, company, created_at FROM sample_leads ORDER BY id DESC LIMIT 20"
        ).fetchall()
    return {"total_leads": total, "recent": [dict(r) for r in recent]}