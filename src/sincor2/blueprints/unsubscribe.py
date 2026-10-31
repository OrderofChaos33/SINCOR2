"""One-click unsubscribe routes (Wren PR 8).

Public by design — the HMAC-signed token IS the credential (RFC 8058
one-click: mail clients POST the List-Unsubscribe URL directly). A valid
token suppresses the address it names in sincor2.comms_compliance and
every outreach send path then refuses it. Anything else — malformed,
tampered, unsigned — answers 404: fail closed, no validity oracle.
"""

from __future__ import annotations

import logging

from flask import Blueprint, abort

from sincor2 import comms_compliance

logger = logging.getLogger("sincor2.unsubscribe")

unsubscribe_bp = Blueprint("unsubscribe", __name__)


@unsubscribe_bp.route("/unsubscribe/<token>", methods=["GET", "POST"])
def unsubscribe(token: str):
    email = comms_compliance.verify_unsubscribe_token(token)
    if not email:
        abort(404)
    comms_compliance.suppress(email, reason="unsubscribe")
    logger.info("[COMMS] Unsubscribe honored for %s", email)
    return (
        "<html><body style=\"font-family:sans-serif;max-width:600px;margin:40px auto;\">"
        "<h1>You're unsubscribed</h1>"
        "<p>You will no longer receive SINCOR outreach email at this address.</p>"
        "</body></html>",
        200,
        {"Content-Type": "text/html"},
    )
