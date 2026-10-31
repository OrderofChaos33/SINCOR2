"""Outbound-email compliance plumbing (Wren PR 8).

CAN-SPAM basics for the outreach lane, built BEFORE any approved-batch
send lane exists (the lane itself stays founder-gated and OFF):

  * Suppression list — SQLite table (email, reason, ts) under the shared
    data dir (``sincor2.data_paths.data_dir()``). Unsubscribing adds the
    address; ``comms_gating.outreach_send_allowed`` checks every send
    against it and fails closed if the check itself errors.
  * Signed one-click unsubscribe — an HMAC-SHA256 token naming the email
    (``make_unsubscribe_token`` / ``verify_unsubscribe_token``). Tampered
    or malformed tokens verify to None (fail closed); the route in
    ``blueprints/unsubscribe.py`` answers 404 for them.
  * Physical postal address — read from ``SINCOR_POSTAL_ADDRESS``. The
    outreach engine refuses to send when it is unset and stamps it in
    the footer of every message when set.
  * Send accounting + caps — every successful outreach send is recorded
    here so the per-domain cap (``OUTREACH_PER_DOMAIN_DAILY_LIMIT``,
    default 3/day) and the global daily cap (``OUTREACH_DAILY_LIMIT``,
    default 40) are enforced against live counts, read at send time.
  * SPF/DKIM/DMARC presence check — best-effort DNS TXT lookups, logged
    by the outreach cycle at boot. Never fatal, never raises.

Env vars:
  SINCOR_POSTAL_ADDRESS            physical postal address for footers
  UNSUBSCRIBE_TOKEN_SECRET         HMAC secret for unsubscribe tokens
                                   (falls back to SECRET_KEY / JWT_SECRET_KEY)
  SINCOR_PUBLIC_BASE_URL           base for unsubscribe links
                                   (default https://getsincor.com)
  OUTREACH_PER_DOMAIN_DAILY_LIMIT  max sends per recipient domain per day (3)
  OUTREACH_DAILY_LIMIT             global outreach cap per day (40)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Dict, Optional

from sincor2.data_paths import data_dir

logger = logging.getLogger("sincor2.comms_compliance")

_DEFAULT_BASE_URL = "https://getsincor.com"
_DKIM_SELECTORS = ("default", "resend", "s1", "s2", "google", "mail")


# ---------------------------------------------------------------------------
# Storage — suppression list + send log (SQLite under the shared data dir)
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    return data_dir() / "comms_compliance.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS suppressions ("
        "email TEXT PRIMARY KEY, reason TEXT NOT NULL, ts REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS outreach_send_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "email TEXT NOT NULL, domain TEXT NOT NULL, ts REAL NOT NULL)"
    )
    return conn


def _normalize(email: str) -> str:
    return (email or "").strip().lower()


def _domain_of(email: str) -> str:
    return _normalize(email).rsplit("@", 1)[-1]


def suppress(email: str, reason: str = "unsubscribe") -> None:
    """Add an address to the suppression list (idempotent)."""
    addr = _normalize(email)
    if not addr:
        return
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO suppressions (email, reason, ts) VALUES (?, ?, ?)",
            (addr, reason, time.time()),
        )
    logger.info("[COMMS] Suppressed %s (%s)", addr, reason)


def is_suppressed(email: str) -> bool:
    """True when the address is on the suppression list."""
    addr = _normalize(email)
    if not addr:
        return False
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM suppressions WHERE email = ?", (addr,)
        ).fetchone()
    return row is not None


def record_send(email: str) -> None:
    """Record one successful outreach send for cap accounting."""
    addr = _normalize(email)
    if not addr:
        return
    with _connect() as conn:
        conn.execute(
            "INSERT INTO outreach_send_log (email, domain, ts) VALUES (?, ?, ?)",
            (addr, _domain_of(addr), time.time()),
        )


def sends_today(domain: Optional[str] = None) -> int:
    """Sends recorded since 00:00 UTC today (optionally for one domain)."""
    with _connect() as conn:
        if domain:
            row = conn.execute(
                "SELECT COUNT(*) FROM outreach_send_log "
                "WHERE domain = ? AND date(ts, 'unixepoch') = date('now')",
                (domain.strip().lower(),),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM outreach_send_log "
                "WHERE date(ts, 'unixepoch') = date('now')"
            ).fetchone()
    return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# Caps — read live from env on every call
# ---------------------------------------------------------------------------

def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def per_domain_cap() -> int:
    return _int_env("OUTREACH_PER_DOMAIN_DAILY_LIMIT", 3)


def global_daily_cap() -> int:
    return _int_env("OUTREACH_DAILY_LIMIT", 40)


def domain_cap_reached(email: str) -> bool:
    return sends_today(_domain_of(email)) >= per_domain_cap()


def global_cap_reached() -> bool:
    return sends_today() >= global_daily_cap()


# ---------------------------------------------------------------------------
# Postal address (CAN-SPAM footer)
# ---------------------------------------------------------------------------

def postal_address() -> str:
    """Physical postal address for email footers; '' when unset (no send)."""
    return os.environ.get("SINCOR_POSTAL_ADDRESS", "").strip()


# ---------------------------------------------------------------------------
# Signed unsubscribe tokens (HMAC over the email; fail closed)
# ---------------------------------------------------------------------------

def _token_secret() -> Optional[bytes]:
    for var in ("UNSUBSCRIBE_TOKEN_SECRET", "SECRET_KEY", "JWT_SECRET_KEY"):
        val = os.environ.get(var, "").strip()
        if val:
            return val.encode("utf-8")
    return None


def _sign(secret: bytes, email: str) -> str:
    return hmac.new(
        secret, f"unsubscribe:{email}".encode("utf-8"), hashlib.sha256
    ).hexdigest()


def make_unsubscribe_token(email: str) -> Optional[str]:
    """Signed token naming the email; None when no secret is configured."""
    secret = _token_secret()
    addr = _normalize(email)
    if secret is None or not addr:
        return None
    payload = base64.urlsafe_b64encode(addr.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{payload}.{_sign(secret, addr)}"


def verify_unsubscribe_token(token: str) -> Optional[str]:
    """Return the token's email when the signature checks out, else None."""
    secret = _token_secret()
    if secret is None or not token or "." not in token:
        return None
    payload, _, sig = token.partition(".")
    try:
        padded = payload + "=" * (-len(payload) % 4)
        addr = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception:
        return None
    addr = _normalize(addr)
    if not addr or "@" not in addr:
        return None
    if not hmac.compare_digest(sig, _sign(secret, addr)):
        return None
    return addr


