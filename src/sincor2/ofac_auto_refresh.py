"""OFAC SDN auto-refresh — boot + daily background loop.

The ``wallet-sanctions-screen`` paid resource fails closed with
``503 snapshot_stale`` once its snapshot ages past the P20 48h
freshness window, so the snapshot must be refreshed at boot and at
least daily without any operator action. This module is the wiring:
:func:`start_auto_refresh` runs
:func:`sincor2.refresh_ofac_sdn.refresh` immediately (the boot
refresh) and then every ``interval_seconds`` (default 24h) on a
daemon thread.

Design rules:

- NEVER raises — a refresh failure must never break app boot or kill
  the loop. Every failure is logged and swallowed; the installed
  snapshot stays untouched on failure (handled inside
  :func:`sincor2.refresh_ofac_sdn.refresh`).
- Idempotent — starting twice is a no-op; the second call returns
  ``False``.
- Kill switch — env ``SINCOR_OFAC_AUTO_REFRESH=0`` (or ``false``,
  case-insensitive) disables the loop entirely.

The refresh function is looked up through the
:mod:`sincor2.refresh_ofac_sdn` module at call time so tests can
monkeypatch ``sincor2.refresh_ofac_sdn.refresh``.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from sincor2 import refresh_ofac_sdn

logger = logging.getLogger("sincor2.ofac_auto_refresh")

__all__ = ["refresh_once", "start_auto_refresh"]

_DEFAULT_INTERVAL_SECONDS = 86400.0
_DISABLED_VALUES = frozenset({"0", "false"})

_lock = threading.Lock()
_started = False
_thread: threading.Thread | None = None
_stop_event = threading.Event()


def refresh_once() -> dict[str, Any]:
    """Run one OFAC refresh. Returns a result dict; never raises.

    On success the dict is the status dict returned by
    :func:`sincor2.refresh_ofac_sdn.refresh` plus ``ok: True``.
    On failure it is ``{"ok": False, "error": ...}``.
    """
    try:
        result = refresh_ofac_sdn.refresh()
    except Exception as exc:  # noqa: BLE001 — never propagate
        logger.warning("OFAC auto-refresh failed: %s: %s",
                       type(exc).__name__, exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    logger.info("OFAC auto-refresh ok: %s", result)
    return {"ok": True, **result}


def _disabled() -> bool:
    value = os.environ.get("SINCOR_OFAC_AUTO_REFRESH")
    if value is None:
        return False
    return value.strip().lower() in _DISABLED_VALUES


def _loop(interval_seconds: float) -> None:
    while not _stop_event.is_set():
        try:
            refresh_once()
        except Exception:  # refresh_once never raises; belt and braces
            logger.exception("OFAC auto-refresh loop iteration failed")
        _stop_event.wait(interval_seconds)


def start_auto_refresh(interval_seconds: float = _DEFAULT_INTERVAL_SECONDS) -> bool:
    """Start the boot + periodic OFAC refresh loop.

    Returns ``True`` when this call started the thread, ``False``
    when the loop was already running or the kill switch is on.
    Never raises.
    """
    global _started, _thread
    if _disabled():
        logger.info("OFAC auto-refresh disabled via SINCOR_OFAC_AUTO_REFRESH")
        return False
    with _lock:
        if _started:
            return False
        _started = True
        _stop_event.clear()
        _thread = threading.Thread(
            target=_loop,
            args=(float(interval_seconds),),
            name="sincor2-ofac-auto-refresh",
            daemon=True,
        )
        _thread.start()
    logger.info("OFAC auto-refresh started (interval %.0fs)", interval_seconds)
    return True


def _reset_for_tests() -> None:
    """Stop the loop and clear state. Private test hook only."""
    global _started, _thread
    _stop_event.set()
    with _lock:
        thread = _thread
        _started = False
        _thread = None
    if thread is not None:
        thread.join(timeout=5.0)
    _stop_event.clear()
