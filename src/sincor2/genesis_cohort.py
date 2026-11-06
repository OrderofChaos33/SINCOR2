"""Genesis cohort allocations — persists on the Railway /data volume."""

from __future__ import annotations

import re
import sqlite3
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
            wallet TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    _drop_legacy_password_column(conn)
    conn.commit()
    return conn


def _drop_legacy_password_column(conn: sqlite3.Connection) -> None:
    """Migrate pre-fix databases: drop the stored password hash.

    The genesis claim used to collect a password and store a PBKDF2 hash,
    but customer password login is disabled (see the /login audit note in
    mvp_blueprints/pages.py), so the credential was a dead end — collected
    and stored, never usable. Existing volume databases still carry the
    column; rebuild the table without it, preserving every member row.
    """
    cols = [row[1] for row in conn.execute("PRAGMA table_info(genesis_cohort)")]
    if "password_hash" not in cols:
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS genesis_cohort_nopw (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            wallet TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO genesis_cohort_nopw (id, email, wallet, created_at)"
        " SELECT id, email, wallet, created_at FROM genesis_cohort"
    )
    conn.execute("DROP TABLE genesis_cohort")
    conn.execute("ALTER TABLE genesis_cohort_nopw RENAME TO genesis_cohort")


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


def claim(email: str, password: str | None = None, wallet: str = "") -> dict:
    """Claim a genesis allocation for (email, optional wallet).

    ``password`` is a deprecated shim: pre-fix callers pass one
    positionally, but it is ignored — never required, hashed, or stored.
    Customer password login is disabled, so a claim password could never
    be used anywhere; the claim form no longer collects one.
    """
    del password  # ignored by design (see docstring)
    email = (email or "").strip().lower()
    wallet = (wallet or "").strip()[:120]
    if not EMAIL_RE.match(email):
        return {"ok": False, "error": "Enter a valid email."}

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
            "INSERT INTO genesis_cohort (email, wallet, created_at) VALUES (?, ?, ?)",
            (email, wallet, now),
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
