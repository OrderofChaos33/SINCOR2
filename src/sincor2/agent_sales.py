"""
Agent sales pipeline — outbound prospecting for LBP pre-orders.
Uses scout → synthesizer → negotiator agent workflow (CRM + draft outreach).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from sincor2.data_paths import data_dir, project_root

logger = logging.getLogger("sincor2.agent_sales")

OUTREACH_TEMPLATE = """Subject: {subject}

Hi {name},

{hook}

{offer}

Proof you can verify in 30 seconds:
• Sample BI reports (Apple, Tesla, Coinbase): {sample_reports}
• Live SINC gateway on Base · CertiK 97/100
• 42-agent platform shipping today — not a roadmap token

{cta}

— SINCOR outbound (agent-drafted, human-approved)
{site}"""

DM_TEMPLATE = """{name} — built an AI team that delivers BI reports in hours (see {sample_reports}).

Running a founding LBP Jul 7 on Base. {offer_short}

Want the 50% first-report discount? Buy $100 SINC during LBP → reply PREORDER"""


def _config_path() -> Path:
    return project_root() / "config" / "agent_prospects.yaml"


def _db_path() -> Path:
    p = data_dir() / "agent_sales.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def load_config() -> dict[str, Any]:
    return yaml.safe_load(_config_path().read_text(encoding="utf-8"))


def init_agent_sales_db() -> None:
    with _conn() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS prospects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                segment_id TEXT NOT NULL,
                name TEXT NOT NULL,
                contact TEXT DEFAULT '',
                channel TEXT DEFAULT 'email',
                company TEXT DEFAULT '',
                score INTEGER DEFAULT 50,
                status TEXT NOT NULL DEFAULT 'queued',
                draft_subject TEXT DEFAULT '',
                draft_body TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS preorders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prospect_id INTEGER,
                email TEXT NOT NULL,
                wallet TEXT DEFAULT '',
                amount_usd REAL DEFAULT 0,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL
            )"""
        )
        db.commit()


