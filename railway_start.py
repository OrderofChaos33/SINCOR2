"""Railway / Gunicorn entry.

Gunicorn imports this module, then binds PORT. /health is served from this
file with the stdlib only and is the deploy probe: it stays 200 only while
mvp_app is loaded or still inside its boot budget, and goes 503 when the
load has failed or exceeded the budget, so Railway fails a broken deploy
instead of promoting it.

Load happens in a background thread (post_worker_init + first request).
Failures retry. /ready tells the truth. /boot shows the exception.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger("sincor2.railway_start")
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

_LIVENESS = frozenset({"/health", "/api/health", "/healthz"})
_HEALTH = b'{"status":"healthy","service":"SINCOR2 MVP","entry":"railway_start"}'

# Boot budget for the deploy probe (/health). Comfortably under Railway's
# 120s healthcheckTimeout: a load that failed or is still stuck after this
# long is a broken deploy and must fail the probe so the previous good
# deployment keeps serving. A transient failure that recovers on retry flips
# back to 200 once loaded, so brief blips don't kill good deploys.
_HEALTH_BOOT_BUDGET_S = 90.0

_lock = threading.Lock()
_ready = threading.Event()
_thread_started = False
_full = None
_full_error = None
_attempts = 0
_last_attempt_at = 0.0
_started_at = time.time()

_RETRY_SECONDS = 8.0
_REQUEST_WAIT_SECONDS = 45.0


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _health_probe(start_response):
    """Truthful deploy probe for Railway's healthcheck.

    - mvp_app loaded -> 200 with the healthy JSON (unchanged).
    - Not loaded, load failed (_full_error set) or boot budget exceeded
      -> 503 with boot_status() JSON (error field included so the deploy
      log shows why).
    - Not loaded, no error, still inside the boot budget -> 200 (the app
      normally imports in ~10s; don't fail good deploys mid-boot).
    """
    start_background_load()
    with _lock:
        full = _full
        error = _full_error
    if full is not None:
        return _respond(start_response, "200 OK", _HEALTH)
    if error is not None or (time.time() - _started_at) >= _HEALTH_BOOT_BUDGET_S:
        return _respond(
            start_response, "503 Service Unavailable", _json_bytes(boot_status())
        )
    return _respond(start_response, "200 OK", _HEALTH)


def _safe_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ")
    text = re.sub(
        r"(?i)(secret|password|token|api[_-]?key|private[_-]?key)\s*[=:]\s*\S+",
        r"\1=<redacted>",
        text,
    )
    return text[:400]


def _load_once() -> bool:
    global _full, _full_error, _attempts, _last_attempt_at
    with _lock:
        if _full is not None:
            return True
        _attempts += 1
        _last_attempt_at = time.time()
        attempt = _attempts
    logger.info("[boot] loading sincor2.mvp_app (attempt %s)", attempt)
    try:
        from sincor2.mvp_app import app as full
    except Exception as exc:
        err = _safe_error(exc)
        logger.exception("[boot] mvp_app import failed (attempt %s): %s", attempt, err)
        with _lock:
            if _full is None:
                _full_error = err
        return False

    with _lock:
        _full = full
        _full_error = None
    _ready.set()
    logger.info("[boot] mvp_app loaded on attempt %s", attempt)
    return True


def _load_loop() -> None:
    delay = _RETRY_SECONDS
    while True:
        if _load_once():
            return
        time.sleep(delay)
        delay = min(delay * 1.5, 60.0)


def _start_ofac_auto_refresh() -> None:
    """Start the OFAC snapshot auto-refresh (boot + daily).

    Fully isolated: any import/start failure is logged and swallowed
    so it can never break app boot.
    """
    try:
        from sincor2.ofac_auto_refresh import start_auto_refresh

        start_auto_refresh()
    except Exception as exc:
        logger.warning("[boot] OFAC auto-refresh not started: %s", _safe_error(exc))


def start_background_load() -> None:
    """Idempotent. Called from gunicorn post_worker_init and on first request."""
    global _thread_started
    with _lock:
        if _thread_started:
            return
        _thread_started = True
        thread = threading.Thread(target=_load_loop, name="sincor2-boot", daemon=True)
        thread.start()
    _start_ofac_auto_refresh()


def _build_stamp() -> dict:
    """Deployed-build identifier (observability build #5), stdlib-only —
    this module must stay importable before sincor2.mvp_app loads. Mirrors
    sincor2.build_info: RAILWAY_GIT_COMMIT_SHA, then SINCOR_BUILD_SHA,
    then "unknown"; build time is SINCOR_BUILD_TIME or this process's
    start. Never raises."""
    try:
        sha = ""
        for var in ("RAILWAY_GIT_COMMIT_SHA", "SINCOR_BUILD_SHA"):
            sha = (os.environ.get(var) or "").strip()
            if sha:
                break
        stamp_time = (os.environ.get("SINCOR_BUILD_TIME") or "").strip()
        if not stamp_time:
            stamp_time = (
                datetime.fromtimestamp(_started_at, timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )
        return {
            "sha": sha or "unknown",
            "short": sha[:12] if sha else "unknown",
            "time": stamp_time,
        }
    except Exception:
        return {"sha": "unknown", "short": "unknown", "time": "unknown"}


def boot_status() -> dict:
    with _lock:
        loaded = _full is not None
        error = _full_error
        attempts = _attempts
        last_attempt_at = _last_attempt_at
    return {
        "status": "ready" if loaded else ("loading" if attempts == 0 or error is None else "error"),
        "service": "SINCOR2 MVP",
        "entry": "railway_start",
        "build": _build_stamp(),
        "loaded": loaded,
        "attempts": attempts,
        "error": error,
        "uptime_s": int(time.time() - _started_at),
        "last_attempt_age_s": (
            None if not last_attempt_at else int(time.time() - last_attempt_at)
        ),
    }


def _respond(start_response, status: str, body: bytes, content_type: str = "application/json"):
    start_response(
        status,
        [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ],
    )
    return [body]


def _call_full(environ, start_response):
    app = _full
    if app is None:
        payload = boot_status()
        payload["error"] = payload.get("error") or "mvp_app unavailable"
        return _respond(start_response, "503 Service Unavailable", _json_bytes(payload))
    return app(environ, start_response)


def wsgi(environ, start_response):
    path = environ.get("PATH_INFO") or "/"
    path = path.split("?", 1)[0]
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    if path == "/health":
        # Deploy probe: truthful. A broken deploy must fail Railway's
        # healthcheck instead of going live.
        return _health_probe(start_response)

    if path in _LIVENESS:
        # /api/health and /healthz are NOT the deploy probe; keep the
        # instant-200 behavior.
        return _respond(start_response, "200 OK", _HEALTH)

    if path == "/boot":
        return _respond(start_response, "200 OK", _json_bytes(boot_status()))

    start_background_load()

    if path == "/ready":
        if _full is not None:
            return _respond(start_response, "200 OK", _json_bytes(boot_status()))
        return _respond(start_response, "503 Service Unavailable", _json_bytes(boot_status()))

    if _full is None:
        _ready.wait(_REQUEST_WAIT_SECONDS)

    if _full is not None:
        try:
            return _call_full(environ, start_response)
        except Exception as exc:
            logger.exception("[boot] mvp_app WSGI call failed: %s", _safe_error(exc))
            payload = boot_status()
            payload["error"] = _safe_error(exc)
            return _respond(start_response, "500 Internal Server Error", _json_bytes(payload))

    payload = boot_status()
    payload["error"] = payload.get("error") or "mvp_app still loading"
    return _respond(start_response, "503 Service Unavailable", _json_bytes(payload))


app = wsgi


def _install_unhandled_hook() -> None:
    def _hook(exc_type, exc, tb):
        logger.critical("[boot] unhandled exception in worker", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook


_install_unhandled_hook()
