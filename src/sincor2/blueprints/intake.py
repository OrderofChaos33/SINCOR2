"""Token-gated B2B pack intake pages on the x402/USDC rail (Wren PR 1).

Routes (registered by :func:`init_intake_routes`):
  GET  /intake/<intake_id>?token=<intake_token>
    -> the buyer's per-SKU intake form (pack name, status, fields)
  POST /intake/<intake_id>?token=<intake_token>   (JSON or form body)
    -> validate + store the answers, status -> intake_complete,
       owner notified once

Why this exists: every paid B2B pack ($297/$997/$2,997) and WebBuilder
($497) buyer is emailed an /intake/<id> link at fulfillment, but no
route served it — every buyer landed on a 404. The intake id is derived
from the challenge id (intake-rail-<challenge_id>) and is guessable, so
the link carries the unguessable intake token minted at fulfillment and
bound to the intake record (details["intake_token"]) — the same
fail-closed pattern as the digital-pack download: unknown id, missing
token, or wrong token all yield 404, and the 404 body carries nothing
about the record. The token may arrive as the ``token`` query parameter
(the emailed link) or as the ``X-Intake-Token`` header; both go through
the exact same check. No cookies are involved, so the token in the URL
is the credential and the POST is CSRF-safe by construction.

Validation is server-side and per-SKU: an allowlist of required fields
(unknown keys are rejected with 400 naming them), HTML stripped, max
lengths enforced, and the one enum field (sbom_format_needed) checked.
Answers land in details["intake_answers"] via
payment_ledger.update_pack_intake_status (merge semantics — the write
can never wipe the fulfillment details or the token). A second POST
updates the answers only while status < in_progress; the recorded
customer_email and payer_wallet are never touched by a submission. The
owner (LAUNCH_REVIEW_ALERT_EMAIL / SUPPORT_EMAIL) is notified exactly
once — when a submission first moves the record to intake_complete —
and a notification failure never fails the buyer's request. No answer
values (PII) are written to logs.
"""

from __future__ import annotations

import hmac
import html
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, abort, jsonify, render_template, request

logger = logging.getLogger("sincor.rail_intake")

intake_bp = Blueprint("rail_intake", __name__)

# --- Per-SKU intake fields ---------------------------------------------------
# Field names verified against the pack definitions in rail_routes
# (PACK_SKUS) and the directive's field sets. Every pack collects the
# shared four; each SKU adds its own. webbuilder_site's set is the
# shared four plus its five (business_name is the shared one).
_SHARED_FIELDS: Tuple[str, ...] = (
    "business_name", "contact_name", "contact_email", "website_or_none",
)

SKU_FIELDS: Dict[str, Tuple[str, ...]] = {
    "credentialing_launch": _SHARED_FIELDS + (
        "practice_type", "state", "specialties", "current_payer_list"),
    "chroma_compliance": _SHARED_FIELDS + (
        "repo_or_product_url", "sbom_format_needed", "compliance_target"),
    "sadas_execution": _SHARED_FIELDS + (
        "goal", "systems_in_scope", "success_metric", "timeline"),
    "webbuilder_site": _SHARED_FIELDS + (
        "phone", "niche", "territory", "prompt"),
}

FIELD_LABELS: Dict[str, str] = {
    "business_name": "Business name",
    "contact_name": "Contact name",
    "contact_email": "Contact email",
    "website_or_none": "Website (or 'none')",
    "practice_type": "Practice type",
    "state": "State",
    "specialties": "Specialties",
    "current_payer_list": "Current payers",
    "repo_or_product_url": "Repository or product URL",
    "sbom_format_needed": "SBOM format needed",
    "compliance_target": "Compliance target",
    "goal": "Goal",
    "systems_in_scope": "Systems in scope",
    "success_metric": "Success metric",
    "timeline": "Timeline",
    "phone": "Phone",
    "niche": "Niche",
    "territory": "Territory",
    "prompt": "What should the site say and do?",
}

