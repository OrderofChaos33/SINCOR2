"""Standalone CHROMA shop app — gunicorn sincor2.chroma_app:app

Also used by the Grok preview. Mounts at /chroma (and / for convenience).
"""

from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, redirect

_REPO = Path(__file__).resolve().parents[2]


def create_chroma_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(_REPO / "templates"),
        static_folder=str(_REPO / "static"),
    )
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or "chroma-demo-secret-change-me"
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
