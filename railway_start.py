"""Railway / Gunicorn entry. Zero SINCOR imports at module load.

Gunicorn imports this file, then binds PORT. /health is served from this
function with the stdlib only. sincor2.mvp_app is imported on the first
non-liveness request so a crash or OOM during that import cannot make
the replica fail Railway's healthcheck.
"""
from __future__ import annotations

_LIVENESS = {"/health", "/api/health", "/healthz", "/ready"}
_HEALTH = (
    b'{"status":"healthy","service":"SINCOR2 MVP","entry":"railway_start"}'
)
_full = None
_full_error = None


def wsgi(environ, start_response):
    global _full, _full_error
    path = environ.get("PATH_INFO") or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    if path in _LIVENESS:
        start_response(
            "200 OK",
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(_HEALTH))),
                ("Cache-Control", "no-store"),
            ],
        )
        return [_HEALTH]

    if _full is None and _full_error is None:
        try:
            from sincor2.mvp_app import app as full

            _full = full
        except Exception as exc:
            _full_error = str(exc)
            body = (
                b'{"status":"degraded","service":"SINCOR2 MVP","error":"mvp_app import failed"}'
            )
            start_response(
                "200 OK",
                [("Content-Type", "application/json"), ("Content-Length", str(len(body)))],
            )
            return [body]

    if _full is not None:
        return _full(environ, start_response)

    body = b'{"status":"degraded","service":"SINCOR2 MVP","error":"mvp_app unavailable"}'
    start_response(
        "200 OK",
        [("Content-Type", "application/json"), ("Content-Length", str(len(body)))],
    )
    return [body]


# Gunicorn looks up railway_start:app
app = wsgi