# Enum-constrained fields: value must be one of these, exactly.
FIELD_CHOICES: Dict[str, Tuple[str, ...]] = {
    "sbom_format_needed": ("CycloneDX", "SPDX", "unsure"),
}

MAX_FIELD_LEN = 500
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TAG_RE = re.compile(r"<[^>]*>")
# script/style blocks are removed with their contents — their text is
# code, not prose, and must never survive into a stored answer.
_BLOCK_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)

# Buyer-facing status labels (Wren PR 6 shows the same states on the
# pricing/buy pages; the stored vocabulary lives in payment_ledger).
STATUS_LABELS: Dict[str, str] = {
    "created": "Received",
    "agent_kickoff": "Received",
    "intake_complete": "Intake complete",
    "in_progress": "In progress",
    "delivered": "Delivered",
}

# Rank mirror of payment_ledger._PACK_INTAKE_STATUS_ORDER — used only
# to decide "may the buyer still edit answers" (status < in_progress).
_STATUS_RANK = {
    "created": 0, "agent_kickoff": 1, "intake_complete": 2,
    "in_progress": 3, "delivered": 4,
}
_IN_PROGRESS_RANK = _STATUS_RANK["in_progress"]


def _intake_db_path() -> Path:
    """The rail ledger file holding pack intakes.

    Mirrors rail_routes._rail_db_path: SINCOR_REVENUE_DB wins, else the
    revenue_orchestrator default. Imported lazily so this module stays
    import-light like rail_delivery.
    """
    from sincor2.revenue_orchestrator import REVENUE_DB

    return Path(os.environ.get("SINCOR_REVENUE_DB") or REVENUE_DB)


def _fields_for_sku(pack_sku: str) -> Tuple[str, ...]:
    """Required intake fields for a pack SKU (shared set as fallback)."""
    return SKU_FIELDS.get(pack_sku or "", _SHARED_FIELDS)


def _authorized_intake(intake_id: str, token: str):
    """Resolve + authorize an intake page access, or None (→ 404).

    Fail closed on every miss, identically: unknown id, a DIGITAL
    pack's record (its credential is a download token, never an intake
    token), a record with no intake token bound, or a token mismatch.
    """
    from sincor2.payment_ledger import get_pack_intake

    if not intake_id or not token:
        return None
    try:
        intake = get_pack_intake(_intake_db_path(), intake_id)
    except Exception:
        logger.exception("[INTAKE] intake lookup failed")
        return None
    if intake is None:
        return None
    details = intake.details or {}
    if details.get("digital"):
        return None
    expected = str(details.get("intake_token") or "")
    if not expected:
        return None
    # Compare as BYTES: hmac.compare_digest on str raises TypeError
    # for non-ASCII input, which would 500 on a KNOWN id while an
    # unknown id cleanly 404s — an unauthenticated existence oracle.
    # Byte comparison keeps every token shape on the identical 404
    # path (surrogatepass makes the encode total, and UTF-8 encoding
    # is injective, so equality semantics are unchanged).
    if not hmac.compare_digest(
            expected.encode("utf-8", "surrogatepass"),
            str(token).encode("utf-8", "surrogatepass")):
        return None
    return intake


def _request_token() -> str:
    return request.args.get("token", "") or request.headers.get(
        "X-Intake-Token", "")


def _clean_value(raw: Any) -> Optional[str]:
    """Sanitize one submitted value; None means 'not a usable string'.

    HTML is stripped: entities are unescaped FIRST, script/style
    blocks go entirely (contents included), every other tag is
    removed keeping its text, and the result is trimmed. Unescaping
    before stripping matters: "&lt;script&gt;…" only becomes markup
    after decoding, and decoding after stripping would store that
    markup literally — stored answers never contain markup the
    unescape step introduced.
    """
    if not isinstance(raw, str):
        return None
    value = html.unescape(raw)
    value = _BLOCK_RE.sub("", value)
    value = _TAG_RE.sub("", value)
    return value.strip()


