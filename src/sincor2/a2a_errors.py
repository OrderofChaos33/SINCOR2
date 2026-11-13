"""JSON error envelopes for the A2A blueprints (gap G2.11).

Every A2A blueprint registers these handlers at mount time so an error is
always a JSON envelope — never an HTML error page::

    {"error": "<message>", "status": <code>}

This matches the ``_http_error`` envelope already used by the inbound
routes, so clients see one shape whether the error was raised
deliberately or escaped a view function.

Unhandled exceptions become a 500 envelope with a fixed generic message.
The traceback is logged server-side (``logger.exception``) and NEVER
serialized into the response — no stack traces, no internals, and no
credential values ever reach the wire. The presented admin credential is
compared with ``hmac.compare_digest`` and is never logged.

Also provides :func:`parse_int_param` for numeric query params: garbage
input raises ``ValueError`` with a 400-safe message instead of bubbling
into a 500.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from flask import Blueprint, jsonify
from werkzeug.exceptions import HTTPException

logger = logging.getLogger("sincor.a2a.errors")


def error_envelope(message: str, status: int, **extra: Any):
    """Build the canonical A2A JSON error envelope.

    Same ``{"error", "status"}`` shape as ``_http_error`` in
    ``a2a_inbound``; ``extra`` keys are merged in for structured detail
    (e.g. ``release_failures``) — never for secrets or tracebacks.
    """
    body = {"error": message, "status": status}
    body.update(extra)
    return jsonify(body), status


def parse_int_param(
    raw: Any,
    name: str,
    default: Optional[int] = None,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    """Parse a numeric query/body param into an int.

    Raises ``ValueError`` with a client-safe message on garbage input —
    callers convert that to a 400 (REST) or -32602 (JSON-RPC) response
    instead of letting it escape as a 500.
    """
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        if default is None:
            raise ValueError(f"{name} is required")
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return value


def register_a2a_error_handlers(bp: Blueprint) -> None:
    """Register JSON error handlers on an A2A blueprint.

    * ``HTTPException`` (400/401/403/404/405/409/429/503 raised via
      ``abort()`` or by the framework inside a blueprint view) becomes a
      JSON envelope carrying the exception's code.
    * Any other exception becomes a 500 JSON envelope with a fixed
      generic message. Details go to the server log only.

    Blueprint-scoped (not app-wide): public non-A2A surfaces keep their
    own handlers. Routing-level 404s (no blueprint view matched) are
    still served by the app-level handler.
    """
    @bp.errorhandler(HTTPException)
    def _a2a_http_error(err: HTTPException):  # noqa: D103
        code = err.code or 500
        description = (
            err.description if isinstance(err.description, str) else err.name
        )
        return error_envelope(description, code)

    @bp.errorhandler(Exception)
    def _a2a_unexpected_error(err: Exception):  # noqa: D103,BLE001
        # Intentional catch-all: this is the last line of defense before
        # Flask's HTML 500 page. Log everything, leak nothing.
        logger.exception("unhandled A2A error in blueprint %s", bp.name)
        return error_envelope("internal server error", 500)
