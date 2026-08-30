#!/usr/bin/env python3
"""Polyclaw scheduler shim — production entry point used by mvp_app.py.

The previous implementation traded on ``random.gauss()`` "edges" and
``random.uniform()`` "market prices", recording paper PnL from coin flips
and zero PnL in live mode. That code is deleted.

This shim keeps the exact module-level API mvp_app.py expects
(``start_polyclaw_scheduler(app)`` / ``stop_polyclaw_scheduler()``) but every
cycle now runs the REAL stack:

    forecasting_engine.scan_opportunities()   real Polymarket markets
    bankroll                                  equity-proportional risk gates
    execution_adapter                         CLOB FOK orders (dry-run default)
    shadow_portfolio                          silent 25% TOA-blend A/B twin

On LIVE startup it also forces CLOB client init + on-chain USDC.e/CTF
approvals so a quiet market scan cannot leave a funded wallet unapproved.

It also registers the REAL ``/api/polyclaw/status`` view. Because mvp_app.py
initialises this scheduler before defining its own legacy status route,
Flask's first-registered rule wins and this real implementation serves the
endpoint (public sees mode/liveness only; admin sees full financials).
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

_scheduler: Optional[Any] = None
_live_adapter: Optional[Any] = None
_bootstrap_error: Optional[str] = None


def _has_any_private_key() -> bool:
    """True if any of the accepted private-key env aliases is set."""
    for name in ("POLYMARKET_PRIVATE_KEY", "POLYCLAW_PRIVATE_KEY", "POLYMARKET_PK"):
        if os.getenv(name, "").strip():
            return True
    return False


def _bootstrap_live_client() -> None:
    """Warm CLOB client + on-chain allowances immediately in live mode.

    Must not raise into the web worker — failures are logged and stored for
    status. Without this, allowances only ran on the first successful order
    path and never executed when no opportunity passed filters.
    """
    global _live_adapter, _bootstrap_error
    if os.getenv("POLYCLAW_LIVE", "false").lower() != "true":
        return
    if not _has_any_private_key():
        _bootstrap_error = (
            "no private key in POLYMARKET_PRIVATE_KEY / "
            "POLYCLAW_PRIVATE_KEY / POLYMARKET_PK"
        )
        logger.error("[POLYCLAW] live mode on but %s", _bootstrap_error)
        return
    try:
        from sincor2.execution_adapter import PolymarketAdapter

        adapter = PolymarketAdapter()
        # Force full live init: API creds + USDC.e/CTF approvals + CLOB cache.
        adapter._get_client()
        _live_adapter = adapter
        _bootstrap_error = None
        logger.info(
            "[POLYCLAW] live bootstrap OK address=%s allowances_ready=%s",
            adapter.trading_address(),
            adapter._allowances_ready,
        )
    except Exception as exc:
        _bootstrap_error = str(exc)[:300]
        logger.exception("[POLYCLAW] live bootstrap FAILED: %s", exc)


# ---------------------------------------------------------------------------
# Real status endpoint (registered ahead of the legacy one in mvp_app.py)
# ---------------------------------------------------------------------------

def _status_view():
    from datetime import datetime
    from flask import jsonify, request

    scheduler_running = bool(
        _scheduler is not None and getattr(_scheduler, "running", False)
    )
    live = os.getenv("POLYCLAW_LIVE", "false").lower() == "true"
    addr = None
    allowances_ready = False
    key_error = None
    if _live_adapter is not None:
        try:
            addr = _live_adapter.trading_address()
            allowances_ready = bool(getattr(_live_adapter, "_allowances_ready", False))
            key_error = getattr(_live_adapter, "key_error", lambda: None)()
        except Exception as exc:
            key_error = str(exc)[:200]
    if addr is None:
        try:
            from sincor2.execution_adapter import PolymarketAdapter

            adapter = PolymarketAdapter()
            addr = adapter.trading_address()
            key_error = adapter.key_error()
        except Exception as exc:
            key_error = str(exc)[:200]

    base = {
        "enabled": os.getenv("POLYCLAW_ENABLED", "true").lower() == "true",
        "scheduler_running": scheduler_running,
        "live_mode": live,
        "cycle_interval_sec": int(os.getenv("POLYCLAW_CYCLE_INTERVAL_SEC", "90")),
        "trading_address": addr,
        "allowances_ready": allowances_ready,
        "bootstrap_error": _bootstrap_error,
        "key_error": key_error,
        "timestamp": datetime.utcnow().isoformat(),
    }
    # Admin-only financial detail (auth helpers live in mvp_app; by request
    # time the module is fully loaded, so the lazy import is safe).
    try:
        from sincor2.mvp_app import _check_admin_key, _check_admin_token
        is_admin = _check_admin_token(request) or _check_admin_key(request)
    except Exception:
        is_admin = False
    if not is_admin:
        return jsonify(base), 200

    try:
        from sincor2.bankroll import get_bankroll
        from sincor2.shadow_portfolio import compare_performance
        bankroll = get_bankroll()
        base.update({
            "bankroll": bankroll.snapshot(),
            "open_positions": bankroll.open_trades()[:25],
            "kill_switch": bankroll.kill_switch_active(),
            "ab_test": compare_performance(),
            "treasury": os.getenv(
                "TREASURY_ADDRESS", "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
            ),
        })
    except Exception as exc:
        base["detail_error"] = str(exc)
    return jsonify(base), 200


def _register_status_route(app: Any) -> None:
    """Register the real status view; Flask's first-registered rule wins."""
    try:
        app.add_url_rule(
            "/api/polyclaw/status",
            endpoint="polyclaw_live_status",
            view_func=_status_view,
            methods=["GET"],
        )
        logger.info("[POLYCLAW] real /api/polyclaw/status registered")
    except Exception as exc:  # route may already exist; never break startup
        logger.debug("[POLYCLAW] status route registration skipped: %s", exc)