def seed_prospect_queue() -> int:
    """Seed placeholder prospects per segment until 200 rows exist."""
    init_agent_sales_db()
    cfg = load_config()
    now = datetime.now(timezone.utc).isoformat()
    added = 0
    with _conn() as db:
        total = db.execute("SELECT COUNT(*) AS c FROM prospects").fetchone()["c"]
        if total >= cfg.get("prospect_target", 200):
            return 0
        for seg in cfg.get("segments", []):
            seg_id = seg["id"]
            quota = int(seg.get("quota", 0))
            existing = db.execute(
                "SELECT COUNT(*) AS c FROM prospects WHERE segment_id = ?", (seg_id,)
            ).fetchone()["c"]
            for i in range(existing, quota):
                if total + added >= cfg.get("prospect_target", 200):
                    break
                name = f"{seg['label']} prospect #{i + 1}"
                db.execute(
                    """INSERT INTO prospects (segment_id, name, contact, channel, company, score, status, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (seg_id, name, "", "email", "", 40 + (i % 50), "queued", now, now),
                )
                added += 1
        db.commit()
    return added


def _segment_offer(seg_id: str) -> str:
    for seg in load_config().get("segments", []):
        if seg["id"] == seg_id:
            return seg.get("offer", "")
    return "Professional BI reports delivered by AI agent teams."


def draft_outreach(prospect_id: int) -> dict[str, Any]:
    init_agent_sales_db()
    cfg = load_config()
    with _conn() as db:
        row = db.execute("SELECT * FROM prospects WHERE id = ?", (prospect_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "not_found"}
        offer = _segment_offer(row["segment_id"])
        subject = f"SINCOR — BI reports in hours + LBP priority ({row['company'] or 'founder'})"
        body = OUTREACH_TEMPLATE.format(
            subject=subject,
            name=row["name"].split(" prospect")[0] or "there",
            hook="We run coordinated research, analysis, and writing agents that deliver professional-grade business intelligence — while you sleep.",
            offer=offer,
            sample_reports=cfg.get("sample_reports", cfg.get("site", "")),
            cta="Pre-deposit opens July 1 for LBP priority. Reply PREORDER for the 50% first-report LBP discount.",
            site=cfg.get("site", "https://getsincor.com"),
        )
        dm = DM_TEMPLATE.format(
            name=row["name"].split(" prospect")[0] or "Hey",
            sample_reports=cfg.get("sample_reports", ""),
            offer_short=offer[:120],
        )
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            """UPDATE prospects SET draft_subject=?, draft_body=?, status='drafted', notes=?, updated_at=?
               WHERE id=?""",
            (subject, body, f"dm:{dm}", now, prospect_id),
        )
        db.commit()
    return {"ok": True, "prospect_id": prospect_id, "subject": subject, "body": body, "dm": dm}


def run_prospecting_batch(limit: int = 25) -> dict[str, Any]:
    """Scout queue → synthesizer drafts for next batch."""
    seed_prospect_queue()
    init_agent_sales_db()
    drafted = 0
    with _conn() as db:
        rows = db.execute(
            "SELECT id FROM prospects WHERE status = 'queued' ORDER BY score DESC LIMIT ?",
            (limit,),
        ).fetchall()
    for row in rows:
        result = draft_outreach(int(row["id"]))
        if result.get("ok"):
            drafted += 1
    return {"ok": True, "drafted": drafted, "limit": limit}


def record_preorder(email: str, wallet: str = "", amount_usd: float = 100.0, prospect_id: int | None = None) -> dict[str, Any]:
    init_agent_sales_db()
    email = (email or "").strip().lower()
    if not email:
        return {"ok": False, "error": "email_required"}
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as db:
        db.execute(
            "INSERT INTO preorders (prospect_id, email, wallet, amount_usd, status, created_at) VALUES (?,?,?,?,?,?)",
            (prospect_id, email, wallet.strip(), amount_usd, "pending", now),
        )
        if prospect_id:
            db.execute(
                "UPDATE prospects SET status='preordered', updated_at=? WHERE id=?",
                (now, prospect_id),
            )
        db.commit()
        count = db.execute("SELECT COUNT(*) AS c FROM preorders").fetchone()["c"]
    cfg = load_config()
    goal = int(cfg.get("goal_preorders", 10))
    return {"ok": True, "preorders": count, "goal": goal, "goal_met": count >= goal}


def pipeline_summary() -> dict[str, Any]:
    init_agent_sales_db()
    cfg = load_config()
    with _conn() as db:
        by_status = {
            r["status"]: r["c"]
            for r in db.execute(
                "SELECT status, COUNT(*) AS c FROM prospects GROUP BY status"
            ).fetchall()
        }
        preorders = db.execute("SELECT COUNT(*) AS c FROM preorders").fetchone()["c"]
        total = db.execute("SELECT COUNT(*) AS c FROM prospects").fetchone()["c"]
        due = db.execute(
            """SELECT id, segment_id, name, status, score, draft_subject
               FROM prospects WHERE status IN ('drafted','researched') ORDER BY score DESC LIMIT 30"""
        ).fetchall()
    goal = int(cfg.get("goal_preorders", 10))
    return {
        "goal_preorders": goal,
        "preorders": preorders,
        "goal_met": preorders >= goal,
        "prospect_target": cfg.get("prospect_target", 200),
        "prospects_total": total,
        "by_status": by_status,
        "due_outreach": [dict(r) for r in due],
        "segments": cfg.get("segments", []),
    }


def list_prospects(limit: int = 50) -> list[dict[str, Any]]:
    init_agent_sales_db()
    with _conn() as db:
        rows = db.execute(
            """SELECT id, segment_id, name, contact, status, score, draft_subject, updated_at
               FROM prospects ORDER BY score DESC, id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]