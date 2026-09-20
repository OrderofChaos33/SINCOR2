"""Production route blueprints extracted from mvp_app."""
from __future__ import annotations

from flask import Flask, redirect, request


def _browser_wants_html() -> bool:
    if request.args.get("format") == "json" or request.args.get("raw") == "1":
        return False
    best = request.accept_mimetypes.best_match(["application/json", "text/html"])
    return best == "text/html"


def _install_human_a2a_redirects(app: Flask) -> None:
    """Homepage one-click cards point at machine JSON. Send browsers to HTML."""

    @app.before_request
    def _humanize_a2a_clicks():
        if request.method != "GET" or not _browser_wants_html():
            return None
        path = request.path.rstrip("/") or "/"
        if path == "/docs/a2a":
            return redirect("/register-agent")
        if path == "/.well-known/agent-card.json":
            return redirect("/agent-card")
        if path == "/api/a2a/quote" and request.args.get("skill_id") == "toa-decision":
            return redirect("/toa")
        return None


def register_mvp_blueprints(app: Flask) -> None:
    from sincor2.mvp_blueprints.health_aliases import bp as health_alias_bp
    from sincor2.mvp_blueprints.health import bp as health_bp
    from sincor2.mvp_blueprints.ops import bp as ops_bp
    from sincor2.mvp_blueprints.auth import bp as auth_bp
    from sincor2.mvp_blueprints.billing import bp as billing_bp
    from sincor2.mvp_blueprints.pages import bp as pages_bp
    from sincor2.mvp_blueprints.webbuilder import bp as webbuilder_bp
    from sincor2.mvp_blueprints.sinc import bp as sinc_bp
    from sincor2.mvp_blueprints.launch import bp as launch_bp
    from sincor2.mvp_blueprints.admin import bp as admin_bp

    app.register_blueprint(health_alias_bp)

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

    try:
        from sincor2.mvp_blueprints.wardrobe import bp as wardrobe_bp

        app.register_blueprint(wardrobe_bp)
    except Exception as exc:  # pragma: no cover — checkout must still boot
        print(f"Wardrobe blueprint not available: {exc}")

    try:
        from sincor2.mvp_blueprints.freshness import bp as freshness_bp

        app.register_blueprint(freshness_bp)
    except Exception as exc:  # pragma: no cover
        print(f"Freshness blueprint not available: {exc}")

    try:
        from verticals.auto_detailing.blueprint import register_chroma

        register_chroma(app)
    except Exception as exc:  # pragma: no cover - platform still boots without CHROMA
        print(f"CHROMA blueprint not available: {exc}")

    try:
        from sincor2.underwriting.blueprint import mount_underwriting

        mount_underwriting(app)
    except Exception as exc:  # pragma: no cover
        print(f"Underwriting blueprint not available: {exc}")

    _install_human_a2a_redirects(app)
