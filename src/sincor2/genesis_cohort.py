"""Genesis cohort allocations — persists on the Railway /data volume.

W-136 fix (2026-11-06): a claim no longer binds a wallet on request.
The old flow bound any email to any wallet with no ownership proof, so
an attacker could squat a victim's email with the attacker's wallet
and the victim's later claim kept the attacker's wallet bound.

Flow now:
  1. ``claim()`` creates a PENDING claim (email + requested wallet)
     and emails a single-use confirmation link to that email. It binds
     nothing and reveals nothing: the response is the same neutral
     "check your email" receipt for new, pending, and existing emails
     (no allocation number, no existence flag).
  2. ``confirm_claim(token)`` — reached only via the emailed link —
     binds the wallet. If the email is already a confirmed member, the
     confirmed reclaim REPLACES the bound wallet. An unconfirmed
     request changes nothing.

Email delivery follows the onboarding-challenge pattern
(mvp_blueprints/auth.py): FAIL CLOSED. If the sender is unavailable /
in stub mode, no usable pending claim is created and nothing binds.

Token security: only a SHA-256 hash of the token is stored (the token
itself is a 256-bit ``secrets.token_urlsafe`` value, so a plain hash
lookup is safe); the hash comparison uses ``hmac.compare_digest``;
tokens expire after ``TOKEN_TTL_SECONDS`` and are single-use (the
pending row is deleted on successful confirmation).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

from sincor2.data_paths import data_dir

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

# Magic-link lifetime: long enough for a phone user to open their mail
# app and tap through, short enough that a leaked link ages out fast.
TOKEN_TTL_SECONDS = 60 * 60  # 1 hour

# Injectable email sender override (tests / app wiring). When set, it
# takes precedence over every other resolution path in get_email_sender().
email_sender = None

_NEUTRAL_MESSAGE = "Check your email to confirm your genesis claim."
_UNAVAILABLE_ERROR = "Email confirmation is unavailable right now. Please try again later."
_CONFIRM_ERROR = "Invalid or expired confirmation link."


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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS genesis_pending_claims (
            email TEXT PRIMARY KEY,
            wallet TEXT,
            token_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
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
    because the attribution lookup hit a snag. Only CONFIRMED members
    are returned; pending claims are invisible here.
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


# ---------------------------------------------------------------------------
# Email sender resolution (injectable for tests)
# ---------------------------------------------------------------------------

def get_email_sender():
    """Resolve the transactional email sender for confirmation mail.

    Resolution order (first non-None wins):
      1. this module's ``email_sender`` override attribute — the test /
         wiring injection point (monkeypatch
         ``sincor2.genesis_cohort.email_sender`` or this function);
      2. the already-loaded app modules' ``email_sender`` (mvp_app
         builds one at startup and the blueprints bind it) — checked
         via ``sys.modules`` so importing this module never boots the
         app as a side effect;
      3. a fresh sender from ``sincor2.email_sender.get_email_sender``.
    """
    if email_sender is not None:
        return email_sender
    for mod_name in ("sincor2.mvp_blueprints.pages", "sincor2.mvp_app"):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            sender = getattr(mod, "email_sender", None)
            if sender is not None:
                return sender
    try:
        from sincor2.email_sender import get_email_sender as _factory

        return _factory()
    except Exception:
        return None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _confirm_url(token: str) -> str:
    base = (
        os.environ.get("SINCOR_PUBLIC_BASE_URL")
        or os.environ.get("APP_BASE_URL")
        or "https://getsincor.com"
    ).rstrip("/")
    return f"{base}/api/genesis/confirm?token={token}"


def claim(email: str, password: str | None = None, wallet: str = "") -> dict:
    """Start a genesis claim for (email, optional wallet).

    Creates a PENDING claim and emails a confirmation link to the
    email address; the wallet binds only when that link's token is
    confirmed via :func:`confirm_claim`. The success response is a
    neutral receipt — identical for brand-new, pending, and already
    confirmed emails — with no allocation and no existence flag, so
    probing an email learns nothing and binds nothing.

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

    # Fail closed (auth.py onboarding_challenge pattern): without real
    # email delivery the confirmation could never reach the address
    # owner, so no usable pending claim is created at all.
    sender = get_email_sender()
    if sender is None or getattr(sender, "mode", "stub") == "stub":
        return {"ok": False, "error": _UNAVAILABLE_ERROR, "email_unavailable": True}

    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(seconds=TOKEN_TTL_SECONDS)).isoformat()
    with _connect() as conn:
        # One live pending claim per email: a fresh request replaces
        # (and thereby invalidates) any earlier unconfirmed token.
        conn.execute(
            "INSERT OR REPLACE INTO genesis_pending_claims"
            " (email, wallet, token_hash, expires_at, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (email, wallet, _hash_token(token), expires_at, now.isoformat()),
        )
        conn.commit()

    url = _confirm_url(token)
    try:
        result = sender.send_email(
            to_email=email,
            to_name=email.split("@")[0],
            subject="Confirm your SINCOR genesis claim",
            html_content=(
                "<p>Confirm your SINCOR genesis claim by opening this link:</p>"
                f'<p><a href="{url}">{url}</a></p>'
                "<p>It expires in 1 hour. If you did not request this, "
                "ignore this email — nothing is bound until the link is used.</p>"
            ),
            text_content=(
                "Confirm your SINCOR genesis claim by opening this link:\n"
                f"{url}\n"
                "It expires in 1 hour. If you did not request this, ignore "
                "this email — nothing is bound until the link is used."
            ),
            metadata={"purpose": "genesis_claim"},
        )
    except Exception:
        result = None
    if not isinstance(result, dict) or result.get("status") != "sent":
        # Delivery failed: the pending claim could never be confirmed
        # by its owner, so remove it rather than leaving a usable row.
        try:
            with _connect() as conn:
                conn.execute(
                    "DELETE FROM genesis_pending_claims WHERE email = ?",
                    (email,),
                )
                conn.commit()
        except Exception:
            pass
        return {"ok": False, "error": _UNAVAILABLE_ERROR, "email_unavailable": True}

    return {"ok": True, "status": "pending_confirmation", "message": _NEUTRAL_MESSAGE}


