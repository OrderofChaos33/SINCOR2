"""x402/USDC rail for B2B skill-pack fulfillment and plan subscriptions
(zero-to-revenue rail rework).

Replaces the Stripe checkout/webhook path of the parked branches
``xioix/z2r-w1-pack-fulfillment`` and ``xioix/z2r2-w3-subscriptions``
with the x402 rail: a pack or a subscription period is bought via a
402 challenge paying USDC on Base into the treasury; the on-chain
transfer is verified, the payment is recorded, the treasury kill-switch
money gate is consulted (fail closed), and only then is the pack
fulfilled (intake record + buyer email + vertical-agent deployment job)
or the subscription period extended (wallet <-> plan binding in
rail_subscriptions.py).

Routes (registered by :func:`init_rail_routes`):
  POST /rail/checkout  {pack_sku, payer_wallet?, buyer_email?}
                       or {subscription_plan, payer_wallet}
    -> 402 with the x402 challenge (challenge_id, payment payload, bind info)
  POST /rail/settle    {challenge_id, tx_hash, payer_wallet?, buyer_email?,
                        buyer_name?, business_name?, phone?, niche?,
                        territory?, prompt?, replay_signature?, replay_ts_ms?}    -> verify onchain USDC transfer -> finalize payment -> money gate ->
       fulfill pack / extend subscription. Idempotent: re-settling an
       already-fulfilled challenge returns the existing result without
       double-provisioning or double-extending.

STRIPE BOUNDARY: this module never touches ``stripe_routes.py`` or
``stripe_checkout.py``. The global excise-vs-dormant decision for the
Stripe files is the founder's call and is out of scope.

FEE NOTE: ``finalize_challenge_payment`` records the payment and the
treasury inflow but books NO automatic 5% fee split (known gap; founder
decision owed). This module does not invent a fee split.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request

logger = logging.getLogger("sincor.rail")

# --- B2B skill packs (ported from xioix/z2r-w1-pack-fulfillment) ------------
# SKU -> vertical module + price. Prices MUST match config/x402_pricing.yaml
# (rail-pack-<sku> resources, amount_usdc == price_cents / 100); the checkout
# route refuses to mint a challenge when the two disagree (fail closed on
# price config drift). The price ALWAYS comes from this server-side table;
# a client-supplied price is never trusted.
PACK_SKUS: Dict[str, Dict[str, Any]] = {
    "credentialing_launch": {
        "name": "Credentialing Launch",
        "module": "verticals/healthcare",
        "price_cents": 29700,
    },
    "chroma_compliance": {
        "name": "Compliance Operations",
        "module": "verticals/compliance",
        "skill": "compliance-sbom",
        "price_cents": 99700,
    },
    "sadas_execution": {
        "name": "SADAS Execution",
        "module": "sadas_orchestrator.py",
        "price_cents": 299700,
    },
    # Zero-to-revenue 4.2: WebBuilder site-builder SKU. Price $497 (49700c)
    # was decided by TOA decision z2r-webbuilder-price-20261023 (confidence
    # 0.72) — do not change it here; the checkout route takes the price from
    # this table, never from the client.
    "webbuilder_site": {
        "name": "WebBuilder Site",
        "module": "webbuilder_studio.py",
        "price_cents": 49700,
    },
}

# Revenue stream label used for pack purchases.
PACK_REVENUE_STREAM = "B2B_PACK"

# --- Digital packs (reworked from the parked Stripe branch) ------------------
# Digital SKUs are pack SKUs too: they ride the same /rail/checkout,
# /rail/settle, event-claim idempotency, and money-gate machinery as the
# B2B packs. Only fulfillment differs: a token-gated download link
# (issue_delivery_token, bound to the intake record) instead of a
# vertical-agent deployment job. The canonical table lives in
# revenue_orchestrator next to verify_digital_delivery's asset allowlist
# so the two can never drift; the rail merges it here at import time.
from sincor2.revenue_orchestrator import DIGITAL_SKUS as _DIGITAL_SKUS

PACK_SKUS.update(_DIGITAL_SKUS)

# x402 resource naming: each pack SKU is registered as rail-pack-<sku> in
# config/x402_pricing.yaml with amount_usdc.
RAIL_RESOURCE_PREFIX = "rail-pack-"

# Celery queue carrying the vertical-agent deployment jobs.
PACK_AGENT_QUEUE = "sincor.long"

# Fulfillment statuses that mean "already done" — a re-settle for one of
# these is acknowledged without re-fulfilling.
_FULFILLED_STATUSES = ("delivered", "fulfilled", "active")

# Repo-root templates (read-only reference for email rendering).
_TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates"

_WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_TX_HASH_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Celery: vertical-agent deployment jobs ---------------------------------
try:
    from sincor2.celery_app import celery as _celery_app
except Exception:  # pragma: no cover — celery extra missing
    _celery_app = None  # type: ignore[assignment]


def _pack_celery_task(*dargs, **dkwargs):
    if _celery_app is None:
        def _wrap(fn):
            return fn
        return _wrap
    return _celery_app.task(*dargs, **dkwargs)


def _instantiate_pack_agent(pack: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve and construct the vertical agent for a pack (worker-side).

    Returns a JSON-serializable receipt describing the agent. Raises on
    unknown modules so a misconfigured pack fails loudly in the worker.
    """
    module = pack.get("module", "")
    if module == "verticals/healthcare":
        from verticals.healthcare.agent import HealthcareAgent
        agent = HealthcareAgent()
    elif module == "verticals/compliance":
        from verticals.compliance.agent import ComplianceAgent
        agent = ComplianceAgent()
    elif module == "sadas_orchestrator.py":
        from sincor2.sadas_orchestrator import get_sadas_orchestrator
        orch = get_sadas_orchestrator()
        return {
            "agent": "sadas_orchestrator",
            "next_scheduled_run": str(orch.get_next_scheduled_run()),
        }
    else:
        raise ValueError(f"no agent binding for pack module {module!r}")
    return {
        "agent": agent.name,
        "version": agent.version,
        "capabilities": list(agent.capabilities),
    }


