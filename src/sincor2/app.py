"""Application factory and runtime wiring for SINCOR2."""

from __future__ import annotations

import logging
from uuid import uuid4

from dotenv import load_dotenv
from flask import Flask, g, request

from sincor2.a2a_integration import A2ARouter
from sincor2.auth_system import SINCORAuth
from sincor2.blueprints.auth import auth_bp
from sincor2.blueprints.monitoring import monitoring_bp
from sincor2.blueprints.pages import pages_bp
from sincor2.blueprints.payments import payments_bp
from sincor2.blueprints.waitlist import waitlist_bp
from sincor2.error_handling import register_error_handlers
from sincor2.settings import Settings
from sincor2.startup import run_startup_initializers
from sincor2.stripe_checkout import StripeCheckout
from sincor2.waitlist_system import waitlist_manager

load_dotenv()
logger = logging.getLogger(__name__)


def _attach_request_context(app: Flask) -> None:
    @app.before_request
    def set_request_id() -> None:
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        g.request_id = request_id
        request.request_id = request_id  # type: ignore[attr-defined]

    @app.after_request
    def add_operational_headers(response):
        response.headers["X-Request-ID"] = getattr(g, "request_id", "")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
        return response


def create_app() -> Flask:
    settings = Settings.from_env()
    app = Flask(__name__, template_folder="../../templates", static_folder="../../static")

    app.config["SECRET_KEY"] = settings.secret_key or "dev-only-secret-key"
    app.config["JWT_SECRET_KEY"] = settings.jwt_secret_key or "dev-only-jwt-key"

    run_startup_initializers(app, settings)
    _attach_request_context(app)

    sincor_auth = SINCORAuth(app)
    app.extensions["sincor_auth"] = sincor_auth

    try:
        app.extensions["stripe_checkout"] = StripeCheckout(api_key=settings.stripe_secret_key)
    except Exception as exc:  # pragma: no cover
        logger.warning("Stripe initialization failed: %s", exc)

    app.extensions["waitlist_manager"] = waitlist_manager

    app.register_blueprint(pages_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(waitlist_bp)
    app.register_blueprint(monitoring_bp)

    app.register_blueprint(A2ARouter().blueprint)

    register_error_handlers(app)
    return app


app = create_app()
