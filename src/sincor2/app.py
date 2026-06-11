"""Application factory and runtime wiring for SINCOR2."""

from __future__ import annotations

import logging
import os
import time
from uuid import uuid4

from dotenv import load_dotenv
from flask import Flask, g, request

from sincor2 import waitlist_system
from sincor2.a2a_integration import A2ARouter
from sincor2.auth_system import SINCORAuth
from sincor2.blueprints.auth import auth_bp
from sincor2.blueprints.marketplace import marketplace_bp
from sincor2.blueprints.monitoring import monitoring_bp
from sincor2.blueprints.pages import pages_bp
from sincor2.blueprints.payments import payments_bp
from sincor2.blueprints.sinc import sinc_bp
from sincor2.blueprints.waitlist import waitlist_bp
from sincor2.error_handling import register_error_handlers
from sincor2.platform_bootstrap import bootstrap_platform
from sincor2.settings import Settings
from sincor2.sinc_access import SINCAccessManager, SINCMeter
from sincor2.startup import run_startup_initializers
from sincor2.stripe_checkout import StripeCheckout

load_dotenv()
logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))


def _attach_request_context(app: Flask) -> None:
    @app.before_request
    def set_request_id() -> None:
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        correlation_id = request.headers.get("X-Correlation-ID") or request_id
        g.request_id = request_id
        g.correlation_id = correlation_id
        g.request_started_at = time.perf_counter()
        request.request_id = request_id  # type: ignore[attr-defined]
        request.correlation_id = correlation_id  # type: ignore[attr-defined]

    @app.after_request
    def add_operational_headers(response):
        started_at = getattr(g, "request_started_at", time.perf_counter())
        duration_ms = (time.perf_counter() - started_at) * 1000
        response.headers["X-Request-ID"] = getattr(g, "request_id", "")
        response.headers["X-Correlation-ID"] = getattr(g, "correlation_id", "")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
        app.logger.info(
            "request_complete method=%s path=%s status=%s duration_ms=%.2f",
            request.method,
            request.path,
            response.status_code,
            duration_ms,
        )
        return response


def create_app() -> Flask:
    settings = Settings.from_env()
    app = Flask(
        __name__,
        template_folder=os.path.join(_ROOT, "templates"),
        static_folder=os.path.join(_ROOT, "static"),
    )

    app.config["SECRET_KEY"] = settings.secret_key
    app.config["JWT_SECRET_KEY"] = settings.jwt_secret_key
    app.config["ADMIN_USERNAME"] = settings.admin_username
    app.config["ADMIN_PASSWORD"] = settings.admin_password
    app.config["ENVIRONMENT"] = settings.environment
    app.config["DEBUG"] = settings.debug

    run_startup_initializers(app, settings)
    _attach_request_context(app)

    sincor_auth = SINCORAuth(app)
    app.extensions["sincor_auth"] = sincor_auth

    # SINC token access management
    sinc_manager = SINCAccessManager(rpc_url=settings.base_rpc_url)
    app.extensions["sinc_access"] = sinc_manager
    app.extensions["sinc_meter"] = SINCMeter()

    if settings.stripe_secret_key:
        try:
            app.extensions["stripe_checkout"] = StripeCheckout(api_key=settings.stripe_secret_key)
        except Exception as exc:  # pragma: no cover
            logger.warning("Stripe initialization failed: %s", exc)

    app.extensions["waitlist_manager"] = waitlist_system.waitlist_manager

    bootstrap_platform(app)

    app.register_blueprint(pages_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(sinc_bp)
    app.register_blueprint(waitlist_bp)
    app.register_blueprint(monitoring_bp)
    app.register_blueprint(marketplace_bp)

    app.register_blueprint(A2ARouter().blueprint)

    register_error_handlers(app)
    return app


app = create_app()