@_pack_celery_task(bind=True, name="sincor2.fulfill_pack_agent", queue=PACK_AGENT_QUEUE)
def fulfill_pack_agent(self=None, intake_id: str = "", pack_sku: str = "",
                       customer_email: str = "") -> Dict[str, Any]:
    """Celery worker entrypoint: deployment kickoff for a fulfilled pack.

    Runs on the ``sincor.long`` queue. Resolves the pack's vertical agent,
    records the kickoff against the intake record, and returns a receipt.
    The agent's buyer-specific work starts once the buyer completes intake
    (via the intake link in their email) — this job never fabricates domain
    payloads (no fake credentialing requests, no demo SBOMs).
    """
    from sincor2.payment_ledger import update_pack_intake_status

    pack = PACK_SKUS.get(pack_sku or "")
    agent_info: Optional[Dict[str, Any]] = None
    if pack is not None:
        agent_info = _instantiate_pack_agent(pack)
    elif (pack_sku or "") not in ("", "agent_services"):
        raise ValueError(f"unknown pack_sku {pack_sku!r} for intake {intake_id}")

    update_pack_intake_status(
        _rail_db_path(),
        intake_id,
        "agent_kickoff",
        details={"agent": agent_info, "customer_email": customer_email},
    )
    logger.info(
        "[FULFILLMENT] pack agent kickoff: intake=%s pack=%s agent=%s",
        intake_id, pack_sku, (agent_info or {}).get("agent"),
    )
    return {
        "status": "ok",
        "intake_id": intake_id,
        "pack_sku": pack_sku,
        "agent": agent_info,
    }


# --- Durable rail state ------------------------------------------------------
_RAIL_EVENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS rail_events (
    event_id           TEXT PRIMARY KEY,
    challenge_id       TEXT NOT NULL,
    pack_sku           TEXT NOT NULL,
    payer_wallet       TEXT NOT NULL DEFAULT '',
    tx_hash            TEXT NOT NULL DEFAULT '',
    amount_usdc        REAL NOT NULL DEFAULT 0,
    verified           INTEGER NOT NULL DEFAULT 0,
    finalized          INTEGER NOT NULL DEFAULT 0,
    fulfillment_status TEXT NOT NULL DEFAULT 'pending',
    intake_id          TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    details            TEXT NOT NULL DEFAULT '{}'
);
"""


def _rail_db_path() -> Path:
    """Ledger file holding rail events, pack intakes, and alerts.

    Shares the revenue ledger file (SINCOR_REVENUE_DB) with the parked
    Stripe implementation so operators keep one place to look.
    """
    return Path(os.environ.get("SINCOR_REVENUE_DB")
                or "data/revenue/revenue_ledger.db")


def _connect_rail() -> sqlite3.Connection:
    db_path = _rail_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.executescript(_RAIL_EVENT_SCHEMA)
    try:
        from sincor2.payment_ledger import _PACK_INTAKE_SCHEMA
        conn.executescript(_PACK_INTAKE_SCHEMA)
    except ImportError:
        logger.warning("[RAIL] pack intake schema unavailable")
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def _resource_id_for(sku: str) -> str:
    return f"{RAIL_RESOURCE_PREFIX}{sku}"


def _sku_for_resource(resource_id: str) -> Optional[str]:
    if not resource_id.startswith(RAIL_RESOURCE_PREFIX):
        return None
    sku = resource_id[len(RAIL_RESOURCE_PREFIX):]
    return sku if sku in PACK_SKUS else None


def _valid_wallet(wallet: str) -> bool:
    return bool(_WALLET_RE.match((wallet or "").strip()))


def _valid_tx_hash(tx_hash: str) -> bool:
    return bool(_TX_HASH_RE.match((tx_hash or "").strip()))


def _download_url(intake_id: str, delivery_token: str) -> str:
    """Buyer-facing token-gated download link for a digital pack.

    Served by GET /download/<intake_id>?token=<token> (rail_delivery).
    """
    return (f"https://getsincor.com/download/{intake_id}"
            f"?token={delivery_token}")


# --- Event claim (settle idempotency) ----------------------------------------
def _claim_rail_event(*, challenge_id: str, pack_sku: str,
                      payer_wallet: str = "", tx_hash: str = "",
                      amount_usdc: float = 0.0) -> Tuple[Dict[str, Any], bool]:
    """Claim the settle event for a challenge, idempotently.

    Returns ``(event, is_new)``: the first claim wins (``is_new=True``);
    a repeat claim returns the existing row with ``is_new=False`` so the
    caller can ack the duplicate instead of re-fulfilling.
    """
    event_id = f"rail-{challenge_id}"
    conn = _connect_rail()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO rail_events "
            "(event_id, challenge_id, pack_sku, payer_wallet, tx_hash, "
            " amount_usdc, finalized, fulfillment_status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (event_id, challenge_id, pack_sku, payer_wallet, tx_hash,
             amount_usdc, 0, "pending", _utc_now_iso()),
        )
        is_new = cur.rowcount == 1
        conn.commit()
        row = conn.execute(
            "SELECT * FROM rail_events WHERE event_id=?", (event_id,)
        ).fetchone()
        return dict(row), is_new
    finally:
        conn.close()


def _update_rail_event(event_id: str, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    conn = _connect_rail()
    try:
        conn.execute(
            f"UPDATE rail_events SET {sets} WHERE event_id=?",
            (*fields.values(), event_id),
        )
        conn.commit()
    finally:
        conn.close()


def _get_rail_event(event_id: str) -> Optional[Dict[str, Any]]:
    conn = _connect_rail()
    try:
        row = conn.execute(
            "SELECT * FROM rail_events WHERE event_id=?", (event_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

# --- Event details (idempotency state for fulfillment retries) ----------------
def _rail_event_details(event_id: str) -> Dict[str, Any]:
    """Read the rail event's JSON details column ({} when absent/corrupt)."""
    event = _get_rail_event(event_id) or {}
    try:
        return json.loads(event.get("details") or "{}") or {}
    except (TypeError, ValueError):
        return {}


def _rail_event_merge_details(event_id: str, updates: Dict[str, Any]) -> None:
    """Merge ``updates`` into the rail event's JSON details column."""
    merged = _rail_event_details(event_id)
    merged.update(updates)
    _update_rail_event(event_id, details=json.dumps(merged))

