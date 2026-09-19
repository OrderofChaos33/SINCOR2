"""Public HTML pages.

Extracted from mvp_app so the gunicorn entry stays the app factory.
Helpers and Flask `app` live in sincor2.mvp_app; this module binds them
after that module has finished constructing the application object.
"""
from __future__ import annotations

from flask import Blueprint

bp = Blueprint("mvp_pages", __name__)


def _bind_mvp():
    import sincor2.mvp_app as mvp
    g = globals()
    for key, value in vars(mvp).items():
        if key.startswith("__") or key in {"bp", "_bind_mvp"}:
            continue
        g[key] = value


_bind_mvp()

GENESIS_COOKIE = "sincor_genesis"
GENESIS_LAUNCH_AT = "2026-09-26T16:00:00.000Z"


@bp.route("/")
def index():
    """Public landing. Must live on mvp_app's blueprint — sincor2.app is not the Railway entry."""
    return render_template("home.html")
