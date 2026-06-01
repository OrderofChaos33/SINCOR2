from __future__ import annotations

import logging

from flask import Flask

from .settings import Settings


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s",
    )


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return True



def run_startup_initializers(app: Flask, settings: Settings) -> None:
    configure_logging()
    logging.getLogger().addFilter(_RequestIdFilter())
    app.logger.info("Startup complete", extra={"request_id": "startup"})
    app.config["SINCOR_SETTINGS"] = settings