# --- Money gate --------------------------------------------------------------
def _rail_money_gate_allows(*, event_id: str, challenge_id: str,
                            pack_sku: str, amount_usdc: float,
                            tx_hash: str) -> bool:
    """W-80 (ported): the treasury kill switch gates pack provisioning too.

    Fail-closed: an unavailable gate denies provisioning.
    """
    try:
        from sincor2.governance.money_gate import (
            check_money_effect,
            treasury_halt_tripped,
        )
    except ImportError as exc:
        logger.critical(
            "[FULFILLMENT] money gate unavailable (%s) — rail event %s "
            "DENIED (fail closed)", exc, event_id)
        return False
    allowed, gate_reason = check_money_effect(
        effect_type="payment.transfer",
        payload={"event": "rail.settle",
                 "amount_usdc": amount_usdc,
                 "challenge_id": challenge_id,
                 "tx_hash": tx_hash,
                 "pack_sku": pack_sku},
        agent_id="rail_pack_fulfillment",
        risk_tier="high",
        idempotency_key="rail-pack-%s" % event_id,
        target="rail_pack_provisioning",
        estimated_cost=float(amount_usdc or 0.0),
        kill_switch_tripped=treasury_halt_tripped(),
    )
    if not allowed:
        logger.critical(
            "[FULFILLMENT] money gate denied rail event %s: %s",
            event_id, gate_reason)
    return allowed


def _record_gate_denial(event_id: str, pack_sku: str) -> None:
    try:
        from sincor2.payment_ledger import record_alert
        record_alert(
            _rail_db_path(),
            event_id,
            "pack_fulfillment_gate_denied",
            "Pack fulfillment gate-denied for rail event %s (%s)"
            % (event_id, pack_sku),
            details={"pack_sku": pack_sku, "rail": True},
        )
    except Exception as exc:
        logger.error("[FULFILLMENT] could not record gate-denial alert: %s", exc)


