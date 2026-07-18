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

It also registers the REAL ``/api/polyclaw/status`` view. Because mvp_app.py
initialises this scheduler before defining its own legacy status route,
Flask's first-registered rule wins and this real implementation serves the
endpoint (public sees mode/liveness only; admin sees full financials).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

_scheduler: Optional[Any] = None


# ---------------------------------------------------------------------------
# Real status endpoint (registered ahead of the legacy one in mvp_app.py)
# ---------------------------------------------------------------------------

def _status_view():
    from datetime import datetime
    from flask import jsonify, request

    scheduler_running = bool(
        _scheduler is not None and getattr(_scheduler, "running", False)
    )
    base = {
        "enabled": os.getenv("POLYCLAW_ENABLED", "true").lower() == "true",
        "scheduler_running": scheduler_running,
        "live_mode": os.getenv("POLYCLAW_LIVE", "false").lower() == "true",
        "cycle_interval_sec": int(os.getenv("POLYCLAW_CYCLE_INTERVAL_SEC", "90")),
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
            result = run_cycle()
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
    logger.info(
        "[POLYCLAW] live scheduler started (every %ds, mode=%s)",
        interval,
        "LIVE" if os.getenv("POLYCLAW_LIVE", "false").lower() == "true" else "DRY-RUN",
    )
    return scheduler


def stop_polyclaw_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[POLYCLAW] scheduler stopped")
    _scheduler = None


if __name__ == "__main__":
    import time
    os.environ.setdefault("POLYCLAW_ENABLED", "true")
    start_polyclaw_scheduler(None)
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        stop_polyclaw_scheduler()
