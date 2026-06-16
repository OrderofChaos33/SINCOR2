"""
Launch partner / KOL outreach CRM for SINCOR July 7 launch.

Targets live in config/launch_partners.yaml.
State persists in data/launch_partners.db.

CLI: scripts/run_partner_outreach.py
UI:  /launch/partners (admin)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from sincor2.data_paths import data_dir, project_root

logger = logging.getLogger("sincor2.partner_outreach")

STATUSES = (
    "queued",
    "contacted",
    "followup_1",
    "followup_2",
    "interested",
    "partnered",
    "declined",
    "no_response",
)

DM_TEMPLATE = """Hi {name} — I'm building SINCOR: 42 autonomous AI agents running a real business on Base (not a chatbot wrapper).

Launch: {launch_date} · Live demo: {pitch_deck}

Proof in 30 seconds:
• SINC {sinc} — CertiK {certik}, Sourcify verified
• Official curve: {curve}

{angle}

{ask}

Happy to give you an exclusive first look / demo — no ask beyond an honest look. Story's the story.

— Court @ SINCOR
{site}"""

FOLLOWUP_TEMPLATE = """Quick follow-up on SINCOR ({launch_date} launch on Base).

We embedded the full autonomous swarm deck + live platform at {site} — 42 agents, wallet-native SINC billing, verifiable contracts.

Still happy to set up a 15-min demo or send a curator pack (thread draft + proof links) if useful for your audience.

