"""Human payment rail page (connect wallet -> pay the x402 challenge in USDC).

Routes (read-only + session-minting; no money movement server-side):

  GET /rail/pay/<resource_id>  -> payment shell (templates/rail_pay.html).
      The page JS mints a challenge via POST /rail/checkout on load (that
      endpoint is built in parallel; its contract is challenge_id + payment
      payload), then settles via POST /rail/settle after the onchain USDC
      transfer confirms.  The shell embeds only the resource id and whether
      the onramp is configured -- amount/merchant always come from the
      server-fetched challenge.

  GET /rail/onramp?amount_usd=&destination_address=
      -> {"ok": True, "url": <Coinbase hosted onramp URL>} or a fail-closed
      error.  ``amount_usd`` only prefills the widget (presetFiatAmount);
      the server never prices anything from it.
"""
from __future__ import annotations

import re

from flask import Blueprint, jsonify, render_template, request

from sincor2.onramp import create_onramp_session, is_configured

rail_pay_bp = Blueprint("rail_pay", __name__)

_RESOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")


@rail_pay_bp.get("/rail/pay/<resource_id>")
def rail_pay_page(resource_id: str):
    if not _RESOURCE_RE.match(resource_id or ""):
        return "Not found", 404
    bootstrap = {
        "resource_id": resource_id,
        "onramp_configured": is_configured(),
    }
    return render_template("rail_pay.html", bootstrap=bootstrap)


@rail_pay_bp.get("/rail/onramp")
def rail_onramp():
    result = create_onramp_session(
        amount_usd=request.args.get("amount_usd"),
        destination_address=request.args.get("destination_address"),
    )
    if result.get("ok"):
        return jsonify(result), 200
    error = result.get("error", "onramp_unavailable")
    if error == "onramp_unconfigured":
        return jsonify(result), 503
    if error.startswith("invalid_"):
        return jsonify(result), 400
    return jsonify(result), 502