def _validate_answers(pack_sku: str, payload: Dict[str, Any]):
    """Validate a submission against the SKU's allowlist.

    Returns (answers, error_body, error_status). On success error_body
    is None and answers holds exactly the allowlisted, sanitized
    fields. Every rejection names the offending fields. Required-ness
    is judged on the CLEANED value: a field whose submission cleans
    to nothing (pure markup or entities, e.g. "<b></b>") was not
    answered, so it is reported missing, not stored blank.
    """
    fields = _fields_for_sku(pack_sku)
    unknown = sorted(set(payload) - set(fields))
    if unknown:
        return None, {"ok": False, "error": "unknown_fields",
                      "fields": unknown}, 400
    missing: List[str] = []
    invalid: List[str] = []
    answers: Dict[str, str] = {}
    for field in fields:
        raw = payload.get(field)
        value = _clean_value(raw)
        if value is None:
            # Absent/null is a missing answer; a present non-string
            # value is an invalid one.
            (invalid if raw else missing).append(field)
            continue
        if value == "":
            missing.append(field)
            continue
        if len(value) > MAX_FIELD_LEN:
            invalid.append(field)
            continue
        choices = FIELD_CHOICES.get(field)
        if choices and value not in choices:
            invalid.append(field)
            continue
        if field == "contact_email" and not _EMAIL_RE.match(value):
            invalid.append(field)
            continue
        answers[field] = value
    if missing:
        return None, {"ok": False, "error": "missing_required_fields",
                      "fields": missing}, 400
    if invalid:
        return None, {"ok": False, "error": "invalid_fields",
                      "fields": invalid}, 400
    return answers, None, 200


def sanitize_prefill_answers(pack_sku: str,
                             raw_details: Any) -> Dict[str, str]:
    """Sanitize settle-supplied order details into prefill answers.

    The rail settle collects the same per-SKU fields on /rail/pay
    BEFORE payment, and whatever the buyer typed there is stored on
    the intake record as pre-filled answers. A settle body is
    buyer-controlled input, not a trusted internal write, so it goes
    through the exact same allowlist + cleaning as a form POST before
    anything is stored. Partial by design: off-allowlist keys,
    non-strings, values that clean to nothing, over-length values,
    and enum/email failures are DROPPED (never stored, never fatal to
    the settle) — the buyer can still complete the real form.
    """
    if not isinstance(raw_details, dict):
        return {}
    allowed = set(_fields_for_sku(pack_sku))
    answers: Dict[str, str] = {}
    for key, raw in raw_details.items():
        if key not in allowed:
            continue
        value = _clean_value(raw)
        if value is None or value == "" or len(value) > MAX_FIELD_LEN:
            continue
        choices = FIELD_CHOICES.get(key)
        if choices and value not in choices:
            continue
        if key == "contact_email" and not _EMAIL_RE.match(value):
            continue
        answers[key] = value
    return answers


