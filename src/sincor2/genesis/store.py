"""SQLite applications table for the Genesis license funnel."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    wallet TEXT,
    referral_code TEXT UNIQUE,
    referrer_application_id INTEGER,
    priority_id INTEGER NOT NULL DEFAULT 0,
    founding_badge INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'applied',
    converted_referrals INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def application_number(row_id: int) -> str:
    return f"#{row_id:06d}"


def referral_code_for(row_id: int) -> str:
    return application_number(row_id)


def parse_ref(raw: str | None) -> int | None:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("#"):
        text = text[1:]
    if text.isdigit():
        value = int(text)
        return value if value > 0 else None
    return None


def apply_email(conn: sqlite3.Connection, email: str, referrer_id: int | None = None) -> dict[str, Any]:
    email = (email or "").strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise ValueError("invalid_email")
    existing = conn.execute("SELECT * FROM applications WHERE email=?", (email,)).fetchone()
    if existing:
        return dict(existing)
    cur = conn.execute(
        """INSERT INTO applications
           (email, wallet, referral_code, referrer_application_id, priority_id, founding_badge, status, converted_referrals, created_at)
           VALUES (?, NULL, NULL, ?, 0, 0, 'applied', 0, ?)""",
        (email, referrer_id, utcnow()),
    )
    row_id = int(cur.lastrowid)
    code = referral_code_for(row_id)
    conn.execute("UPDATE applications SET referral_code=? WHERE id=?", (code, row_id))
    conn.commit()
    row = conn.execute("SELECT * FROM applications WHERE id=?", (row_id,)).fetchone()
    return dict(row)


def mark_verified(conn: sqlite3.Connection, application_id: int, wallet: str | None = None) -> dict[str, Any]:
    conn.execute(
        "UPDATE applications SET status='verified', wallet=COALESCE(?, wallet) WHERE id=?",
        (wallet, application_id),
    )
    row = conn.execute("SELECT * FROM applications WHERE id=?", (application_id,)).fetchone()
    if row and row["referrer_application_id"]:
        increment_converted(conn, int(row["referrer_application_id"]))
    conn.commit()
    return dict(row) if row else {}


def increment_converted(conn: sqlite3.Connection, referrer_id: int) -> dict[str, Any]:
    """converted count >= 3 -> priority_id; >= 10 -> founding_badge."""
    conn.execute(
        "UPDATE applications SET converted_referrals = converted_referrals + 1 WHERE id=?",
        (referrer_id,),
    )
    row = conn.execute("SELECT * FROM applications WHERE id=?", (referrer_id,)).fetchone()
    if not row:
        return {}
    converted = int(row["converted_referrals"])
    priority = 1 if converted >= 3 else int(row["priority_id"])
    founding = 1 if converted >= 10 else int(row["founding_badge"])
    conn.execute(
        "UPDATE applications SET priority_id=?, founding_badge=? WHERE id=?",
        (priority, founding, referrer_id),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM applications WHERE id=?", (referrer_id,)).fetchone()
    return dict(updated) if updated else {}


def stats(conn: sqlite3.Connection, holders: int) -> dict[str, int]:
    signups = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    verifications = conn.execute(
        "SELECT COUNT(*) FROM applications WHERE status='verified'"
    ).fetchone()[0]
    return {
        "signups": int(signups),
        "verifications": int(verifications),
        "holders": int(holders),
        "licenses_left": max(0, 1000 - int(verifications)),
    }


def leaderboard(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT id, email, referral_code, converted_referrals, priority_id, founding_badge, status
           FROM applications ORDER BY converted_referrals DESC, id ASC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def verified_tranche(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT wallet, converted_referrals, id FROM applications
           WHERE status='verified' AND wallet IS NOT NULL AND wallet != ''
           ORDER BY converted_referrals DESC, id ASC"""
    ).fetchall()
    return [dict(r) for r in rows]
