"""
Launchpad outreach CRM — 5 LBP venues, one-tap email like partner pipeline.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from sincor2.data_paths import data_dir, project_root

logger = logging.getLogger("sincor2.launchpad_outreach")

STATUSES = ("queued", "contacted", "followup", "interested", "partnered", "declined", "no_response")

EMAIL_TEMPLATE = """Subject: {subject}

Hi {name} team,

We're running a KPI-driven TGE for SINC on Base (July 7) — MegaETH-style milestone rollouts with a pre-deposit waitlist opening July 1.

Proof (30 seconds):
• SINC {sinc} — CertiK {certik}, Sourcify {sourcify}
• Live gateway + $1.50 USDC hook floor (not empty pool)
• 42-agent autonomous platform · A2A marketplace · verified on Base

Campaign: {campaign_url}
Pre-deposit opens: {predeposit_opens}
Pitch: {pitch}

{angle}

{ask}

Happy to share predeposit pipeline metrics + social whitelist data as we hit KPI milestones.

— Court @ SINCOR
{site}"""

EMAIL_SUBJECT = "SINC Base TGE July 7 — KPI pre-deposit campaign + LBP slot request"


def _config_path() -> Path:
    return project_root() / "config" / "launchpads.yaml"


def _db_path() -> Path:
    p = data_dir() / "launchpads.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_launchpad_db() -> None:
    with _conn() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS launchpad_status (
                launchpad_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'queued',
                last_contact_at TEXT,
                notes TEXT DEFAULT '',
                outreach_count INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL
            )"""
        )
        db.commit()


def load_config() -> dict[str, Any]:
    return yaml.safe_load(_config_path().read_text(encoding="utf-8"))


def sync_targets() -> None:
    init_launchpad_db()
    cfg = load_config()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as db:
        for lp in cfg.get("launchpads", []):
            lid = lp["id"]
            if not db.execute(
                "SELECT 1 FROM launchpad_status WHERE launchpad_id=?", (lid,)
            ).fetchone():
                db.execute(
                    """INSERT INTO launchpad_status (launchpad_id, status, updated_at)
                       VALUES (?, 'queued', ?)""",
                    (lid, now),
                )
        db.commit()


def _get_status(launchpad_id: str) -> dict[str, Any]:
    with _conn() as db:
        row = db.execute(
            "SELECT * FROM launchpad_status WHERE launchpad_id=?", (launchpad_id,)
        ).fetchone()
    if not row:
        return {"status": "queued", "outreach_count": 0, "notes": ""}
    return dict(row)


def render_email(launchpad: dict[str, Any]) -> str:
    cfg = load_config()
    proof = cfg.get("proof", {})
    ctx = {
        "subject": EMAIL_SUBJECT,
        "name": launchpad.get("name", ""),
        "sinc": proof.get("sinc", ""),
        "certik": proof.get("certik", ""),
        "sourcify": proof.get("sourcify", ""),
        "campaign_url": cfg.get("tge_campaign", ""),
        "predeposit_opens": proof.get("predeposit_opens", "2026-07-01"),
        "pitch": cfg.get("pitch", ""),
        "angle": launchpad.get("angle", ""),
        "ask": launchpad.get("ask", ""),
        "site": cfg.get("site", ""),
    }
    return EMAIL_TEMPLATE.format(**ctx)


def due_outreach(limit: int = 5) -> list[dict[str, Any]]:
    sync_targets()
    cfg = load_config()
    due: list[dict[str, Any]] = []
    for lp in cfg.get("launchpads", []):
        st = _get_status(lp["id"])
        if st.get("status") in ("partnered", "declined"):
            continue
        email_body = render_email(lp)
        mailto = ""
        if lp.get("contact_email"):
            from urllib.parse import quote

            mailto = (
                f"mailto:{lp['contact_email']}?subject={quote(EMAIL_SUBJECT)}"
                f"&body={quote(email_body.split(chr(10), 1)[-1][:1800])}"
            )
        due.append({
            **lp,
            **st,
            "email_body": email_body,
            "subject": EMAIL_SUBJECT,
            "mailto": mailto,
        })
    tier_order = {"P0": 0, "P1": 1, "P2": 2}
    due.sort(key=lambda x: tier_order.get(x.get("tier", "P1"), 9))
    return due[:limit]


def list_launchpads() -> list[dict[str, Any]]:
    sync_targets()
    cfg = load_config()
    rows: list[dict[str, Any]] = []
    for lp in cfg.get("launchpads", []):
        rows.append({**lp, **_get_status(lp["id"])})
    tier_order = {"P0": 0, "P1": 1, "P2": 2}
    rows.sort(key=lambda x: tier_order.get(x.get("tier", "P1"), 9))
    return rows


def pipeline_summary() -> dict[str, Any]:
    sync_targets()
    cfg = load_config()
    by_status: dict[str, int] = {}
    for lp in cfg.get("launchpads", []):
        st = _get_status(lp["id"])["status"]
        by_status[st] = by_status.get(st, 0) + 1
    return {
        "launch_date": cfg.get("launch_date"),
        "total": len(cfg.get("launchpads", [])),
        "by_status": by_status,
    }


def mark_contacted(
    launchpad_id: str,
    *,
    status: str = "contacted",
    notes: str = "",
) -> bool:
    if status not in STATUSES:
        return False
    init_launchpad_db()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as db:
        row = db.execute(
            "SELECT outreach_count FROM launchpad_status WHERE launchpad_id=?",
            (launchpad_id,),
        ).fetchone()
        if not row:
            return False
        count = int(row["outreach_count"] or 0) + 1
        db.execute(
            """UPDATE launchpad_status SET status=?, last_contact_at=?, notes=?,
               outreach_count=?, updated_at=? WHERE launchpad_id=?""",
            (status, now, notes[:2000], count, now, launchpad_id),
        )
        db.commit()
    return True


def write_batch_file(limit: int = 5) -> Path:
    due = due_outreach(limit=limit)
    out_dir = data_dir() / "launch_outreach"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = out_dir / f"launchpads_{stamp}.md"
    lines = [
        f"# Launchpad outreach batch — {stamp}",
        "",
        "One-tap: open `mailto` links or copy email body. Mark contacted in /launch/launchpads",
        "",
    ]
    for lp in due:
        lines.extend([
            f"## {lp.get('name')} ({lp.get('id')}) — {lp.get('tier')}",
            f"- Method: {lp.get('contact_method')} · {lp.get('contact_url')}",
        ])
        if lp.get("mailto"):
            lines.append(f"- [Send email]({lp['mailto']})")
        lines.extend(["", "```", lp.get("email_body", ""), "```", ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path