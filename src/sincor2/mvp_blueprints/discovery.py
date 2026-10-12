"""Agent-presence discovery files: /llms.txt, /ai.txt, /robots.txt, /.well-known/security.txt.

Machine-readable agent-discoverability surface for outside AI agents.
Content lives in static/ (text files) so it is editable without code
changes; these routes serve them at their canonical root paths with
correct Content-Type headers.
"""
from __future__ import annotations

import os

from flask import Blueprint, Response, current_app

bp = Blueprint("discovery", __name__)


def _serve_static_text(relpath: str, mimetype: str) -> Response:
    path = os.path.join(current_app.static_folder, relpath)
    with open(path, "r", encoding="utf-8") as fh:
        body = fh.read()
    return Response(body, mimetype=mimetype)


@bp.route("/llms.txt", methods=["GET"])
def llms_txt():
    return _serve_static_text("llms.txt", "text/plain; charset=utf-8")


@bp.route("/ai.txt", methods=["GET"])
def ai_txt():
    return _serve_static_text("ai.txt", "text/plain; charset=utf-8")


@bp.route("/robots.txt", methods=["GET"])
def robots_txt():
    return _serve_static_text("robots.txt", "text/plain; charset=utf-8")


@bp.route("/.well-known/security.txt", methods=["GET"])
def security_txt():
    return _serve_static_text(
        ".well-known/security.txt", "text/plain; charset=utf-8")