def unsubscribe_url(email: str) -> Optional[str]:
    """Absolute one-click unsubscribe URL; None when it cannot be signed."""
    token = make_unsubscribe_token(email)
    if token is None:
        return None
    base = os.environ.get("SINCOR_PUBLIC_BASE_URL", "").strip() or _DEFAULT_BASE_URL
    return f"{base.rstrip('/')}/unsubscribe/{token}"


# ---------------------------------------------------------------------------
# SPF / DKIM / DMARC presence check — best effort, logged, never fatal
# ---------------------------------------------------------------------------

def _txt_records(name: str) -> list:
    """TXT record strings for a name; [] on any DNS failure."""
    try:
        import dns.resolver

        answers = dns.resolver.resolve(name, "TXT", lifetime=3)
        out = []
        for ans in answers:
            try:
                out.append(ans.to_text().strip('"'))
            except Exception:
                out.append(str(ans).strip('"'))
        return out
    except Exception:
        return []


def check_sender_dns_auth(domain: str) -> Dict[str, Optional[bool]]:
    """Presence check for SPF / DMARC / DKIM on a sender domain.

    Returns {"domain", "spf", "dmarc", "dkim"} with True = record found,
    False = lookup completed, no record, None = could not determine
    (DNS failure, or DKIM selector unknown — DKIM cannot be verified
    without knowing the selector, so common selectors are probed and a
    miss stays None rather than a false negative). Never raises.
    """
    result: Dict[str, Optional[bool]] = {
        "domain": domain, "spf": None, "dmarc": None, "dkim": None,
    }
    try:
        domain = (domain or "").strip().lower()
        if not domain:
            return result
        result["domain"] = domain
        result["spf"] = any(
            rec.lower().startswith("v=spf1") for rec in _txt_records(domain)
        )
        result["dmarc"] = any(
            rec.lower().startswith("v=dmarc1")
            for rec in _txt_records(f"_dmarc.{domain}")
        )
        for selector in _DKIM_SELECTORS:
            records = _txt_records(f"{selector}._domainkey.{domain}")
            if any("v=dkim1" in rec.lower() or "k=" in rec.lower() for rec in records):
                result["dkim"] = True
                break
    except Exception as e:  # the check must never take the lane down
        logger.warning("[COMMS] DNS auth check failed for %s: %s", domain, e)
    return result


def log_sender_dns_auth_status(domain: Optional[str] = None) -> Dict[str, Optional[bool]]:
    """Run the DNS-auth presence check and log the outcome (boot step)."""
    if not domain:
        from_addr = (
            os.environ.get("OUTREACH_FROM_EMAIL")
            or os.environ.get("SINCOR_EMAIL", "support@getsincor.com")
        )
        domain = from_addr.rsplit("@", 1)[-1] if "@" in from_addr else "getsincor.com"
    result = check_sender_dns_auth(domain)
    logger.info(
        "[COMMS] Sender DNS auth for %s: SPF=%s DMARC=%s DKIM=%s",
        result["domain"], result["spf"], result["dmarc"], result["dkim"],
    )
    if result["spf"] is not True:
        logger.warning("[COMMS] No SPF record confirmed for %s", result["domain"])
    if result["dmarc"] is not True:
        logger.warning("[COMMS] No DMARC record confirmed for %s", result["domain"])
    return result
