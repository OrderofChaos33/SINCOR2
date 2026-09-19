"""Railway / Gunicorn entry.

Gunicorn imports this module, then binds PORT. /health is served from this
file with the stdlib only so Railway's probe can pass while mvp_app loads.

The previous shim imported mvp_app on the first real request inside a sync
worker, cached any failure forever, and kept advertising healthy. That left
getsincor.com serving JSON stubs for /, /buy, and every other path while
Railway kept billing the replica.

Load happens in a background thread (post_worker_init + first request).
Failures retry. /ready tells the truth. /boot shows the exception.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time

logger = logging.getLogger("sincor2.railway_start")
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

_LIVENESS = frozenset({"/health", "/api/health", "/healthz"})
_HEALTH = b'{"status":"healthy","service":"SINCOR2 MVP","entry":"railway_start"}'

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


def start_background_load() -> None:
    """Idempotent. Called from gunicorn post_worker_init and on first request."""
    global _thread_started
    with _lock:
        if _thread_started:
            return
        _thread_started = True
        thread = threading.Thread(target=_load_loop, name="sincor2-boot", daemon=True)
        thread.start()


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

    if path in _LIVENESS:
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
