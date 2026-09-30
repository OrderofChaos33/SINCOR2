from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from flask import Flask, g, has_request_context

from .settings import Settings

_LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s "
    "request_id=%(request_id)s correlation_id=%(correlation_id)s %(message)s"
)
_REQUEST_ID_FACTORY_INSTALLED = False
_BOOT_RUN_ID = f"boot-{uuid.uuid4().hex[:12]}"


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

    def _boot_log(phase: str, outcome: str, detail: str) -> None:
        app.logger.info(
            "startup_event phase=%s outcome=%s run_id=%s ts=%s detail=%s",
            phase,
            outcome,
            _BOOT_RUN_ID,
            datetime.now(timezone.utc).isoformat(),
            detail,
        )

    _boot_log("startup", "begin", "initializing runtime settings")
    app.config["SINCOR_SETTINGS"] = settings
    _boot_log("startup", "ok", "settings bound to flask app")
    try:
        from sincor2.onchain.probe import validate_at_startup

        report = validate_at_startup(rpc_url=settings.base_rpc_url)
        app.config["SINCOR_ONCHAIN"] = report.to_dict()
        _boot_log(
            "onchain",
            "ok" if report.ok else "mismatch",
            report.summary(),
        )
    except Exception as exc:  # noqa: BLE001 — never block boot on RPC
        _boot_log("onchain", "error", str(exc))
    if not settings.stripe_secret_key:
        _boot_log("stripe", "disabled", "STRIPE_SECRET_KEY not configured")
    else:
        _boot_log("stripe", "enabled", "STRIPE_SECRET_KEY configured")
    if not settings.anthropic_api_key:
        _boot_log("anthropic", "disabled", "ANTHROPIC_API_KEY not configured")
    else:
        _boot_log("anthropic", "enabled", "ANTHROPIC_API_KEY configured")
    if not settings.base_rpc_url:
        _boot_log("base_rpc", "disabled", "BASE_RPC_URL not configured")
    else:
        _boot_log("base_rpc", "enabled", "BASE_RPC_URL configured")
    _boot_log("startup", "complete", "runtime startup initializers complete")
