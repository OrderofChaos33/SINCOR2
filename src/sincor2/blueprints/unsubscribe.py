"""Unsubscribe routes (Wren PR 8).

Public by design — the HMAC-signed token IS the credential. A valid
token suppresses the address it names in sincor2.comms_compliance and
every outreach send path then refuses it. Anything else — malformed,
tampered, unsigned — answers 404: fail closed, no validity oracle.

GET never mutates state: mail scanners prefetch links, so a GET only
renders a confirmation page whose button POSTs back to the same URL.
The POST is the action — and it is also the RFC 8058 one-click path
(mail clients POST the List-Unsubscribe URL directly), so one-click
keeps working exactly as mail clients expect.
"""

from __future__ import annotations

import logging

from flask import Blueprint, abort, request

from sincor2 import comms_compliance

logger = logging.getLogger("sincor2.unsubscribe")

unsubscribe_bp = Blueprint("unsubscribe", __name__)

_PAGE_STYLE = "font-family:sans-serif;max-width:600px;margin:40px auto;"


@unsubscribe_bp.route("/unsubscribe/<token>", methods=["GET", "POST"])
def unsubscribe(token: str):
    email = comms_compliance.verify_unsubscribe_token(token)
    if not email:
        abort(404)
    if request.method == "GET":
        # Confirmation page only — a scanner's prefetch must not
        # unsubscribe anyone. The form POSTs to this same URL.
        return (
            f"<html><body style=\"{_PAGE_STYLE}\">"
            "<h1>Unsubscribe from SINCOR outreach?</h1>"
            "<p>Click the button below to stop outreach email to "
            "this address.</p>"
            "<form method=\"post\" action=\"\">"
            "<button type=\"submit\" style=\"font-size:1rem;"
            "padding:0.6em 1.2em;\">Unsubscribe</button>"
            "</form>"
            "</body></html>",
            200,
            {"Content-Type": "text/html"},
        )
    comms_compliance.suppress(email, reason="unsubscribe")
    logger.info("[COMMS] Unsubscribe honored for %s", email)
    return (
        f"<html><body style=\"{_PAGE_STYLE}\">"
        "<h1>You're unsubscribed</h1>"
        "<p>You will no longer receive SINCOR outreach email at this address.</p>"
        "</body></html>",
        200,
        {"Content-Type": "text/html"},
    )
