"""Genesis cohort allocations — persists on the Railway /data volume."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import secrets
from datetime import datetime, timezone

from sincor2.data_paths import data_dir

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def _db_path():
    return data_dir() / "genesis_cohort.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS genesis_cohort (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            wallet TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120_000,
    ).hex()
    return f"{salt}${digest}"


def genesis_count() -> int:
    try:
        with _connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM genesis_cohort").fetchone()
            return int(row["n"] if row else 0)
    except Exception:
        return 0


def get_member(email: str) -> dict | None:
    """Read-only lookup: return the genesis cohort record for an email, or None.

    Never raises — callers on the payment path must not fail a payment
    because the attribution lookup hit a snag.
    """
    email = (email or "").strip().lower()
    if not email:
        return None
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT id, email, wallet, created_at FROM genesis_cohort WHERE email = ?",
                (email,),
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return {
        "email": row["email"],
        "allocation": f"{int(row['id']):05d}",
        "wallet": row["wallet"] or "",
        "created_at": row["created_at"],
    }


def get_member_by_wallet(wallet: str) -> dict | None:
    """Read-only lookup: return the genesis cohort record for a registered
    wallet address, or None.

    The wallet is the secondary attribution key on the x402 rail: a member
    who pays from a wallet with no buyer_email is still attributable when
    the wallet was registered to their claim. Comparison is
    case-insensitive (wallets are stored as submitted at claim time).
    Never raises — same no-fail-attribution rule as get_member().
    """
    wallet = (wallet or "").strip().lower()
    if not wallet:
        return None
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT id, email, wallet, created_at FROM genesis_cohort "
                "WHERE LOWER(wallet) = ?",
                (wallet,),
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return {
        "email": row["email"],
        "allocation": f"{int(row['id']):05d}",
        "wallet": row["wallet"] or "",
        "created_at": row["created_at"],
    }


def is_genesis_member(email: str) -> bool:
    """Read-only: True if the email belongs to a claimed genesis cohort member."""
    return get_member(email) is not None


def claim(email: str, password: str, wallet: str = "") -> dict:
    email = (email or "").strip().lower()
    password = password or ""
    wallet = (wallet or "").strip()[:120]
    if not EMAIL_RE.match(email):
        return {"ok": False, "error": "Enter a valid email."}
    if len(password) < 8:
        return {"ok": False, "error": "Password must be at least 8 characters."}

    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM genesis_cohort WHERE email = ?",
            (email,),
        ).fetchone()
        if existing:
            allocation = f"{int(existing['id']):05d}"
            return {
                "ok": True,
                "existing": True,
                "allocation": allocation,
                "count": genesis_count(),
            }
        conn.execute(
            "INSERT INTO genesis_cohort (email, password_hash, wallet, created_at) VALUES (?, ?, ?, ?)",
            (email, _hash_password(password), wallet, now),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM genesis_cohort WHERE email = ?", (email,)).fetchone()
        allocation = f"{int(row['id']):05d}"
        return {
            "ok": True,
            "existing": False,
            "allocation": allocation,
            "count": genesis_count(),
        }
