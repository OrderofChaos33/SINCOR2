"""Production route blueprints extracted from mvp_app."""
from __future__ import annotations

from flask import Flask


def register_mvp_blueprints(app: Flask) -> None:
    from sincor2.mvp_blueprints.health import bp as health_bp
    from sincor2.mvp_blueprints.ops import bp as ops_bp
    from sincor2.mvp_blueprints.auth import bp as auth_bp
    from sincor2.mvp_blueprints.billing import bp as billing_bp
    from sincor2.mvp_blueprints.pages import bp as pages_bp
    from sincor2.mvp_blueprints.webbuilder import bp as webbuilder_bp
    from sincor2.mvp_blueprints.sinc import bp as sinc_bp
    from sincor2.mvp_blueprints.launch import bp as launch_bp
    from sincor2.mvp_blueprints.admin import bp as admin_bp

    for bp in (
        health_bp,
        ops_bp,
        auth_bp,
        billing_bp,
        pages_bp,
        webbuilder_bp,
        sinc_bp,
        launch_bp,
        admin_bp,
    ):
        app.register_blueprint(bp)
