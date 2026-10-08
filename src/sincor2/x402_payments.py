"""HTTP 402 micropayments (spec §5.4).

Challenges are denominated per resource config; platform policy prefers AXM
for new flows (SINC is legacy for residual subscription renewals only).

Design constraint (dev-watch item 79, Payload Tools postmortem 2026-10-07):
``verify`` is an authorization-shape check, NOT payment. ``settle`` is the
money movement. Work must NEVER be served on a verify receipt alone — the
postmortem documents sellers doing unpaid work on credit because verify
passed on every attempt while settle rejected everything with
``invalid_payload`` (token ``name`` had to be exactly "USD Coin", not
"USDC"; a $0.001 amount minimum — neither was in the spec). The
``SETTLE_BEFORE_SERVE`` constant is True, and ``require_settled()`` is the
single guard every serve path must call before doing paid work. The
batch-settlement voucher section below covers offchain promises only:
vouchers are promises, not settlement, and batch settlement does NOT relax
settle-before-serve.
"""

# ---------------------------------------------------------------------------
# CDP SDK integration notes (dev-watch item 78, 2026-10-08).
#
# CDP_SDK_PIN: there is NO cdp-sdk in requirements.txt / requirements.lock.
# This module speaks the x402 wire protocol directly (challenge payloads)
# and does not import the CDP SDK, so nothing is pinned here.  Pin cdp-sdk
# in requirements BEFORE any live CdpX402Client/facilitator integration,
# then re-run the explicit-network tests in
# tests/pytest/test_cdp_explicit_networks.py against the pinned version.
#
# CDP 1.58.0 notes:
#   * revoke-delegation moved DELETE -> POST; the old routes are deprecated
#     and stop working 2026-10-22.  Any delegation-revocation flow built
#     later must use POST.
#   * new Mandates API (createMandate/getMandate/listMandates/
#     cancelMandate/approveWalletMandate/revokeWalletMandate/
#     authorizeMandatePaymentSession).  See MANDATES_API_WATCH below.
# ---------------------------------------------------------------------------

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final, Optional, Sequence

import yaml

from sincor2.agent_billing import record_platform_payment
from sincor2.platform_payments import (
    CHAIN_ID,
    atomic_to_display,
    display_to_atomic,
    token_address,
    token_decimals,
    TREASURY,
    verify_treasury_transfer,
)
from sincor2.treasury_inflow import record_inflow

_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG = _ROOT / "config" / "x402_pricing.yaml"


def _db_path() -> Path:
    from sincor2.data_paths import orders_db_path

    return orders_db_path()


def init_x402_db() -> None:
    conn = sqlite3.connect(_db_path())
    conn.execute(
        """CREATE TABLE IF NOT EXISTS x402_challenges (
            challenge_id TEXT PRIMARY KEY,
            resource_id TEXT NOT NULL,
            amount_atomic TEXT NOT NULL,
            amount_display REAL NOT NULL,
            payer_wallet TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            tx_hash TEXT,
            access_token TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            fulfilled_at TEXT
        )"""
    )
    conn.commit()
    conn.close()


def _conn() -> sqlite3.Connection:
    init_x402_db()
    c = sqlite3.connect(_db_path())
    c.row_factory = sqlite3.Row
    return c


def load_pricing() -> dict[str, Any]:
    if not _CONFIG.is_file():
        return {"resources": {}, "defaults": {}}
    return yaml.safe_load(_CONFIG.read_text(encoding="utf-8")) or {}


