from __future__ import annotations

import logging

from flask import Flask

from .settings import Settings

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s"


def configure_logging() -> None:
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)
    else:
        root_logger.setLevel(logging.INFO)
        formatter = logging.Formatter(_LOG_FORMAT)
        for handler in root_logger.handlers:
            handler.setFormatter(formatter)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Our formatter requires request_id for every record; set a fallback for
        # startup/background logs that are emitted outside request context.
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return True



def run_startup_initializers(app: Flask, settings: Settings) -> None:
    """Initialize logging and bind validated runtime settings to the Flask app."""
    configure_logging()
    root_logger = logging.getLogger()
    if not any(isinstance(log_filter, _RequestIdFilter) for log_filter in root_logger.filters):
        root_logger.addFilter(_RequestIdFilter())

    app.logger.info("Startup complete", extra={"request_id": "startup"})
    app.config["SINCOR_SETTINGS"] = settings
    if not settings.stripe_secret_key:
        app.logger.info("Stripe integration disabled: STRIPE_SECRET_KEY is not configured.")
    if not settings.anthropic_api_key:
        app.logger.info("Anthropic integration disabled: ANTHROPIC_API_KEY is not configured.")
    if not settings.base_rpc_url:
        app.logger.info("Base RPC integration disabled: BASE_RPC_URL is not configured.")
