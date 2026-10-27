"""Token-gated digital-pack downloads on the x402/USDC rail.

Reworked from the parked Stripe branch's ``stripe_routes.digital_bp``:
the download route lives here now. Stripe files are untouchable — the
global excise-vs-dormant decision for them is the founder's call and out
of scope, so this module never imports ``stripe_routes`` or
``stripe_checkout``.

Routes (registered by :func:`init_rail_delivery_routes`):
  GET /download/<intake_id>?token=<delivery_token>
    -> the purchased digital pack's asset file (token-gated, fail closed)

Token-gated: no auth beyond the token, no network calls. The token may
arrive as the ``token`` query parameter (the emailed link) or as the
``X-Delivery-Token`` header; both go through the exact same
``verify_digital_delivery`` check — same lookup, same semantics, no
parallel weaker path. Fail closed — unknown intake, missing/blank/wrong
token, or an unresolvable asset all yield 404. Serves ONLY what
``verify_digital_delivery`` resolves to: no fallback, no path joining
with user input. The asset read is additionally confined under
money_assets/ (defense in depth against any future allowlist change).
Successful downloads carry ``Cache-Control: no-store`` and a
``Content-Disposition: attachment`` header naming the delivered file.
"""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, Response, abort, request

logger = logging.getLogger("sincor.rail_delivery")

rail_delivery_bp = Blueprint("rail_digital_delivery", __name__)


def _repo_root() -> Path:
    """Repo root holding the money_assets/ pack files.

    NEVER derived from user input — only joined with the asset path that
    verify_digital_delivery allowlisted.
    """
    return Path(__file__).resolve().parent.parent.parent


@rail_delivery_bp.route("/download/<intake_id>", methods=["GET"])
def download_digital_pack(intake_id):
    """Serve a purchased digital pack's asset file.

    Token-gated: the intake id alone is never enough. Fail closed —
    unknown intake, missing/blank/wrong token, or an unresolvable asset
    all yield 404.
    """
    # Lazy import: revenue_orchestrator must not be imported at module
    # level here (keeps this module import-light like the rail routes).
    from sincor2.revenue_orchestrator import verify_digital_delivery

    # The query parameter is the original credential (emailed links);
    # X-Delivery-Token is an alternative carrier for the SAME credential,
    # validated by the same verify_digital_delivery call below.
    token = request.args.get("token", "") or request.headers.get(
        "X-Delivery-Token", "")
    if not token:
        abort(404)  # fail closed: no token, no download
    try:
        asset = verify_digital_delivery(intake_id, token)
    except ValueError:
        abort(404)
    # verify_digital_delivery allowlists asset to one of the known digital
    # pack assets; still confine the read to money_assets/ so a future
    # allowlist change can never walk the path — defense in depth.
    asset_path = (_repo_root() / asset).resolve()
    money_assets = (_repo_root() / "money_assets").resolve()
    if asset_path != money_assets and money_assets not in asset_path.parents:
        abort(404)
    try:
        data = asset_path.read_bytes()
    except OSError:
        abort(404)  # asset unresolvable — no fallback file served
    response = Response(data, content_type="text/markdown; charset=utf-8")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{asset_path.name}"')
    return response


def init_rail_delivery_routes(app) -> None:
    """Mount the token-gated digital-download blueprint on a Flask app."""
    app.register_blueprint(rail_delivery_bp)
    logger.info("[RAIL] registered /download/<intake_id> "
                "(token-gated digital delivery)")