def _notify_owner(intake) -> None:
    """Email the owner that an intake was completed. Best-effort: a
    provider failure is logged (no PII) and swallowed — the buyer's
    submission already succeeded and must not 5xx."""
    alert = (os.environ.get("LAUNCH_REVIEW_ALERT_EMAIL")
             or "court@getsincor.com").strip()
    support = (os.environ.get("SUPPORT_EMAIL")
               or "support@getsincor.com").strip()
    recipients: List[str] = []
    for addr in (alert, support):
        if addr and addr not in recipients:
            recipients.append(addr)
    if not recipients:
        return
    details = intake.details or {}
    pack_name = str(details.get("pack_name") or intake.pack_sku)
    subject = f"SINCOR pack intake completed: {pack_name} ({intake.intake_id})"
    text = (
        f"A buyer completed the intake for the {pack_name} pack.\n\n"
        f"Intake: {intake.intake_id}\n"
        f"SKU: {intake.pack_sku}\n"
        f"Status: intake_complete\n\n"
        "The submitted answers are stored on the intake record in the "
        "payment ledger (details.intake_answers)."
    )
    html_body = "<p>" + html.escape(text).replace("\n", "<br>") + "</p>"
    try:
        from sincor2.email_sender import EmailSender

        sender = EmailSender()
        for addr in recipients:
            sender.send_email(
                to_email=addr, to_name="SINCOR Owner",
                subject=subject, html_content=html_body,
                text_content=text,
                metadata={"type": "pack_intake_complete",
                          "intake_id": intake.intake_id,
                          "pack_sku": intake.pack_sku})
    except Exception:
        logger.exception(
            "[INTAKE] owner notification failed for intake %s "
            "(submission unaffected)", intake.intake_id)


@intake_bp.route("/intake/<intake_id>", methods=["GET"])
def intake_page(intake_id):
    """Render the buyer's intake form. Token-gated, fail closed."""
    token = _request_token()
    intake = _authorized_intake(intake_id, token)
    if intake is None:
        abort(404)
    details = intake.details or {}
    answers = details.get("intake_answers") or {}
    fields = [
        {
            "name": name,
            "label": FIELD_LABELS.get(name, name),
            "value": str(answers.get(name) or ""),
            "choices": FIELD_CHOICES.get(name),
            "locked": _STATUS_RANK.get(intake.status, 0)
                      >= _IN_PROGRESS_RANK,
        }
        for name in _fields_for_sku(intake.pack_sku)
    ]
    return render_template(
        "intake.html",
        intake_id=intake.intake_id,
        token=token,
        pack_name=str(details.get("pack_name") or intake.pack_sku),
        status=intake.status,
        status_label=STATUS_LABELS.get(intake.status, intake.status),
        fields=fields,
        submitted=request.args.get("submitted") == "1",
    )


@intake_bp.route("/intake/<intake_id>", methods=["POST"])
def intake_submit(intake_id):
    """Validate + store a buyer's intake answers.

    Same token auth as the GET (query param or X-Intake-Token header;
    no cookies). Idempotent while status < in_progress: re-submitting
    updates the answers and stays intake_complete. Once work is in
    progress the record is locked (409) and answers no longer change.
    The recorded customer_email / payer_wallet are never written here.
    """
    token = _request_token()
    intake = _authorized_intake(intake_id, token)
    if intake is None:
        abort(404)
    if _STATUS_RANK.get(intake.status, 0) >= _IN_PROGRESS_RANK:
        return jsonify({"ok": False, "error": "intake_locked",
                        "status": intake.status}), 409

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = request.form.to_dict() if request.form else {}
    answers, error_body, error_status = _validate_answers(
        intake.pack_sku, payload)
    if error_body is not None:
        return jsonify(error_body), error_status

    from sincor2.payment_ledger import update_pack_intake_status

    # The owner notification keys off the ledger write's own atomic
    # transition signal — not the status this request happened to
    # read beforehand. Under concurrent first POSTs every request can
    # observe the pre-completion record, but only the one whose write
    # actually advanced the record into intake_complete is told so,
    # and only that one notifies.
    advanced = update_pack_intake_status(
        _intake_db_path(), intake.intake_id, "intake_complete",
        details={"intake_answers": answers})
    logger.info("[INTAKE] intake %s completed (sku=%s)",
                intake.intake_id, intake.pack_sku)
    if advanced:
        _notify_owner(intake)
    return jsonify({"ok": True, "status": "intake_complete"}), 200


def init_intake_routes(app) -> None:
    """Mount the token-gated pack intake blueprint on a Flask app."""
    app.register_blueprint(intake_bp)
    logger.info("[INTAKE] registered /intake/<intake_id> "
                "(token-gated pack intake)")