def get_resource(resource_id: str) -> dict[str, Any] | None:
    cfg = load_pricing()
    res = (cfg.get("resources") or {}).get(resource_id)
    if not res:
        return None
    defaults = cfg.get("defaults") or {}
    token = str(res.get("token") or defaults.get("token") or "SINC").upper()
    if token == "AXIOM":
        token = "AXM"
    amount = float(
        res.get(f"amount_{token.lower()}")
        or res.get("amount")
        or res.get("amount_display")
        or res.get("amount_sinc")
        or 1
    )
    ttl = int(defaults.get("challenge_ttl_seconds", 900))
    resource = {
        "id": resource_id,
        "label": res.get("label", resource_id),
        "description": res.get("description", ""),
        "amount_display": amount,
        "amount_atomic": str(display_to_atomic(amount, token)),
        "token": token,
        "token_address": token_address(token),
        "token_decimals": token_decimals(token),
        "treasury": res.get("treasury", defaults.get("treasury", TREASURY)),
        "chain_id": int(res.get("chain_id", defaults.get("chain_id", CHAIN_ID))),
        "ttl_seconds": ttl,
        "skill_id": str(res.get("skill_id") or ""),
    }
    if token == "SINC":
        resource["amount_sinc"] = amount
    if token == "USDC":
        resource["amount_usdc"] = amount
    return resource


def list_resources() -> list[dict[str, Any]]:
    cfg = load_pricing()
    return [
        get_resource(rid)  # type: ignore
        for rid in (cfg.get("resources") or {}).keys()
    ]


#: Fail-closed network allowlist for x402 payment routes.  From CDP SDK
#: 1.56.0, a route with scheme "upto" and NO explicit networks expands to
#: BOTH Base and Solana (previously Base-only).  Every route we construct
#: must therefore carry an explicit networks list; :func:`build_route_config`
#: is the only sanctioned constructor and it raises on None/empty.
X402_NETWORKS_EXPLICIT: Final = ("base",)


class MANDATES_API_WATCH:
    """Design note (no code yet): the CDP Mandates API (1.58.0) is the
    wallet-layer payment-session authorization primitive candidate for
    the session-key work item.  When that work starts, prefer
    authorizeMandatePaymentSession-scoped mandates over long-lived
    delegation grants: sessions bound to a mandate are revocable in one
    call (revokeWalletMandate) and carry explicit spend/network bounds,
    which composes with the X402_NETWORKS_EXPLICIT allowlist above.
    Tracked here so the primitive is evaluated, not silently skipped."""


def build_route_config(
    *,
    scheme: str = "exact",
    networks: Optional[Sequence[str]] = X402_NETWORKS_EXPLICIT,
) -> dict[str, Any]:
    """Build an x402 route config with explicit networks, fail closed.

    ``networks`` defaults to :data:`X402_NETWORKS_EXPLICIT`; passing
    ``None`` or an empty sequence raises ``ValueError`` -- we never rely
    on SDK defaults, which from CDP 1.56.0 expand an "upto" route with no
    networks to Base AND Solana.  All production route construction must
    go through this function.
    """
    if networks is None or len(list(networks)) == 0:
        raise ValueError(
            "x402 route networks must be explicit (got None/empty); "
            "refusing to rely on CDP SDK default network expansion"
        )
    seen: list[str] = []
    for net in networks:
        net = str(net).strip().lower()
        if not net or net in seen:
            continue
        seen.append(net)
    if not seen:
        raise ValueError("x402 route networks must be explicit (all blank)")
    return {"scheme": str(scheme), "networks": seen}


