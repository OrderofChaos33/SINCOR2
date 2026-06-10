from __future__ import annotations

import logging

from flask import Flask, g, has_request_context

from .settings import Settings

_LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s "
    "request_id=%(request_id)s correlation_id=%(correlation_id)s %(message)s"
)
_REQUEST_ID_FACTORY_INSTALLED = False


def configure_logging() -> None:
    global _REQUEST_ID_FACTORY_INSTALLED

    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)
    else:
        root_logger.setLevel(logging.INFO)
        formatter = logging.Formatter(_LOG_FORMAT)
        for handler in root_logger.handlers:
            handler.setFormatter(formatter)

    if not _REQUEST_ID_FACTORY_INSTALLED:
        old_factory = logging.getLogRecordFactory()

        def record_factory(*args, **kwargs):
            record = old_factory(*args, **kwargs)
            if not hasattr(record, "request_id"):
                record.request_id = "-"
            if not hasattr(record, "correlation_id"):
                record.correlation_id = "-"
            return record

        logging.setLogRecordFactory(record_factory)
        _REQUEST_ID_FACTORY_INSTALLED = True


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if has_request_context():
            record.request_id = getattr(g, "request_id", getattr(record, "request_id", "-"))
            record.correlation_id = getattr(
                g, "correlation_id", getattr(record, "correlation_id", record.request_id)
            )

        if not hasattr(record, "request_id"):
            record.request_id = "-"
        if not hasattr(record, "correlation_id"):
            record.correlation_id = "-"
        return True



def run_startup_initializers(app: Flask, settings: Settings) -> None:
    """Initialize logging and bind validated runtime settings to the Flask app."""
    configure_logging()
    root_logger = logging.getLogger()
    if not any(isinstance(log_filter, _RequestIdFilter) for log_filter in root_logger.filters):
        root_logger.addFilter(_RequestIdFilter())

    app.logger.info("Startup complete")
    app.config["SINCOR_SETTINGS"] = settings
    if not settings.stripe_secret_key:
        app.logger.info("Stripe integration disabled: STRIPE_SECRET_KEY is not configured.")
    if not settings.anthropic_api_key:
        app.logger.info("Anthropic integration disabled: ANTHROPIC_API_KEY is not configured.")
    if not settings.base_rpc_url:
        app.logger.info("Base RPC integration disabled: BASE_RPC_URL is not configured.")
