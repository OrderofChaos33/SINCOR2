from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from flask import Flask, Response, jsonify, request

logger = logging.getLogger(__name__)


@dataclass
class ApiError(Exception):
    code: str
    message: str
    status: int = 400
    details: dict[str, Any] | None = None



def api_error(
    code: str,
    message: str,
    status: int = 400,
    details: dict[str, Any] | None = None,
) -> tuple[Response, int]:
    """Return a normalized API error payload with correlation metadata."""
    payload = {
        "status": "error",
        "code": code,
        "message": message,
        "details": details or {},
        "request_id": getattr(request, "request_id", None),
    }
    return jsonify(payload), status



def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError):
        return api_error(error.code, error.message, error.status, error.details)

    @app.errorhandler(404)
    def handle_not_found(_):
        return api_error("not_found", "Resource not found", status=404)

    @app.errorhandler(500)
    def handle_internal_error(error):
        logger.exception("Unhandled server error: %s", error)
        return api_error("internal_error", "Internal server error", status=500)
