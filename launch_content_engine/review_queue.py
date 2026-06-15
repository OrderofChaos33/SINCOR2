"""SQLite review queue — agents draft, human approves, adapters post."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "human_review_queue.db"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute(
        """CREATE TABLE IF NOT EXISTS drafts (
            id TEXT PRIMARY KEY,
            pipeline TEXT NOT NULL,
            channel TEXT NOT NULL,
            title TEXT,
            body TEXT NOT NULL,
            meta_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            posted_at TEXT,
            post_result TEXT
        )"""
    )
    c.commit()
    return c


def enqueue(
    pipeline: str,
    channel: str,
    body: str,
    *,
    title: str = "",
    meta: dict[str, Any] | None = None,
) -> str:
    draft_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            """INSERT INTO drafts (id, pipeline, channel, title, body, meta_json, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (draft_id, pipeline, channel, title, body, json.dumps(meta or {}), now),
        )
    return draft_id


def list_drafts(status: str | None = "pending", limit: int = 50) -> list[dict[str, Any]]:
    with _conn() as c:
        if status:
            rows = c.execute(
                "SELECT * FROM drafts WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM drafts ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["meta"] = json.loads(d.pop("meta_json") or "{}")
        out.append(d)
    return out


def get_draft(draft_id: str) -> dict[str, Any] | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["meta"] = json.loads(d.pop("meta_json") or "{}")
    return d


def set_status(draft_id: str, status: str, *, post_result: str = "") -> bool:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        row = c.execute("SELECT id FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if not row:
            return False
        reviewed = now if status in ("approved", "rejected") else None
        posted = now if status == "posted" else None
        c.execute(
            """UPDATE drafts SET status = ?, reviewed_at = COALESCE(reviewed_at, ?),
               posted_at = COALESCE(posted_at, ?), post_result = ?
               WHERE id = ?""",
            (status, reviewed, posted, post_result, draft_id),
        )
    return True


def approve_and_post(draft_id: str) -> dict[str, Any]:
    draft = get_draft(draft_id)
    if not draft:
        return {"ok": False, "error": "not_found"}
    if draft["status"] not in ("pending", "approved"):
        return {"ok": False, "error": f"invalid_status:{draft['status']}"}

    try:
        import sys
        from pathlib import Path

        root = str(Path(__file__).resolve().parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        from sincor2.compliance_guardrails import guardrails

        guardrails.check_content(
            f"{draft.get('title', '')}\n{draft.get('body', '')}",
            agent="launch_review",
            content_type="social_post",
        )
    except Exception as e:
        return {"ok": False, "error": "guardrail_block", "detail": str(e)}

    from launch_content_engine.adapters.farcaster_api import post_approved_draft

    result = post_approved_draft(draft)
    if result.get("ok"):
        set_status(draft_id, "posted", post_result=json.dumps(result))
    else:
        set_status(draft_id, "approved", post_result=json.dumps(result))
    return result