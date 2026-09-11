"""Standalone CHROMA shop app — gunicorn sincor2.chroma_app:app

Also used by the Grok preview. Mounts at /chroma (and / for convenience).
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from flask import Flask, redirect

_REPO = Path(__file__).resolve().parents[2]


def _session_secret() -> str:
    """Never use a shared hard-coded fallback — that makes sessions forgeable."""
    key = (os.environ.get("SECRET_KEY") or "").strip()
    if key:
        return key
    env = (os.environ.get("FLASK_ENV") or os.environ.get("ENVIRONMENT") or "").lower()
    if env in {"production", "prod"}:
        raise RuntimeError("SECRET_KEY must be set in production")
    return secrets.token_hex(32)


def create_chroma_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(_REPO / "templates"),
        static_folder=str(_REPO / "static"),
    )
    app.config["SECRET_KEY"] = _session_secret()
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    from verticals.auto_detailing.blueprint import register_chroma

    register_chroma(app)

    @app.route("/")
    def root():
        return redirect("/chroma/")

    @app.route("/health")
    def root_health():
        from verticals.auto_detailing.pipeline import health_payload

        from flask import jsonify

        return jsonify(health_payload()), 200

    return app


app = create_chroma_app()
