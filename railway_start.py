"""Railway / Gunicorn entry. Zero SINCOR imports at module load."""
from __future__ import annotations

import json
import sys
import traceback
from time import monotonic

_LIVENESS = {"/health", "/api/health", "/healthz"}
_HEALTH = b'{"status":"healthy","service":"SINCOR2 MVP","entry":"railway_start"}'
_full = None
_full_error = None
_full_error_type = None
_next_retry = 0.0
_RETRY_S = 15.0


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _respond(start_response, status: str, body: bytes):
    start_response(
        status,
        [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ],
    )
    return [body]


def _try_load() -> None:
    global _full, _full_error, _full_error_type, _next_retry
    if _full is not None:
        return
    now = monotonic()
    if _full_error is not None and now < _next_retry:
        return
    try:
        from sincor2.mvp_app import app as full

        _full = full
        _full_error = None
        _full_error_type = None
        print("[railway_start] mvp_app loaded", file=sys.stderr, flush=True)
    except Exception as exc:
        _full = None
        _full_error_type = type(exc).__name__
        _full_error = str(exc)[:500]
        _next_retry = now + _RETRY_S
        print("[railway_start] mvp_app import failed:", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)


def wsgi(environ, start_response):
    path = environ.get("PATH_INFO") or "/"
    path = path.split("?", 1)[0]
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    if path in _LIVENESS:
        return _respond(start_response, "200 OK", _HEALTH)

    _try_load()

    if path == "/__boot":
        body = _json_bytes(
            {
                "entry": "railway_start",
                "mvp_app": _full is not None,
                "error_type": _full_error_type,
                "error": _full_error,
            }
        )
        status = "200 OK" if _full is not None else "503 Service Unavailable"
        return _respond(start_response, status, body)

    if _full is not None:
        return _full(environ, start_response)

    body = _json_bytes(
        {
            "status": "degraded",
            "service": "SINCOR2 MVP",
            "error": "mvp_app import failed",
            "error_type": _full_error_type,
            "detail": _full_error,
        }
    )
    return _respond(start_response, "503 Service Unavailable", body)


app = wsgi