{contact_url}"""

EMAIL_SUBJECT = "SINCOR July 7 launch — Base AI agent swarm (verified, not vapor)"


def _config_path() -> Path:
    return project_root() / "config" / "launch_partners.yaml"


def _db_path() -> Path:
    p = data_dir() / "launch_partners.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_partner_db() -> None:
    with _conn() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS partner_status (
                partner_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'queued',
                last_contact_at TEXT,
                next_followup_at TEXT,
                notes TEXT DEFAULT '',
                outreach_count INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS outreach_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                partner_id TEXT NOT NULL,
                channel TEXT,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        db.commit()


def load_config() -> dict[str, Any]:
    with open(_config_path(), encoding="utf-8") as f:
        return yaml.safe_load(f)


def launch_date() -> date:
    cfg = load_config()
    return date.fromisoformat(cfg["launch_date"])


def days_to_launch() -> int:
    return (launch_date() - date.today()).days


def _tier_cadence(tier: str, days: int) -> str | None:
    """Return outreach phase label if partner is due for contact."""
    tier = (tier or "P2").upper()
    if tier == "P0":
        if days <= 1:
            return "launch_eve"
        if days <= 7:
            return "followup_2"
        if days <= 14:
            return "followup_1"
        if days <= 22:
            return "intro"
        return "intro"
    if tier == "P1":
        if days <= 7:
            return "followup_1"
        if days <= 14:
            return "intro"
        return None
    if tier == "P2" and days <= 7:
        return "intro"
    return None


def _get_status(partner_id: str) -> dict[str, Any]:
    with _conn() as db:
        row = db.execute(
            "SELECT * FROM partner_status WHERE partner_id=?", (partner_id,)
        ).fetchone()
    if row:
        return dict(row)
    return {
        "partner_id": partner_id,
        "status": "queued",
        "last_contact_at": None,
        "next_followup_at": None,
        "notes": "",
        "outreach_count": 0,
    }


def sync_targets() -> int:
    """Ensure every yaml partner has a DB row."""
    init_partner_db()
    cfg = load_config()
    now = datetime.now(timezone.utc).isoformat()
    added = 0
    with _conn() as db:
        for p in cfg.get("partners", []):
            pid = p["id"]
            exists = db.execute(
                "SELECT 1 FROM partner_status WHERE partner_id=?", (pid,)
            ).fetchone()
            if not exists:
                db.execute(
                    """INSERT INTO partner_status (partner_id, status, updated_at)
                       VALUES (?, 'queued', ?)""",
                    (pid, now),
                )
                added += 1
        db.commit()
    return added


def render_message(partner: dict[str, Any], phase: str = "intro") -> str:
    cfg = load_config()
    proof = cfg.get("proof", {})
    ctx = {
        "name": partner.get("name", "there"),
        "launch_date": cfg.get("launch_date", "2026-07-07"),
        "pitch_deck": cfg.get("pitch_deck", "https://getsincor.com/pitch"),
        "site": cfg.get("site", "https://getsincor.com"),
        "sinc": proof.get("sinc", ""),
        "curve": proof.get("curve", ""),
        "certik": proof.get("certik", "97/100"),
        "angle": partner.get("angle", ""),
        "ask": partner.get("ask", ""),
        "contact_url": partner.get("contact_url", cfg.get("site", "")),
    }
    if phase in ("followup_1", "followup_2", "launch_eve"):
        return FOLLOWUP_TEMPLATE.format(**ctx)
    return DM_TEMPLATE.format(**ctx)


def list_partners(status: str | None = None) -> list[dict[str, Any]]:
    sync_targets()
    cfg = load_config()
    out: list[dict[str, Any]] = []
    for p in cfg.get("partners", []):
        st = _get_status(p["id"])
        if status and st["status"] != status:
            continue
        out.append({**p, **st})
    return out


def pipeline_summary() -> dict[str, Any]:
    partners = list_partners()
    by_status: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    for p in partners:
        by_status[p["status"]] = by_status.get(p["status"], 0) + 1
        tier = p.get("tier", "P2")
        by_tier[tier] = by_tier.get(tier, 0) + 1
    return {
        "launch_date": str(launch_date()),
        "days_to_launch": days_to_launch(),
        "total": len(partners),
        "by_status": by_status,
        "by_tier": by_tier,
    }


def due_outreach(limit: int = 12) -> list[dict[str, Any]]:
    """Partners due for outreach today based on tier cadence + status."""
    sync_targets()
    days = days_to_launch()
    cfg = load_config()
    due: list[dict[str, Any]] = []

    for p in cfg.get("partners", []):
        st = _get_status(p["id"])
        if st["status"] in ("partnered", "declined"):
            continue
        phase = _tier_cadence(p.get("tier", "P2"), days)
        if not phase:
            continue
        status = st["status"]
        if phase == "intro" and status != "queued":
            continue
        if phase == "followup_1" and status not in ("contacted", "no_response"):
            continue
        if phase == "followup_2" and status not in (
            "contacted", "followup_1", "no_response", "interested"
        ):
            continue
        if phase == "launch_eve" and status not in (
            "contacted", "followup_1", "followup_2", "interested", "partnered"
        ):
            if status == "queued":
                pass
            elif status not in ("interested", "partnered"):
                continue
        msg = render_message(p, phase)
        due.append({
            **p,
            **st,
            "phase": phase,
            "message": msg,
            "subject": EMAIL_SUBJECT,
            "days_to_launch": days,
        })

    tier_order = {"P0": 0, "P1": 1, "P2": 2}
    due.sort(key=lambda x: (tier_order.get(x.get("tier", "P2"), 9), x.get("name", "")))
    return due[:limit]


def mark_contacted(
    partner_id: str,
    *,
    status: str = "contacted",
    notes: str = "",
    channel: str = "",
    message: str = "",
) -> bool:
    init_partner_db()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as db:
        row = db.execute(
            "SELECT outreach_count FROM partner_status WHERE partner_id=?",
            (partner_id,),
        ).fetchone()
        if not row:
            return False
        count = int(row["outreach_count"] or 0) + 1
        merged_notes = notes
        if notes:
            existing = db.execute(
                "SELECT notes FROM partner_status WHERE partner_id=?", (partner_id,)
            ).fetchone()
            prev = (existing["notes"] or "") if existing else ""
            merged_notes = f"{prev}\n[{now[:10]}] {notes}".strip() if prev else notes
        db.execute(
            """UPDATE partner_status SET
               status=?, last_contact_at=?, notes=?, outreach_count=?, updated_at=?
               WHERE partner_id=?""",
            (status, now, merged_notes, count, now, partner_id),
        )
        if message:
            db.execute(
                """INSERT INTO outreach_log (partner_id, channel, message, created_at)
                   VALUES (?, ?, ?, ?)""",
                (partner_id, channel, message[:8000], now),
            )
        db.commit()
    return True


def update_status(partner_id: str, status: str, notes: str = "") -> bool:
    if status not in STATUSES:
        return False
    return mark_contacted(partner_id, status=status, notes=notes)


def export_batch_markdown(limit: int = 10) -> str:
    """Human-readable outreach batch for copy-paste."""
    due = due_outreach(limit=limit)
    days = days_to_launch()
    lines = [
        f"# SINCOR Partner Outreach — {date.today().isoformat()}",
        f"Launch: {launch_date()} ({days} days)",
        f"Due today: {len(due)}",
        "",
    ]
    for i, p in enumerate(due, 1):
        method = p.get("contact_method", "dm")
        url = p.get("contact_url", "")
        lines.extend([
            f"## {i}. {p['name']} [{p.get('tier')}] — {p['phase']}",
            f"- Role: {p.get('role')} · Channel: {p.get('channel')} · Via: {method}",
            f"- Handle: @{p.get('handle', 'n/a')} · URL: {url}",
            f"- Status: {p.get('status')} → mark `contacted` after send",
            "",
            "```",
            p["message"],
            "```",
            "",
        ])
    lines.append("---")
    lines.append("After sending: `python scripts/run_partner_outreach.py mark <id> contacted`")
    return "\n".join(lines)


def write_batch_file(limit: int = 10) -> Path:
    out_dir = data_dir() / "launch_outreach"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"batch_{date.today().isoformat()}.md"
    path.write_text(export_batch_markdown(limit=limit), encoding="utf-8")
    return path