def facilitator_route(
    *,
    scheme: str = "exact",
    networks: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """Seam for the future CdpX402Client/facilitator integration.

    Intercepts ``networks=None`` BEFORE it reaches the SDK and substitutes
    :data:`X402_NETWORKS_EXPLICIT`, so the SDK's default-expansion (Base +
    Solana from 1.56.0 on an "upto" route with no networks) can never fire
    on our behalf.  Callers pass the returned ``networks`` list explicitly
    to the SDK call.
    """
    resolved: Optional[Sequence[str]] = (
        X402_NETWORKS_EXPLICIT if networks is None else networks
    )
    return build_route_config(scheme=scheme, networks=resolved)


def build_payment_payload(
    resource: dict[str, Any],
    resource_id: str,
    challenge_id: str,
    *,
    scheme: str = "exact",
) -> dict[str, Any]:
    """Build the 402 payment payload for a resource.

    The route (scheme + explicit networks) always comes from
    :func:`build_route_config`; no code path constructs a route without
    it.  ``networks`` (the full explicit list) is included alongside the
    legacy singular ``network`` field so facilitators honoring the x402
    v2 route format see the binding too.
    """
    route = build_route_config(scheme=scheme)
    return {
        "x402Version": 1,
        "scheme": route["scheme"],
        "network": route["networks"][0],
        "networks": route["networks"],
        "chainId": resource["chain_id"],
        "maxAmountRequired": resource["amount_atomic"],
        "resource": f"/x402/{resource_id}",
        "description": resource["description"],
        "payTo": resource["treasury"],
        "asset": resource["token_address"],
        "extra": {
            "challenge_id": challenge_id,
            "token": resource["token"],
            "amount_display": resource["amount_display"],
            "decimals": resource["token_decimals"],
            "skill_id": resource.get("skill_id", ""),
        },
    }


def create_challenge(resource_id: str, *, payer_wallet: str = "") -> dict[str, Any]:
    resource = get_resource(resource_id)
    if not resource:
        return {"ok": False, "error": "unknown_resource"}

    now = datetime.now(timezone.utc)
    challenge_id = f"x402-{secrets.token_hex(8)}"
    expires = now + timedelta(seconds=resource["ttl_seconds"])

    with _conn() as conn:
        conn.execute(
            """INSERT INTO x402_challenges
               (challenge_id, resource_id, amount_atomic, amount_display, payer_wallet,
                status, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (
                challenge_id,
                resource_id,
                resource["amount_atomic"],
                resource["amount_display"],
                payer_wallet.lower() if payer_wallet else "",
                now.isoformat(),
                expires.isoformat(),
            ),
        )
        conn.commit()

    payload = build_payment_payload(resource, resource_id, challenge_id)

    return {
        "ok": False,
        "http_status": 402,
        "error": "payment_required",
        "challenge_id": challenge_id,
        "expires_at": expires.isoformat(),
        "payment": payload,
        "message": (
            f"Send {resource['amount_display']} {resource['token']} "
            f"to {resource['treasury']} on Base"
        ),
    }


def verify_challenge(
    challenge_id: str,
    tx_hash: str,
    *,
    payer_wallet: str = "",
) -> dict[str, Any]:
    if not challenge_id or not tx_hash:
        return {"ok": False, "error": "challenge_id_and_tx_hash_required"}

    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM x402_challenges WHERE challenge_id=?", (challenge_id,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": "challenge_not_found"}
        if row["status"] == "fulfilled":
            return {
                "ok": True,
                "status": "already_fulfilled",
                "access_token": row["access_token"],
                "resource_id": row["resource_id"],
            }

        expires = row["expires_at"]
        if expires and datetime.fromisoformat(expires.replace("Z", "+00:00")) < datetime.now(timezone.utc):
            return {"ok": False, "error": "challenge_expired"}

    resource = get_resource(str(row["resource_id"]))
    vr = verify_treasury_transfer(
        tx_hash,
        token=str((resource or {}).get("token", "SINC")),
        expected_atomic=int(row["amount_atomic"]),
        treasury=(resource or {}).get("treasury"),
        payer_wallet=payer_wallet,
    )
    if not vr.get("ok"):
        return vr

    access_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).isoformat()

    with _conn() as conn:
        conn.execute(
            """UPDATE x402_challenges SET status='fulfilled', tx_hash=?, payer_wallet=?,
               access_token=?, fulfilled_at=? WHERE challenge_id=?""",
            (tx_hash, vr["payer_wallet"], access_token, now, challenge_id),
        )
        conn.commit()

    return {
        "ok": True,
        "status": "fulfilled",
        "challenge_id": challenge_id,
        "resource_id": row["resource_id"],
        "access_token": access_token,
        "tx_hash": tx_hash,
        "payer_wallet": vr["payer_wallet"],
    }


def finalize_challenge_payment(result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("ok") or result.get("status") != "fulfilled":
        return {}
    resource = get_resource(str(result.get("resource_id") or ""))
    if not resource:
        return {}
    token = resource["token"]
    amount_display = float(result.get("amount_display") or resource["amount_display"])
    payment = record_platform_payment(
        tx_hash=str(result.get("tx_hash") or ""),
        payer_wallet=str(result.get("payer_wallet") or ""),
        token=token,
        amount_atomic=int(result.get("amount_atomic") or resource["amount_atomic"]),
        product_name=f"x402:{resource['id']}",
        plan_id="x402",
        payment_id=str(result.get("challenge_id") or ""),
    )
    inflow = record_inflow(
        amount_display,
        asset=token,
        source="x402_payment",
        usd_estimate=amount_display,
        tx_hash=str(result.get("tx_hash") or ""),
        note=f"x402:{resource['id']}",
        projected=False,
    )
    return {
        "payment_log": payment,
        "treasury_inflow": inflow.to_dict() if hasattr(inflow, "to_dict") else {},
    }


def execute_paid_resource(resource_id: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    resource = get_resource(resource_id)
    if not resource:
        return 404, {"ok": False, "error": "unknown_resource"}
    if resource.get("skill_id"):
        from verticals.loader import instantiate_vertical_agents
        from sincor2.vertical_dispatch import dispatch_vertical_task

        input_payload = payload or {}
        input_text = json.dumps({"payload": input_payload})
        dispatched = dispatch_vertical_task(
            resource["skill_id"],
            input_text,
            {"vertical_agents": instantiate_vertical_agents()},
        )
        if not dispatched:
            return 503, {
                "ok": False,
                "error": "skill_unavailable",
                "resource": resource_id,
                "skill_id": resource["skill_id"],
            }
        output, error = dispatched
        if error:
            return 502, {
                "ok": False,
                "error": "skill_execution_failed",
                "resource": resource_id,
                "skill_id": resource["skill_id"],
                "detail": error,
            }
        try:
            execution = json.loads(output)
        except json.JSONDecodeError:
            execution = {"status": "success", "result": {"raw": output}}
        return 200, {
            "ok": True,
            "resource": resource_id,
            "skill_id": resource["skill_id"],
            "execution": execution,
        }
    return 200, {
        "ok": True,
        "resource": resource_id,
        "message": "Access granted. Resource handler may be extended per config/x402_pricing.yaml.",
    }


def access_granted(access_token: str, resource_id: str) -> bool:
    if not access_token:
        return False
    with _conn() as conn:
        row = conn.execute(
            """SELECT challenge_id FROM x402_challenges
               WHERE access_token=? AND resource_id=? AND status='fulfilled'""",
            (access_token, resource_id),
        ).fetchone()
    return bool(row)

# ---------------------------------------------------------------------------
# Settle-before-serve guard (dev-watch item 79)
# ---------------------------------------------------------------------------

#: Hard rule: a serve path may only proceed when a settle RECEIPT — not a
#: verify receipt — exists for the payment reference. Item 79 (Payload Tools
#: postmortem, 2026-10-07): sellers who served work on ``verify`` alone did
#: unpaid work on credit, because verify passed on every attempt while settle
#: rejected everything with ``invalid_payload``. Root causes were (a) the
#: token ``name`` field had to be exactly "USD Coin", not "USDC", and (b) a
#: $0.001 amount minimum — neither was in the spec (x402-foundation/x402#961).
#: Every serve path MUST call :func:`require_settled` before doing paid work.
SETTLE_BEFORE_SERVE = True

_SETTLE_OK_STATUSES = {"settled", "confirmed", "success"}

# intent_hash -> "settled" | "rejected": process-wide memory of settle
# outcomes, consulted by require_settled(). This is the nonce-reuse trap
# hedge: when settle rejects with nonce_already_used, the intent is marked
# rejected. Minting a fresh authorization for the SAME intent afterwards
# must NOT be treated as a new payment unless the fresh authorization has
# its own confirmed settle receipt — otherwise the "fix" converts a replay
# rejection into a double payment.
_INTENT_OUTCOMES: dict[str, str] = {}


def register_settle_outcome(intent_hash: str, outcome: str) -> None:
    """Record a terminal settle outcome for an intent.

    ``outcome`` must be ``"settled"`` or ``"rejected"``. A "rejected"
    intent refuses verify-only serve attempts until a fresh authorization
    for the same intent produces its own confirmed settle receipt.
    """
    if not intent_hash:
        raise ValueError("intent_hash required")
    if outcome not in ("settled", "rejected"):
        raise ValueError("outcome must be 'settled' or 'rejected'")
    _INTENT_OUTCOMES[intent_hash] = outcome


def intent_outcome(intent_hash: str) -> str | None:
    """Return the recorded settle outcome for an intent, or None."""
    return _INTENT_OUTCOMES.get(intent_hash)


def reset_intent_registry() -> None:
    """Clear the intent-outcome registry. Test helper only."""
    _INTENT_OUTCOMES.clear()


def require_settled(payment: Any) -> bool:
    """Serve-path guard: return True only if a settle receipt exists.

    ``payment`` is a payment reference dict with keys::

        {"intent_hash": str,
         "verify": {"ok": bool, ...},
         "settle": {"ok": bool, "status": str, "reason": str, ...}}

    Rules:
    - ``SETTLE_BEFORE_SERVE`` must be True (fail closed if ever weakened).
    - verify alone is NEVER enough — verify passed on every attempt in the
      item-79 postmortem while settle rejected everything.
    - A confirmed settle receipt (``ok`` True + settled/confirmed/success
      status) approves and records the intent as settled.
    - A rejected settle marks the intent rejected and returns False.
    - A verify-only reference whose intent was previously rejected (the
      nonce-reuse trap: fresh auth minted after ``nonce_already_used``
      without confirming the first settle) returns False. It can only be
      approved by producing its own confirmed settle receipt.
    """
    if SETTLE_BEFORE_SERVE is not True:
        return False
    if not isinstance(payment, dict):
        return False
    intent = str(payment.get("intent_hash") or "")
    verify = payment.get("verify") or {}
    if not isinstance(verify, dict) or verify.get("ok") is not True:
        return False
    settle = payment.get("settle") or {}
    if isinstance(settle, dict) and settle:
        status = str(settle.get("status") or "").lower()
        if settle.get("ok") is True and status in _SETTLE_OK_STATUSES:
            if intent:
                _INTENT_OUTCOMES[intent] = "settled"
            return True
        # A settle receipt exists but is not successful: record the
        # rejection so the intent cannot be re-served on verify alone.
        if intent:
            _INTENT_OUTCOMES[intent] = "rejected"
        return False
    # Verify-only reference: blocked by the intent registry if a previous
    # settle for this intent was rejected (nonce-reuse trap); blocked in
    # all other cases too — verify is not settlement.
    if intent and _INTENT_OUTCOMES.get(intent) == "rejected":
        return False
    return False


# ---------------------------------------------------------------------------
# Batch-settlement vouchers (dev-watch item 81)
# ---------------------------------------------------------------------------

# VOUCHERS ARE PROMISES, NOT SETTLEMENT.
#
# Coinbase-style x402 batch settlement (2026-10-06): buyers deposit ERC-20
# into onchain escrow and sign offchain vouchers per request; sellers verify
# vouchers, serve, and redeem batched later. This section covers the offchain
# voucher half ONLY. Batch settlement does NOT relax settle-before-serve:
# ``redeem_voucher()`` records an intent to include a voucher in a future
# batch — it creates NO settle receipt. A serve path must still call
# ``require_settled()`` and see a confirmed batch-settlement receipt before
# serving voucher-backed work.
#
# Replay surface: a voucher replayed across redemption batches, and
# voucher-vs-settlement races. Mitigations: nonce uniqueness enforced
# process-wide (``_REDEEMED_NONCES``), idempotent redemption (re-redeeming
# returns the original record and never double-pays), expiry enforcement.

@dataclass
class Voucher:
    """Offchain payment voucher for batch settlement (a promise, not money)."""

    voucher_id: str
    payer: str
    pay_to: str
    amount: float
    token: str
    nonce: str
    expiry_ts: float
    intent_hash: str


# nonce -> redeemed: process-wide redemption memory. A nonce may be redeemed
# exactly once across all batches.
_REDEEMED_NONCES: set[str] = set()

# voucher_id -> redemption record: idempotency memory for redeem_voucher().
_REDEMPTION_RECORDS: dict[str, dict[str, Any]] = {}


def reset_voucher_registry() -> None:
    """Clear voucher redemption memory. Test helper only."""
    _REDEEMED_NONCES.clear()
    _REDEMPTION_RECORDS.clear()


def validate_voucher(voucher: Any) -> dict[str, Any]:
    """Validate a voucher without redeeming it.

    Enforces: nonce present and never previously redeemed (across all
    batches), expiry_ts in the future, amount > 0, payer/pay_to/intent_hash
    present. Returns ``{"ok": True}`` or ``{"ok": False, "error": ...}``.
    """
    if not isinstance(voucher, Voucher):
        return {"ok": False, "error": "not_a_voucher"}
    if not voucher.nonce:
        return {"ok": False, "error": "nonce_required"}
    if voucher.nonce in _REDEEMED_NONCES:
        return {"ok": False, "error": "nonce_already_redeemed"}
    if not voucher.payer:
        return {"ok": False, "error": "payer_required"}
    if not voucher.pay_to:
        return {"ok": False, "error": "pay_to_required"}
    if not voucher.intent_hash:
        return {"ok": False, "error": "intent_hash_required"}
    if voucher.amount is None or voucher.amount <= 0:
        return {"ok": False, "error": "invalid_amount"}
    if voucher.expiry_ts <= time.time():
        return {"ok": False, "error": "voucher_expired"}
    return {"ok": True}


def redeem_voucher(voucher: Voucher, *, batch_id: str = "") -> dict[str, Any]:
    """Idempotently redeem a voucher for batch settlement.

    First redemption of a ``voucher_id`` validates the voucher, consumes its
    nonce, and records the redemption. Re-redeeming the same voucher (e.g.
    replayed across batches) returns the ORIGINAL redemption record with
    ``error: "already_redeemed"`` and ``double_pay: False`` — it never
    double-pays.

    The redemption record carries ``"settled": False`` deliberately:
    redemption is batch inclusion intent, NOT settlement. Serving the
    voucher-backed work still requires a confirmed batch-settlement receipt
    via :func:`require_settled`.
    """
    if not isinstance(voucher, Voucher):
        return {"ok": False, "error": "not_a_voucher", "double_pay": False}
    if voucher.voucher_id in _REDEMPTION_RECORDS:
        original = _REDEMPTION_RECORDS[voucher.voucher_id]
        return {
            "ok": False,
            "error": "already_redeemed",
            "double_pay": False,
            "redemption": dict(original),
        }
    check = validate_voucher(voucher)
    if not check.get("ok"):
        return {"ok": False, "error": check["error"], "double_pay": False}
    record = {
        "voucher_id": voucher.voucher_id,
        "payer": voucher.payer,
        "pay_to": voucher.pay_to,
        "amount": voucher.amount,
        "token": voucher.token,
        "nonce": voucher.nonce,
        "intent_hash": voucher.intent_hash,
        "batch_id": batch_id,
        "redeemed_at": datetime.now(timezone.utc).isoformat(),
        "settled": False,  # redemption is NOT settlement; see module docstring
    }
    _REDEMPTION_RECORDS[voucher.voucher_id] = record
    _REDEEMED_NONCES.add(voucher.nonce)
    return {"ok": True, "redemption": dict(record), "settle_pending": True}
