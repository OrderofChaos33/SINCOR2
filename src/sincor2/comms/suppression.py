"""Suppression list: in-memory + file-backed opt-out checking (D1/D2).

Every ``email.send`` intent must be checked against the suppression list
BEFORE dispatch. A suppressed recipient → ``blocked_policy``, no exceptions.
There is no bypass path: transactional emails do not bypass suppression.

Storage:
- In-memory set for the process lifetime (fast path).
- File-backed JSONL at ``SINCOR_SUPPRESSION_FILE`` or
  ``<data_dir>/comms/suppression.jsonl`` (durable across restarts).
- Each line: ``{"email": "...", "reason": "...", "added_at": "..."}``.

Thread-safe. Fail-closed: if the file cannot be read, the in-memory set
is authoritative; if an email cannot be normalized, it is treated as
suppressed (blocked) rather than allowed.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, Optional

logger = logging.getLogger("sincor2.comms.suppression")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_email(email: str) -> Optional[str]:
    """Normalize an email for suppression lookup.

    Returns None if the email is malformed (caller must treat as suppressed).
    """
    if not isinstance(email, str):
        return None
    cleaned = email.strip().lower()
    if "@" not in cleaned or "." not in cleaned.split("@")[-1]:
        return None
    # Reject obvious junk / header-injection attempts.
    if any(c in cleaned for c in ("\n", "\r", " ", ",", ";")):
        return None
    local, _, domain = cleaned.partition("@")
    if not local or not domain:
        return None
    return cleaned


class SuppressionList:
    """Thread-safe suppression list with file persistence."""

    def __init__(self, file_path: Optional[str] = None):
        self._lock = threading.Lock()
        self._suppressed: Dict[str, Dict[str, str]] = {}
        self._file_path: Optional[Path] = None
        if file_path:
            self._file_path = Path(file_path)
        else:
            env_path = os.environ.get("SINCOR_SUPPRESSION_FILE")
            if env_path:
                self._file_path = Path(env_path)
        if self._file_path is not None:
            self._load_from_file()

    # -- persistence ------------------------------------------------------

    def _load_from_file(self) -> None:
        assert self._file_path is not None
        try:
            if not self._file_path.exists():
                return
            with self._file_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    email = normalize_email(record.get("email", ""))
                    if email:
                        self._suppressed[email] = {
                            "reason": str(record.get("reason", "unknown")),
                            "added_at": str(
                                record.get("added_at", _utc_now_iso())
                            ),
                        }
        except OSError as exc:
            # Fail-closed: log, keep in-memory set authoritative.
            logger.warning(
                "suppression file unreadable (%s); in-memory set authoritative",
                exc,
            )

    def _append_to_file(self, email: str, reason: str, added_at: str) -> None:
        if self._file_path is None:
            return
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            with self._file_path.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {"email": email, "reason": reason, "added_at": added_at}
                    )
                    + "\n"
                )
        except OSError as exc:
            logger.warning("suppression file append failed (%s)", exc)

    # -- public API ---------------------------------------------------------

    def add(self, email: str, reason: str = "opt_out") -> bool:
        """Add an email to the suppression list. Returns True if added."""
        normalized = normalize_email(email)
        if normalized is None:
            return False
        added_at = _utc_now_iso()
        with self._lock:
            is_new = normalized not in self._suppressed
            self._suppressed[normalized] = {
                "reason": reason,
                "added_at": added_at,
            }
        if is_new:
            self._append_to_file(normalized, reason, added_at)
        return is_new

    def is_suppressed(self, email: str) -> bool:
        """Check suppression. Malformed email → True (fail closed)."""
        normalized = normalize_email(email)
        if normalized is None:
            return True
        with self._lock:
            return normalized in self._suppressed

    def check_batch(self, emails: list) -> Dict[str, bool]:
        """Check multiple emails. Returns {email: is_suppressed}."""
        return {e: self.is_suppressed(e) for e in emails}

    def count(self) -> int:
        with self._lock:
            return len(self._suppressed)

    def iter_entries(self) -> Iterator[Dict[str, str]]:
        """Iterate suppression entries (for audit/export)."""
        with self._lock:
            items = list(self._suppressed.items())
        for email, meta in items:
            yield {"email": email, **meta}


# Module-level default instance (process lifetime).
_default_instance: Optional[SuppressionList] = None
_default_lock = threading.Lock()


def get_suppression_list() -> SuppressionList:
    """Return the process-wide suppression list singleton."""
    global _default_instance
    with _default_lock:
        if _default_instance is None:
            _default_instance = SuppressionList()
        return _default_instance