def confirm_claim(token: str) -> dict:
    """Confirm a pending genesis claim from its emailed token.

    On success the pending wallet binds: a new email becomes a member;
    an already-confirmed email has its bound wallet REPLACED (reclaim
    path — the emailed token is the proof of email ownership). The
    token is single-use. Every failure (unknown, tampered, expired, or
    replayed token) returns the same generic error.
    """
    token = (token or "").strip()
    if not token:
        return {"ok": False, "error": _CONFIRM_ERROR}
    token_hash = _hash_token(token)
    now = datetime.now(timezone.utc)
    with _connect() as conn:
        row = conn.execute(
            "SELECT email, wallet, token_hash, expires_at"
            " FROM genesis_pending_claims WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        if not row or not hmac.compare_digest(row["token_hash"], token_hash):
            return {"ok": False, "error": _CONFIRM_ERROR}
        try:
            expired = now >= datetime.fromisoformat(row["expires_at"])
        except Exception:
            expired = True
        if expired:
            conn.execute(
                "DELETE FROM genesis_pending_claims WHERE email = ?",
                (row["email"],),
            )
            conn.commit()
            return {"ok": False, "error": _CONFIRM_ERROR}

        email = row["email"]
        wallet = row["wallet"] or ""
        # Single-use: consume the token before binding.
        conn.execute(
            "DELETE FROM genesis_pending_claims WHERE email = ?", (email,)
        )
        existing = conn.execute(
            "SELECT id FROM genesis_cohort WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE genesis_cohort SET wallet = ? WHERE email = ?",
                (wallet, email),
            )
            conn.commit()
            allocation = f"{int(existing['id']):05d}"
            return {
                "ok": True,
                "existing": True,
                "email": email,
                "allocation": allocation,
                "wallet": wallet,
                "count": genesis_count(),
            }
        conn.execute(
            "INSERT INTO genesis_cohort (email, wallet, created_at) VALUES (?, ?, ?)",
            (email, wallet, now.isoformat()),
        )
        conn.commit()
        bound = conn.execute(
            "SELECT id FROM genesis_cohort WHERE email = ?", (email,)
        ).fetchone()
        allocation = f"{int(bound['id']):05d}"
        return {
            "ok": True,
            "existing": False,
            "email": email,
            "allocation": allocation,
            "wallet": wallet,
            "count": genesis_count(),
        }