# ---------------------------------------------------------------------------
# Scheduler lifecycle (API expected by mvp_app.py)
# ---------------------------------------------------------------------------

def start_polyclaw_scheduler(app: Any = None) -> Optional[Any]:
    """Start the real trading loop and register the real status endpoint.

    Returns the BackgroundScheduler (or None if disabled/unavailable).
    """
    global _scheduler

    if app is not None:
        _register_status_route(app)

    if os.getenv("POLYCLAW_ENABLED", "true").lower() != "true":
        logger.info("[POLYCLAW] disabled via POLYCLAW_ENABLED=false")
        return None
    if _scheduler is not None:
        return _scheduler

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning("[POLYCLAW] APScheduler not installed — loop not started")
        return None

    from sincor2.polyclaw_mega_aggressive_live import run_cycle

    interval = int(os.getenv("POLYCLAW_CYCLE_INTERVAL_SEC", "90"))
    scheduler = BackgroundScheduler(daemon=True)

    def _job() -> None:
        try:
            # Re-attempt bootstrap if a previous start failed (e.g. RPC blip).
            if (
                os.getenv("POLYCLAW_LIVE", "false").lower() == "true"
                and (
                    _live_adapter is None
                    or not getattr(_live_adapter, "_allowances_ready", False)
                )
            ):
                _bootstrap_live_client()
            result = run_cycle(adapter=_live_adapter)
            if result.get("status") == "halted":
                logger.warning("[POLYCLAW] cycle halted: kill switch")
        except Exception:
            logger.exception("[POLYCLAW] cycle crashed")

    scheduler.add_job(
        _job,
        trigger="interval",
        seconds=interval,
        id="polyclaw_scan",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler

    # Do not block gunicorn worker boot on Polygon RPC; run approvals async.
    if os.getenv("POLYCLAW_LIVE", "false").lower() == "true":
        threading.Thread(
            target=_bootstrap_live_client,
            name="polyclaw-live-bootstrap",
            daemon=True,
        ).start()

    logger.info(
        "[POLYCLAW] live scheduler started (every %ds, mode=%s)",
        interval,
        "LIVE" if os.getenv("POLYCLAW_LIVE", "false").lower() == "true" else "DRY-RUN",
    )
    return scheduler


def stop_polyclaw_scheduler() -> None:
    global _scheduler, _live_adapter
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[POLYCLAW] scheduler stopped")
    _scheduler = None
    _live_adapter = None


if __name__ == "__main__":
    import time
    os.environ.setdefault("POLYCLAW_ENABLED", "true")
    start_polyclaw_scheduler(None)
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        stop_polyclaw_scheduler()
