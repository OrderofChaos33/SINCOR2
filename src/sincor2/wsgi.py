"""Gunicorn entry that answers /health before mvp_app is imported.

Railway healthchecks hit a new replica that has not accepted traffic yet.
Importing sincor2.mvp_app pulls JWT, limiter, A2A, blueprints, and optional
Celery. If that import raises or blocks, the master never binds PORT and
Railway reports "service unavailable" for the whole retry window.

This module constructs a WSGI callable immediately. Liveness paths are
served from a tiny Flask app. The full app is loaded in a background
thread and used for every other path.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from flask import Flask, jsonify

log = logging.getLogger("sincor.wsgi")

_LIVENESS_PATHS = {"/health", "/api/health", "/healthz"}


def _liveness_app() -> Flask:
    app = Flask("sincor_liveness")

    @app.get("/health")
    @app.get("/api/health")
    @app.get("/healthz")
    def health():
        state = getattr(app, "full_app_state", "loading")
        err = getattr(app, "full_app_error", "")
        body = {
            "status": "healthy",
            "service": "SINCOR2 MVP",
            "boot": state,
        }
        if err:
            body["boot_error"] = err
        return jsonify(body), 200

    app.full_app_state = "loading"
    app.full_app_error = ""
    return app


class DeferredApp:
    def __init__(self) -> None:
        self.liveness = _liveness_app()
        self._full: Any = None
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._load_full, name="sincor-mvp-import", daemon=True)
        self._thread.start()

    def _load_full(self) -> None:
        try:
            from sincor2.mvp_app import app as full

            self._full = full
            self.liveness.full_app_state = "ready"
            log.info("mvp_app imported")
        except Exception as exc:
            self.liveness.full_app_state = "failed"
            self.liveness.full_app_error = str(exc)
            log.exception("mvp_app import failed: %s", exc)

    def __call__(self, environ: dict, start_response: Callable) -> Any:
        path = environ.get("PATH_INFO") or ""
        if path.rstrip("/") in _LIVENESS_PATHS or path in _LIVENESS_PATHS:
            return self.liveness(environ, start_response)
        full = self._full
        if full is None:
            return self.liveness(environ, start_response)
        return full(environ, start_response)


app = DeferredApp()