# --- Pack fulfillment (ported from the parked Stripe branch) ------------------
def _fail_pack(event_id: str, order: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """Mark a pack fulfillment failed and raise an alert."""
    from sincor2.payment_ledger import record_alert

    _update_rail_event(event_id, fulfillment_status="failed")
    try:
        record_alert(
            _rail_db_path(),
            event_id or "unknown",
            "pack_fulfillment_failed",
            "Pack fulfillment failed for %s: %s"
            % (order.get("customer_email", order.get("payer_wallet", "?")),
               reason),
            details={"pack_sku": order.get("pack_sku"), "reason": reason,
                     "rail": True},
        )
    except Exception as alert_exc:
        logger.error("[FULFILLMENT] Could not record failure alert: %s", alert_exc)
    return {"status": "failed", "type": "pack", "error": reason}


def _render_pack_email(*, customer_name: str, pack_name: str,
                       customer_email: str, intake_url: str,
                       order_id: str, digital: bool = False,
                       download_url: str = "") -> str:
    """Render templates/thank_you_purchase_email.html for a pack buyer."""
    from jinja2 import Template

    template_path = _TEMPLATE_DIR / "thank_you_purchase_email.html"
    if not template_path.is_file():
        raise FileNotFoundError(
            f"pack email template missing: {template_path}"
        )
    html = template_path.read_text(encoding="utf-8-sig")
    if digital:
        agent_count = "instant digital download"
        feature_list = (
            f"Your {pack_name} is ready — download it here: {download_url}"
        )
        quickstart = download_url
    else:
        agent_count = "dedicated vertical agent"
        feature_list = (
            f"{pack_name} deployment kickoff — vertical agent queued. "
            f"Complete your intake here: {intake_url}"
        )
        quickstart = (
            f"https://getsincor.com/files/guides/quickstart-{order_id}.pdf"
        )
    body = Template(html).render(
        CUSTOMER_NAME=customer_name,
        TIER_NAME=pack_name,
        AGENT_COUNT=agent_count,
        FEATURE_LIST=feature_list,
        ACTIVATION_DATE=datetime.now(timezone.utc).date().isoformat(),
        CUSTOMER_EMAIL=customer_email,
        DASHBOARD_URL="https://getsincor.com/",
        HELP_URL="https://getsincor.com/help",
        STATUS_URL="https://getsincor.com/status",
        COMPANY_ADDRESS="SINCOR Business Solutions",
        UNSUBSCRIBE="https://getsincor.com/unsubscribe",
        DOWNLOAD_QUICKSTART=quickstart,
        STARTER_SELECTED=False,
        PROFESSIONAL_SELECTED=False,
        ENTERPRISE_SELECTED=False,
    )
    if digital:
        banner = (
            '<div style="background:#eef7ee;border-left:4px solid #22a355;'
            'padding:20px;border-radius:8px;margin:30px 0;">'
            '<h4 style="margin:0 0 10px 0;color:#22a355;">Your download '
            'is ready</h4>'
            '<p style="margin:0 0 12px 0;font-size:14px;color:#4a5568;">'
            'This link is unique to your purchase — keep it private:</p>'
            f'<a href="{download_url}" style="display:inline-block;background:'
            '#22a355;color:white;padding:12px 24px;border-radius:6px;'
            'text-decoration:none;font-weight:600;">Download your pack →</a>'
            '</div>'
        )
    else:
        # Intake call-to-action banner (template file itself is not modified).
        banner = (
            '<div style="background:#eef2ff;border-left:4px solid #667eea;'
            'padding:20px;border-radius:8px;margin:30px 0;">'
            '<h4 style="margin:0 0 10px 0;color:#667eea;">Next step: '
            'complete your intake</h4>'
            '<p style="margin:0 0 12px 0;font-size:14px;color:#4a5568;">'
            'Your vertical agent is queued. Tell us about your operation so '
            'it can start your first paid workflow:</p>'
            f'<a href="{intake_url}" style="display:inline-block;background:'
            '#667eea;color:white;padding:12px 24px;border-radius:6px;'
            'text-decoration:none;font-weight:600;">Complete intake →</a>'
            '</div>'
        )
    anchor = "<!-- Dashboard Access -->"
    if anchor in body:
        body = body.replace(anchor, anchor + "\n" + banner, 1)
    else:  # template changed upstream — never silently drop the link
        body = body.replace("</body>", banner + "\n</body>", 1)
    return body


def _send_pack_email(*, to_email: str, customer_name: str,
                     pack_name: str, intake_url: str, order_id: str,
                     digital: bool = False,
                     download_url: str = "") -> Dict[str, str]:
    """Email the buyer their pack confirmation + intake/download link.

    Raises unless the provider confirms the send — a silent stub is a
    fulfillment failure, not a delivery.
    """
    from sincor2.email_sender import EmailSender

    html = _render_pack_email(
        customer_name=customer_name,
        pack_name=pack_name,
        customer_email=to_email,
        intake_url=intake_url,
        order_id=order_id,
        digital=digital,
        download_url=download_url,
    )
    if digital:
        subject = f"Your SINCOR {pack_name} — download your pack"
        text = (
            f"Thank you for purchasing SINCOR {pack_name}!\n\n"
            f"Your download is ready (this link is unique to your "
            f"purchase — keep it private):\n{download_url}\n\n"
            f"Order: {order_id}\nSupport: support@getsincor.com\n"
        )
    else:
        subject = f"Your SINCOR {pack_name} pack — complete your intake"
        text = (
            f"Thank you for purchasing SINCOR {pack_name}!\n\n"
            f"Your vertical agent deployment is queued.\n"
            f"Next step — complete your intake: {intake_url}\n\n"
            f"Order: {order_id}\nSupport: support@getsincor.com\n"
        )
    result = EmailSender().send_email(
        to_email=to_email,
        to_name=customer_name,
        subject=subject,
        html_content=html,
        text_content=text,
        metadata={"order_id": order_id, "pack": pack_name,
                  "intake_url": intake_url, "type": "pack_fulfillment",
                  "rail": True},
    )
    if result.get("status") != "sent":
        raise RuntimeError(
            f"pack email not sent (status={result.get('status')}, "
            f"error={result.get('error')})"
        )
    logger.info("[FULFILLMENT] Pack email sent to %s", to_email)
    return result


def _queue_pack_agent_job(*, intake_id: str, pack_sku: str,
                          customer_email: str) -> str:
    """Queue the vertical-agent deployment job on the sincor.long queue."""
    if _celery_app is None:
        raise RuntimeError("celery unavailable — cannot queue pack agent job")
    result = fulfill_pack_agent.apply_async(
        args=[intake_id, pack_sku, customer_email],
        queue=PACK_AGENT_QUEUE,
    )
    logger.info(
        "[FULFILLMENT] Queued pack agent job %s on %s", result.id, PACK_AGENT_QUEUE)
    return result.id


def _fulfill_rail_pack(order: Dict[str, Any]) -> Dict[str, Any]:
    """Fulfill a pack order paid over the x402 rail.

    B2B skill packs: intake record + buyer email with the intake link +
    vertical-agent deployment job on the ``sincor.long`` queue.

    Digital packs: bind an unguessable delivery token to the intake
    record and email the buyer a token-gated download link
    (GET /download/<intake_id>?token=<token>); NO vertical-agent job is
    queued. Token issuance is idempotent per purchase: on a retry
    redelivery the intake row already holds the original token and it is
    reused — a second token is never minted.

    Steps, in order:
      1. create an intake record (idempotent on the event id),
      2. email the buyer (skipped for wallet-native buyers with no email —
         the intake link is returned in the settle response instead),
      3. fulfillment work: for the webbuilder_site SKU the 4.1 WebBuilder
         bundle (static HTML site via the webbuilder modules) plus a
         webbuilder_crm record is built synchronously here; every other
         pack queues the vertical-agent job on the ``sincor.long`` Celery
         queue,      4. mark the rail event ``delivered`` — only when every step above
         succeeded. Any failure marks the event ``failed`` and records an
         alert.
    """
    from sincor2.payment_ledger import record_pack_intake

    event_id = order.get("event_id", "")
    customer_email = order.get("customer_email", "")
    customer_name = order.get("customer_name", "") or customer_email or "buyer"
    payer_wallet = order.get("payer_wallet", "")
    pack_sku = order.get("pack_sku")
    pack = PACK_SKUS.get(pack_sku) if pack_sku else None

    if pack_sku and pack is None:
        return _fail_pack(event_id, order, f"unknown pack_sku {pack_sku!r}")

    is_digital = bool(pack and pack.get("digital"))
    pack_name = pack["name"] if pack else "Agent Services"
    try:
        # Digital packs: bind an unguessable delivery token to the intake
        # record. The download link is intake-gated AND token-gated — the
        # intake id alone is not enough to fetch the asset.
        delivery_token = ""
        asset = ""
        if is_digital:
            from sincor2.revenue_orchestrator import issue_delivery_token
            asset = (pack or {}).get("asset", "")
            if not asset:
                raise ValueError(
                    f"digital pack {pack_sku!r} has no asset bound")
            if asset != _DIGITAL_SKUS.get(pack_sku, {}).get("asset"):
                raise ValueError(
                    f"digital pack {pack_sku!r} asset mismatch")
            delivery_token = issue_delivery_token()

        # 1. Intake record (idempotent: same event -> same single row).
        intake_id = f"intake-{event_id}" if event_id else None
        if not intake_id:
            import uuid as _uuid
            intake_id = f"intake-{_uuid.uuid4().hex}"
        if is_digital:
            details = {
                "pack_name": pack_name,
                "asset": asset,
                "delivery_token": delivery_token,
                "digital": True,
                "payer_wallet": payer_wallet,
                "rail": True,
            }
            access_url = ""  # derived from the intake row below
        else:
            access_url = f"https://getsincor.com/intake/{intake_id}"
            details = {
                "pack_name": pack_name,
                "module": (pack or {}).get("module", ""),
                "payer_wallet": payer_wallet,
                "rail": True,
            }
        intake, _is_new = record_pack_intake(
            _rail_db_path(),
            intake_id=intake_id,
            customer_email=customer_email or payer_wallet or "wallet-buyer",
            pack_sku=pack_sku or "agent_services",
            event_id=event_id,
            intake_url=access_url or _download_url(intake_id, delivery_token),
            details=details,
        )
        if is_digital:
            # The intake row is authoritative: on a retry redelivery
            # (INSERT OR IGNORE kept the first row) the buyer must get
            # the ORIGINAL token, not a fresh one.
            delivery_token = str(
                (intake.details or {}).get("delivery_token") or "")
            if not delivery_token:
                raise ValueError(
                    f"digital intake {intake.intake_id} has no delivery token")
            access_url = _download_url(intake.intake_id, delivery_token)
        logger.info("[FULFILLMENT] Intake record: %s", intake.intake_id)

        # 2. Buyer email with the intake link (B2B) or the token-gated
        #    download link (digital). Wallet-native buyers skip email —
        #    the link is returned in the settle response instead.
        #    Idempotent per event: the sent flag survives a failed
        #    fulfillment, so a settle retry never re-sends the thank-you
        #    email.
        if customer_email:
            if not _rail_event_details(event_id).get("thank_you_email_sent"):
                _send_pack_email(
                    to_email=customer_email,
                    customer_name=customer_name,
                    pack_name=pack_name,
                    intake_url=access_url,
                    order_id=intake.intake_id,
                    digital=is_digital,
                    download_url=access_url if is_digital else "",
                )
                _rail_event_merge_details(
                    event_id, {"thank_you_email_sent": True})
        else:
            logger.info(
                "[FULFILLMENT] no buyer email for %s — intake link returned "
                "in the settle response", event_id)

        # 3. Fulfillment work.
        webbuilder_receipt: Optional[Dict[str, Any]] = None
        if pack_sku == "webbuilder_site":
            # Zero-to-revenue 4.2: the deliverable is the 4.1 WebBuilder
            # bundle (static HTML site via the webbuilder modules) plus a
            # webbuilder_crm record — built synchronously here, not via
            # the vertical-agent Celery job (that worker raises for packs
            # with no vertical-agent binding). Resume-safe: a retry of a
            # failed event continues from the already-created project.
            webbuilder_receipt = _fulfill_webbuilder_site(
                event_id=event_id,
                intake_id=intake.intake_id,
                customer_email=customer_email or payer_wallet,
                customer_name=customer_name,
                order=order,
            )
            job_id = webbuilder_receipt["project_id"]
        elif is_digital:
            # Digital packs have nothing to deploy.
            job_id = None
        else:
            # Vertical-agent deployment job on the sincor.long queue.
            job_id = _queue_pack_agent_job(
                intake_id=intake.intake_id,
                pack_sku=pack_sku or "agent_services",
                customer_email=customer_email or payer_wallet,
            )

    except Exception as exc:
        logger.exception("[FULFILLMENT] Pack fulfillment failed for %s", event_id)
        return _fail_pack(event_id, order, str(exc))

    # 4. Delivered — only on full success.
    _update_rail_event(event_id, fulfillment_status="delivered",
                       intake_id=intake.intake_id)
    result: Dict[str, Any] = {
        "status": "delivered",
        "type": "pack",
        "pack_sku": pack_sku,
        "intake_id": intake.intake_id,
        "intake_url": access_url,
        "job_id": job_id,
        "webbuilder": webbuilder_receipt,
    }
    if is_digital:
        result["digital"] = True
        result["download_url"] = access_url
    return result


def _fulfill_webbuilder_site(*, event_id: str, intake_id: str,
                             customer_email: str, customer_name: str,
                             order: Dict[str, Any]) -> Dict[str, Any]:
    """Build the WebBuilder site bundle, idempotent across settle retries.

    The studio project is the non-idempotent half: it is persisted to the
    rail event's details the moment it exists, so a retry of a failed event
    resumes from the recorded project_id (CRM record + deliverable receipt)
    instead of building a second project, site dir, and thank-you bundle.
    A recorded project that no longer exists in the studio store (operator
    deleted it) is treated as fresh — rebuild is the safe failover.
    """
    from sincor2.revenue_orchestrator import RevenueOrchestrator
    from sincor2 import webbuilder_studio as _wb

    wb_state = dict(_rail_event_details(event_id).get("webbuilder") or {})
    project = None
    project_id = wb_state.get("project_id")
    if project_id:
        project = _wb.get_project(project_id)

    phone = ((order.get("details") or {}).get("phone") or "").strip()
    if project is None:
        created = RevenueOrchestrator._create_webbuilder_project(
            customer_email=customer_email,
            customer_name=customer_name,
            order=order,
        )
        project_id = created["project_id"]
        phone = created.get("phone", phone)
        wb_state.update({"project_id": project_id,
                         "slug": created.get("slug"),
                         "name": created.get("name", "")})
        _rail_event_merge_details(event_id, {"webbuilder": wb_state})
        project = _wb.get_project(project_id) or {
            "id": project_id, "slug": created.get("slug"),
            "name": created.get("name", ""),
            "preview_url": created.get("preview_url", ""),
        }

    return RevenueOrchestrator._complete_webbuilder_bundle(
        project=project,
        phone=phone,
        intake_id=intake_id,
        customer_email=customer_email,
        customer_name=customer_name,
    )


# --- Settle pipeline ----------------------------------------------------------
def _finalize_once(*, event_id: str, event: Dict[str, Any],
                   verify_result: Optional[Dict[str, Any]],
                   pack_sku: str) -> None:
    """Record the platform payment + treasury inflow exactly once.

    ``finalize_challenge_payment`` appends to JSONL logs (not idempotent),
    so the rail event's ``finalized`` flag gates it. ``verify_result`` is
    the verify call that transitioned the challenge (status 'fulfilled');
    on retry paths it is None and the finalize input is rebuilt from the
    verified event row + the pack resource — the on-chain transfer was
    verified when the event was marked verified.
    """
    if event.get("finalized"):
        return
    from sincor2 import x402_payments as x402

    if verify_result and verify_result.get("status") == "fulfilled":
        result = verify_result
    elif event.get("verified"):
        resource = x402.get_resource(_resource_id_for(pack_sku))
        if not resource:
            raise RuntimeError("pack resource missing for finalize")
        result = {
            "ok": True,
            "status": "fulfilled",
            "challenge_id": event["challenge_id"],
            "resource_id": _resource_id_for(pack_sku),
            "tx_hash": event.get("tx_hash"),
            "payer_wallet": event.get("payer_wallet"),
            "amount_atomic": resource["amount_atomic"],
            "amount_display": resource["amount_display"],
        }
    else:
        raise RuntimeError(
            "cannot finalize: challenge not verified and no verify result")
    x402.finalize_challenge_payment(result)
    _update_rail_event(event_id, finalized=1,
                       tx_hash=str(result.get("tx_hash") or event.get("tx_hash") or ""))
    logger.info("[RAIL] finalized payment for event %s", event_id)


def _existing_result(event: Dict[str, Any]) -> Dict[str, Any]:
    """Build the idempotent ack for an already-fulfilled settle."""
    from sincor2.payment_ledger import get_pack_intake

    intake = None
    if event.get("intake_id"):
        try:
            intake = get_pack_intake(_rail_db_path(), event["intake_id"])
        except Exception:
            intake = None
    fulfillment: Dict[str, Any] = {
        "status": event.get("fulfillment_status"),
        "intake_id": event.get("intake_id"),
        "intake_url": intake.intake_url if intake else "",
    }
    if intake is not None:
        # Digital packs: the ack carries the ORIGINAL download URL —
        # re-derived from the stored delivery token, never re-minted.
        details = intake.details or {}
        token = str(details.get("delivery_token") or "")
        if details.get("digital") and token:
            fulfillment["download_url"] = _download_url(
                event.get("intake_id") or "", token)
    return {
        "ok": True,
        "status": "already_fulfilled",
        "duplicate": True,
        "pack_sku": event.get("pack_sku"),
        "challenge_id": event.get("challenge_id"),
        "fulfillment": fulfillment,
    }


def _verify_fail(result: Dict[str, Any]) -> Dict[str, Any]:
    """Map a failed verify_challenge result to a settle error (fail closed).

    rpc_failed / tx_pending / tx_failed are returned as errors, never as
    payment. replay_auth_failed carries the sanctioned recovery hint: the
    payer re-settles with an EIP-191 replay signature (W-103).
    """
    out: Dict[str, Any] = {
        "ok": False,
        "error": result.get("error", "verify_failed"),
        "detail": result.get("detail", ""),
    }
    if result.get("error") == "replay_auth_failed":
        out["replay_hint"] = (
            "challenge already fulfilled: re-settle with replay_signature + "
            "replay_ts_ms — an EIP-191 personal_sign of "
            "build_replay_message(challenge_id, ts_ms) by the payer wallet")
    return out


def settle_rail_order(challenge_id: str, tx_hash: str, *,
                      payer_wallet: str = "",
                      buyer_email: str = "",
                      buyer_name: str = "",
                      replay_signature: str = "",
                      replay_ts_ms: Any = None,
                      business_name: str = "",
                      phone: str = "",
                      niche: str = "",
                      territory: str = "",
                      prompt: str = "") -> Dict[str, Any]:
    """Run the rail settle pipeline for one x402 challenge.

    claim event (idempotent) -> verify onchain USDC transfer (fail-closed
    on rpc_failed/tx_pending) -> finalize payment once -> money gate
    (fail-closed, gate-denial alert) -> fulfill pack (intake + email +
    agent job, or the synchronous WebBuilder bundle for webbuilder_site).

    The business_* fields are optional order details for the
    webbuilder_site SKU (business name / phone / niche / territory /
    prompt flow into the WebBuilder project); a client-supplied price is
    never accepted anywhere on this path.

    A re-settle of an already-fulfilled challenge returns the existing
    result without touching the chain or double-provisioning. A retry of a
    claimed-but-incomplete settle reuses the verified event row (the
    on-chain transfer was verified when the event was marked verified);
    only the narrow crash window between the x402 fulfill and the
    verified-flag write needs the W-103 replay signature, which is
    accepted here and forwarded to verify_challenge.

    Subscription challenges (rail-sub-* resources) are routed to
    rail_subscriptions.settle_renewal, which extends the wallet's
    subscription exactly one period instead of fulfilling a pack.
    """
    from sincor2 import x402_payments as x402
    from sincor2 import rail_subscriptions as rs

    challenge_id = (challenge_id or "").strip()
    tx_hash = (tx_hash or "").strip()
    payer_wallet = (payer_wallet or "").strip().lower()
    if not challenge_id or not _valid_tx_hash(tx_hash):
        return {"ok": False, "error": "challenge_id_and_tx_hash_required"}

    event_id = f"rail-{challenge_id}"
    event = _get_rail_event(event_id)
    sub_settle_id = f"railsub-{challenge_id}"
    sub_settle = rs._get_sub_settle(sub_settle_id)
    verify_result: Optional[Dict[str, Any]] = None

    if event is not None:
        # Duplicate ack: the idempotency claim is the source of truth —
        # no chain call, no token re-issue, no double-provisioning.
        if event.get("fulfillment_status") in _FULFILLED_STATUSES:
            logger.info("[RAIL] Duplicate settle for %s — acknowledged", event_id)
            return _existing_result(event)
        # Retry of a claimed-but-incomplete settle: the tx_hash and payer
        # are pinned to the claim (fail closed on mismatch).
        claimed_tx = (event.get("tx_hash") or "").lower()
        if claimed_tx and tx_hash.lower() != claimed_tx:
            return {"ok": False, "error": "tx_hash_mismatch",
                    "event_id": event_id}
        claimed_payer = (event.get("payer_wallet") or "").lower()
        if payer_wallet and claimed_payer and payer_wallet != claimed_payer:
            return {"ok": False, "error": "payer_wallet_mismatch",
                    "event_id": event_id}
        pack_sku = event.get("pack_sku") or ""
        pack = PACK_SKUS.get(pack_sku)
        if not pack:
            return {"ok": False, "error": "unknown_pack_sku",
                    "event_id": event_id}
        if not event.get("verified"):
            # The first attempt died before verify completed — re-verify
            # (the challenge is still pending, so no replay auth needed).
            verify = x402.verify_challenge(
                challenge_id, event.get("tx_hash") or tx_hash,
                payer_wallet=payer_wallet,
                replay_signature=replay_signature, replay_ts_ms=replay_ts_ms)
            if not verify.get("ok"):
                return _verify_fail(verify)
            _update_rail_event(event_id, verified=1,
                               payer_wallet=str(verify.get("payer_wallet") or ""))
            verify_result = verify
    elif sub_settle is not None:
        # Subscription retry/duplicate: the subscription pipeline owns
        # its claim — settle_renewal acks duplicates without touching
        # the chain and retries claimed-but-incomplete settles.
        return rs.settle_renewal(
            challenge_id, tx_hash, payer_wallet,
            replay_signature=replay_signature, replay_ts_ms=replay_ts_ms)
    else:
        # Subscription fast path: a challenge this module issued is
        # wallet-bound — a different wallet is refused BEFORE any chain
        # call (the x402 payer check below is the second layer).
        issued = rs._get_issued(challenge_id)
        if issued is not None and issued["wallet"] != payer_wallet:
            return {"ok": False, "error": "wallet_mismatch",
                    "settle_id": sub_settle_id}
        # First attempt: verify the on-chain transfer, then claim.
        verify = x402.verify_challenge(
            challenge_id, tx_hash, payer_wallet=payer_wallet,
            replay_signature=replay_signature, replay_ts_ms=replay_ts_ms)
        if not verify.get("ok"):
            return _verify_fail(verify)

        # Route by resource: pack challenges provision packs, subscription
        # challenges extend subscriptions — never cross-provisioned.
        resource_id = str(verify.get("resource_id") or "")
        pack_sku = _sku_for_resource(resource_id)
        plan_id = rs._plan_for_resource(resource_id)
        if pack_sku:
            pack = PACK_SKUS[pack_sku]
            event, _is_new = _claim_rail_event(
                challenge_id=challenge_id, pack_sku=pack_sku,
                payer_wallet=str(verify.get("payer_wallet") or payer_wallet),
                tx_hash=tx_hash,
                amount_usdc=pack["price_cents"] / 100)
            _update_rail_event(event_id, verified=1)
            verify_result = verify
        elif plan_id:
            # Subscription pipeline: the verify above is the single
            # on-chain check — forward it so settle_renewal does not
            # re-verify (which would hit the x402 already_fulfilled
            # replay wall).
            return rs.settle_renewal(
                challenge_id, tx_hash, payer_wallet,
                replay_signature=replay_signature,
                replay_ts_ms=replay_ts_ms,
                _preverified=verify)
        else:
            return {"ok": False, "error": "not_a_pack_challenge",
                    "resource_id": resource_id}

    amount_usdc = pack["price_cents"] / 100

    try:
        # Record the platform payment + treasury inflow exactly once.
        # NOTE: no automatic 5% fee split is booked (known gap; founder
        # decision owed) — payments are recorded faithfully.
        _finalize_once(event_id=event_id,
                       event=_get_rail_event(event_id) or event,
                       verify_result=verify_result, pack_sku=pack_sku)

        # Treasury kill-switch money gate (fail closed). A denial records
        # an alert and stops provisioning; the claim stays non-delivered
        # so an operator can retry after review.
        if not _rail_money_gate_allows(
                event_id=event_id, challenge_id=challenge_id,
                pack_sku=pack_sku, amount_usdc=amount_usdc, tx_hash=tx_hash):
            _record_gate_denial(event_id, pack_sku)
            _update_rail_event(event_id, fulfillment_status="gate_denied")
            return {"ok": False, "error": "money_gate_denied",
                    "event_id": event_id}

        # Fulfill the pack.
        order = {
            "event_id": event_id,
            "customer_email": buyer_email,
            "customer_name": buyer_name,
            "payer_wallet": (_get_rail_event(event_id) or {}).get("payer_wallet", ""),
            "pack_sku": pack_sku,
            "amount": amount_usdc,
            "tx_hash": tx_hash,
            "challenge_id": challenge_id,
            # Order details (e.g. business_name/phone for the
            # webbuilder_site SKU) flow to fulfillment.
            "details": {
                "business_name": business_name,
                "phone": phone,
                "niche": niche,
                "territory": territory,
                "prompt": prompt,
            },
        }
        fulfillment = _fulfill_rail_pack(order)
        if fulfillment.get("status") != "delivered":
            return {"ok": False, "error": fulfillment.get("error", "fulfillment_failed"),
                    "event_id": event_id, "fulfillment": fulfillment}
        return {"ok": True, "status": "fulfilled", "event_id": event_id,
                "pack_sku": pack_sku, "challenge_id": challenge_id,
                "fulfillment": fulfillment}
    except Exception as exc:
        logger.exception("[RAIL] settle pipeline failed for %s", event_id)
        _update_rail_event(event_id, fulfillment_status="failed")
        return {"ok": False, "error": "settle_pipeline_failed",
                "detail": str(exc), "event_id": event_id}


# --- Flask blueprint ----------------------------------------------------------
rail_bp = Blueprint("rail", __name__, url_prefix="/rail")


def _rail_subscription_checkout(data: Dict[str, Any], plan_id: str):
    """Mint an x402 challenge for one subscription billing period.

    Body: {subscription_plan, payer_wallet}. The price is derived from
    SUB_PLANS on the server — a client-supplied price is never trusted.
    Unknown plan -> 400 (fail-closed allowlist). A subscription must
    attach to a wallet, so payer_wallet is required here (unlike packs).
    """
    from sincor2 import rail_subscriptions as rs

    if plan_id not in rs.SUB_PLANS:
        return jsonify({"ok": False,
                        "error": f"Unknown subscription_plan: {plan_id}"}), 400

    payer_wallet = str(data.get("payer_wallet") or "").strip().lower()
    if not payer_wallet:
        return jsonify({"ok": False,
                        "error": "subscription_wallet_required"}), 400
    if not _valid_wallet(payer_wallet):
        return jsonify({"ok": False, "error": "invalid_payer_wallet"}), 400

    try:
        challenge = rs.issue_renewal_challenge(payer_wallet, plan_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except RuntimeError as exc:
        logger.error("[RAIL] subscription checkout failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500

    http_status = int(challenge.get("http_status") or 402)
    return jsonify({
        "ok": challenge.get("ok", False),
        "plan_id": challenge["plan_id"],
        "plan_name": challenge["plan_name"],
        "amount_usdc": challenge["amount_usdc"],
        "challenge_id": challenge.get("challenge_id"),
        "resource_id": challenge.get("resource_id"),
        "expires_at": challenge.get("expires_at"),
        "payment": challenge.get("payment"),
        "bind": challenge.get("bind"),
        "message": challenge.get("message"),
        "payer_wallet": payer_wallet,
    }), http_status


@rail_bp.post("/checkout")
def rail_checkout():
    """Mint an x402 challenge for a pack purchase or a subscription period.

    Body: {pack_sku, payer_wallet?, buyer_email?} for packs, or
    {subscription_plan, payer_wallet} for subscriptions (mutually
    exclusive). Prices are derived from server-side tables — a
    client-supplied price is never trusted. Unknown SKU/plan -> 400.
    """
    from sincor2 import x402_payments as x402

    data = request.get_json(force=True, silent=True) or {}
    pack_sku = str(data.get("pack_sku") or "").strip()
    plan_id = str(data.get("subscription_plan") or "").strip().lower()
    if pack_sku and plan_id:
        return jsonify(
            {"ok": False,
             "error": "pack_sku_and_subscription_plan_mutually_exclusive"}
        ), 400
    if plan_id:
        return _rail_subscription_checkout(data, plan_id)

    pack = PACK_SKUS.get(pack_sku)
    if not pack:
        return jsonify({"ok": False,
                        "error": f"Unknown pack_sku: {pack_sku}"}), 400

    payer_wallet = str(data.get("payer_wallet") or "").strip().lower()
    if payer_wallet and not _valid_wallet(payer_wallet):
        return jsonify({"ok": False, "error": "invalid_payer_wallet"}), 400
    buyer_email = str(data.get("buyer_email") or "").strip()

    # Price cross-check: the x402 resource must agree with PACK_SKUS.
    # A mismatch is a server config error — fail closed, never charge.
    resource_id = _resource_id_for(pack_sku)
    resource = x402.get_resource(resource_id)
    if not resource:
        logger.error("[RAIL] x402 resource missing for pack %s", pack_sku)
        return jsonify({"ok": False, "error": "pack_resource_not_configured"}), 500
    expected_usdc = pack["price_cents"] / 100
    if abs(float(resource["amount_usdc"]) - expected_usdc) > 0.001:
        logger.critical(
            "[RAIL] price config drift for %s: PACK_SKUS=%s vs yaml=%s",
            pack_sku, expected_usdc, resource["amount_usdc"])
        return jsonify({"ok": False, "error": "pack_price_mismatch"}), 500

    challenge = x402.create_challenge(resource_id)
    http_status = int(challenge.get("http_status") or 402)
    body = {
        "ok": challenge.get("ok", False),
        "pack_sku": pack_sku,
        "pack_name": pack["name"],
        "amount_usdc": expected_usdc,
        "challenge_id": challenge.get("challenge_id"),
        "resource_id": challenge.get("resource_id"),
        "expires_at": challenge.get("expires_at"),
        "payment": challenge.get("payment"),
        "bind": challenge.get("bind"),
        "message": challenge.get("message"),
    }
    if payer_wallet:
        body["payer_wallet"] = payer_wallet
    if buyer_email:
        body["buyer_email"] = buyer_email
    return jsonify(body), http_status


@rail_bp.post("/settle")
def rail_settle():
    """Verify the on-chain USDC transfer and fulfill the pack — or extend
    a subscription when the challenge is for a rail-sub-* resource.

    Body: {challenge_id, tx_hash, payer_wallet?, buyer_email?,
           replay_signature?, replay_ts_ms?, business_name?, phone?,
           niche?, territory?, prompt?}.
    Fail-closed: rpc_failed/tx_pending from the RPC layer are returned as
    errors, never as payment. Idempotent on re-settle. replay_signature +
    replay_ts_ms are the W-103 recovery path: an EIP-191 personal_sign of
    build_replay_message(challenge_id, ts_ms) by the payer wallet, needed
    only when the challenge was fulfilled outside this settle (narrow
    crash window between the x402 fulfill and the rail verified-flag
    write). business_name/phone/niche/territory/prompt are optional order
    details for the webbuilder_site SKU.
    """
    data = request.get_json(force=True, silent=True) or {}
    result = settle_rail_order(
        str(data.get("challenge_id") or ""),
        str(data.get("tx_hash") or ""),
        payer_wallet=str(data.get("payer_wallet") or ""),
        buyer_email=str(data.get("buyer_email") or "").strip(),
        buyer_name=str(data.get("buyer_name") or "").strip(),
        replay_signature=str(data.get("replay_signature") or ""),
        replay_ts_ms=data.get("replay_ts_ms"),
        business_name=str(data.get("business_name") or "").strip(),
        phone=str(data.get("phone") or "").strip(),
        niche=str(data.get("niche") or "").strip(),
        territory=str(data.get("territory") or "").strip(),
        prompt=str(data.get("prompt") or "").strip(),
    )
    status = 200 if result.get("ok") else 402
    if result.get("error") in ("challenge_id_and_tx_hash_required",
                               "not_a_pack_challenge",
                               "tx_hash_mismatch",
                               "payer_wallet_mismatch",
                               "unknown_pack_sku",
                               # subscription pipeline client errors
                               "invalid_wallet",
                               "challenge_not_issued",
                               "wallet_mismatch",
                               "not_a_subscription_challenge",
                               "downgrade_at_renewal"):
        status = 400
    return jsonify(result), status


def init_rail_routes(app) -> None:
    """Mount the x402 pack + subscription rail blueprint on a Flask app."""
    app.register_blueprint(rail_bp)
    logger.info("[RAIL] registered /rail/checkout and /rail/settle "
                "(packs + subscription plans)")
