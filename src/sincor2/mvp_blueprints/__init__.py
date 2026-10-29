"""Production route blueprints extracted from mvp_app."""
from __future__ import annotations

from flask import Flask


# NOTE (machine surfaces, 2026-10-28): there used to be a before_request
# hook here that redirected browser-Accept GETs away from machine URLs
# (/.well-known/agent-card.json -> /agent-card, /api/a2a/quote?skill_id=
# toa-decision -> /toa, /docs/a2a -> /register-agent). It was removed on
# purpose: those URLs are machine surfaces whose audience is agents and
# integrators, so they must serve JSON regardless of Accept headers.
# The human explainers live at /agent-card, /toa and /register-agent and
# link TO the machine URLs; the machine URLs never bounce back to HTML.
# Pinned by tests/pytest/test_machine_surfaces.py.


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
    from sincor2.mvp_blueprints.discovery import bp as discovery_bp

    app.register_blueprint(health_alias_bp)
    app.register_blueprint(discovery_bp)

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

    try:
        # Consolidation: /api/metrics/treasury previously lived only on the
        # legacy sincor2.app. Attach the maintained view from the monitoring
        # blueprint (without its /health route, which mvp_app already serves).
        from sincor2.blueprints.monitoring import treasury_metrics

        app.add_url_rule(
            "/api/metrics/treasury", view_func=treasury_metrics, methods=["GET"]
        )
    except Exception as exc:  # pragma: no cover
        print(f"Treasury metrics route not available: {exc}")
