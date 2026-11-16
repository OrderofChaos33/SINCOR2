"""Sybil & Campaign Verification-as-a-Service (VaaS) — external surface.

Path 49 (52 money paths): packaging of the LIVE internal anti-gaming
machinery in :mod:`sincor2.axm_antigaming` as an outside-facing,
authenticated, metered API. This module adds NO new detection logic:
every verdict is produced by importing and calling the exact functions
the A2A inbound market enforces today
(``axm_antigaming.check_bid_eligibility``, which includes
``check_self_bid``). The internal enforcement path is untouched.

WHAT THE SERVICE CHECKS (sellable description)
----------------------------------------------
Given a campaign task (poster + bounty) and a participant / bidder,
VaaS answers one question: *would this bid be admitted by SINCOR's own
live marketplace anti-gaming guards right now?* That covers:

* self-bid / self-dealing (a participant bidding on its own campaign,
  including wallet-principal self-bids and anonymous bounty tasks
  where self-dealing cannot be ruled out),
* participation domination (rolling-window entry / win caps),
* collusion rotation (consecutive-win and poster/winner-pair caps,
  including the non-consecutive alternation breaker).

ENDPOINT CONTRACT
-----------------
Mount: ``app.register_blueprint(vaas_bp)`` (prefix ``/v1/vaas``);
:func:`register_vaas` is the idempotent app-factory helper.

* ``GET  /v1/vaas/health``  — public, ``{ok, service, version}`` only.
* ``GET  /v1/vaas/pricing`` — public tariff + service description.
* ``POST /v1/vaas/check``   — auth required. Body:
  ``{"task": {"task_id": ..., "poster_id": ..., "bounty_axm": ...},
     "participant_id": "<agent id>"}``
  (``agent_id`` accepted as an alias for ``participant_id``.)
* ``POST /v1/vaas/campaign/check`` — auth required. Body:
  ``{"campaign_id": ..., "task": {...},
     "participants": ["<agent id>", ...]}``
  Returns one verdict per participant plus verdict counts for the
  batch (the buyer's own submitted data).

Verdict shape (verdict-level detail ONLY — see anti-gaming note):
``{"participant_id": ..., "eligible": bool,
   "verdict": "eligible"|"ineligible"|"unknown",
   "reason_code": "none"|"self_bid"|"anonymous_bounty"|
                  "participation_cap"|"collusion_cap"|
                  "guard_unavailable",
   "risk_band": "low"|"high"|"unknown"}``

ANTI-GAMING-OF-THE-API (deliberate non-exposure)
------------------------------------------------
Responses NEVER include internal rule thresholds, caps, window sizes,
streak/window counts, weights, or the internal exception messages
(those messages embed cap values). A caller learns only the verdict
for the exact participant it submitted — the same information the live
market reveals by accepting or rejecting a real bid — so the API does
not teach an attacker how close they are to a cap or how to tune an
evasion. Pricing is public by design; detection parameters are not.

AUTH
----
Existing SINCOR API-key patterns, fail closed: the caller presents
``X-Api-Key: <key>`` or ``Authorization: Bearer <key>`` where the key
matches one of the comma-separated keys in env ``VAAS_API_KEYS``
(constant-time comparison), or the shared operator credential accepted
by :func:`sincor2.admin_api_key.check_admin_header_key`. When no key
is configured anywhere, every check request is rejected (401).

METERING + PRICING
------------------
Every participant check appends exactly ONE entry to the VaaS metering
ledger (append-only JSONL, following the ``agent_billing`` /
``SINCMeter`` ledger pattern in this codebase) recording the caller
(a SHA-256 fingerprint of the API key — never the raw key), campaign /
task id, participant, verdict, and the tariff charged. The x402 rail
was considered and deliberately NOT used for v1: x402 in this codebase
is a per-resource on-chain settlement challenge for anonymous wallet
payers, whereas VaaS is an authenticated B2B API-key service whose
buyers are invoiced against the metering ledger — the same
ledger-first pattern as the platform's other metered surfaces. The
tariff constant below is the single pricing hook an x402 challenge or
an invoice run reads from.

FOUNDER-ADJUSTABLE TARIFF: :data:`VAAS_PRICE_PER_CHECK_USD` — see its
comment. Checks are READ-ONLY against the anti-gaming store: VaaS
never records bids/wins, so querying the service cannot consume a
participant's quota or alter live market state (the guards' own
rejection audit logging on a rejected check is the guards' existing
behaviour, identical on the internal path).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

from sincor2 import axm_antigaming as ag

logger = logging.getLogger("sincor.vaas")

# ---------------------------------------------------------------------------
# Pricing — THE single tariff constant (FOUNDER-ADJUSTABLE).
#
# $0.05 USDC per participant check. Rationale: the codebase's cheapest
# paid verification precedent, the x402 quick-scan, is $0.01 for a
# trivial scan; a VaaS check runs the full multi-guard eligibility
# evaluation plus state lookups, priced at 5x that micro-call floor,
# while staying two orders of magnitude under the $5.00 credential
# check so campaign-scale batches stay cheap (1,000 participants =
# $50.00). Override per deployment with env SINCOR_VAAS_PRICE_USD.
# ---------------------------------------------------------------------------
VAAS_PRICE_PER_CHECK_USD = 0.05
VAAS_CURRENCY = "USDC"
VAAS_SERVICE_NAME = "SINCOR Sybil & Campaign Verification (VaaS)"
VAAS_VERSION = "1.0"


def price_per_check_usd() -> float:
    try:
        return float(os.environ.get("SINCOR_VAAS_PRICE_USD", "") or VAAS_PRICE_PER_CHECK_USD)
    except (TypeError, ValueError):
        return VAAS_PRICE_PER_CHECK_USD


vaas_bp = Blueprint("vaas", __name__, url_prefix="/v1/vaas")


# ---------------------------------------------------------------------------
# Auth (existing SINCOR API-key patterns; fail closed)
# ---------------------------------------------------------------------------

def _configured_vaas_keys() -> List[str]:
    raw = os.environ.get("VAAS_API_KEYS", "") or ""
    return [k.strip() for k in raw.split(",") if len(k.strip()) >= 8]


def _caller_key() -> str:
    return (
        (request.headers.get("X-Api-Key") or "").strip()
        or (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    )


def _key_fingerprint(key: str) -> str:
    return "key_" + hashlib.sha256(key.encode()).hexdigest()[:16]


def _authenticated() -> Optional[str]:
    """Return the caller fingerprint when authenticated, else None."""
    provided = _caller_key()
    if not provided:
        return None
    for configured in _configured_vaas_keys():
        try:
            if hmac.compare_digest(provided, configured):
                return _key_fingerprint(provided)
        except (TypeError, ValueError):
            return None
    # Shared operator credential (same helper every admin surface uses).
    try:
        from sincor2.admin_api_key import check_admin_header_key

        if check_admin_header_key(provided):
            return _key_fingerprint(provided)
    except Exception:
        pass
    return None


def _unauthorized():
    return jsonify({"error": "unauthorized", "status": 401}), 401


# ---------------------------------------------------------------------------
# Metering ledger (append-only JSONL; one entry per participant check)
# ---------------------------------------------------------------------------

class VaasMeter:
    """Append-only per-check metering ledger (agent_billing pattern)."""

    def __init__(self, log_path: Optional[str] = None) -> None:
        env_path = os.environ.get("SINCOR_VAAS_LEDGER_PATH", "").strip()
        self._log_path: Optional[Path] = (
            Path(log_path) if log_path else (Path(env_path) if env_path else None)
        )
        self._events: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def record(self, *, caller: str, campaign_id: str, task_id: str,
               participant_id: str, verdict: str) -> Dict[str, Any]:
        entry = {
            "ts_ms": int(time.time() * 1000),
            "type": "vaas_check",
            "caller": caller,
            "campaign_id": str(campaign_id or ""),
            "task_id": str(task_id or ""),
            "participant_id": str(participant_id),
            "verdict": verdict,
            "price_usd": price_per_check_usd(),
            "currency": VAAS_CURRENCY,
        }
        with self._lock:
            self._events.append(entry)
            if self._log_path:
                try:
                    self._log_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(self._log_path, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(entry) + "\n")
                except OSError as exc:
                    logger.warning("vaas meter write failed: %s", exc)
        return entry

    def entries(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._events]


_METER = VaasMeter()


def vaas_meter() -> VaasMeter:
    return _METER


def reset_vaas_meter(log_path: Optional[str] = None) -> VaasMeter:
    """Test hook: swap the process-wide meter for an isolated one."""
    global _METER
    _METER = VaasMeter(log_path=log_path)
    return _METER


def _default_ledger_path() -> Optional[str]:
    if os.environ.get("SINCOR_VAAS_LEDGER_PATH", "").strip():
        return None  # env already honoured by VaasMeter
    try:
        from sincor2.data_paths import data_dir

        return str(data_dir() / "vaas_metering.jsonl")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Core verification — a thin verdict translator over the LIVE guards.
# No detection logic lives here; do not add any.
# ---------------------------------------------------------------------------

def verify_participant(task: Dict[str, Any], participant_id: str) -> Dict[str, Any]:
    """Run the exact internal eligibility gate for one participant and
    translate the outcome to a verdict-level payload (no thresholds,
    counts, weights, or internal messages — see module docstring)."""
    participant_id = str(participant_id)
    try:
        ag.check_bid_eligibility(task, participant_id)
    except ag.AnonymousBountyError:
        return {"participant_id": participant_id, "eligible": False,
                "verdict": "ineligible", "reason_code": "anonymous_bounty",
                "risk_band": "high"}
    except ag.SelfBidError:
        return {"participant_id": participant_id, "eligible": False,
                "verdict": "ineligible", "reason_code": "self_bid",
                "risk_band": "high"}
    except ag.ParticipationCapError:
        return {"participant_id": participant_id, "eligible": False,
                "verdict": "ineligible", "reason_code": "participation_cap",
                "risk_band": "high"}
    except ag.CollusionCapError:
        return {"participant_id": participant_id, "eligible": False,
                "verdict": "ineligible", "reason_code": "collusion_cap",
                "risk_band": "high"}
    except ag.GuardUnavailableError:
        return {"participant_id": participant_id, "eligible": False,
                "verdict": "unknown", "reason_code": "guard_unavailable",
                "risk_band": "unknown"}
    return {"participant_id": participant_id, "eligible": True,
            "verdict": "eligible", "reason_code": "none",
            "risk_band": "low"}


def _clean_task(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    task: Dict[str, Any] = {
        "task_id": str(raw.get("task_id") or raw.get("campaign_id") or ""),
        "poster_id": raw.get("poster_id"),
    }
    if raw.get("bounty_axm") is not None:
        task["bounty_axm"] = raw.get("bounty_axm")
    return task


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@vaas_bp.get("/health")
def vaas_health():
    return jsonify({"ok": True, "service": "vaas", "version": VAAS_VERSION})


@vaas_bp.get("/pricing")
def vaas_pricing():
    return jsonify({
        "service": VAAS_SERVICE_NAME,
        "version": VAAS_VERSION,
        "price_per_check_usd": price_per_check_usd(),
        "currency": VAAS_CURRENCY,
        "unit": "one participant verification against one campaign task",
        "checks": ["self_bid", "anonymous_bounty", "participation_caps",
                   "collusion_rotation"],
        "endpoints": {"check": "POST /v1/vaas/check",
                      "campaign_check": "POST /v1/vaas/campaign/check"},
        "auth": "X-Api-Key or Authorization: Bearer <key>",
    })


@vaas_bp.post("/check")
def vaas_check():
    caller = _authenticated()
    if caller is None:
        return _unauthorized()
    body = request.get_json(silent=True) or {}
    task = _clean_task(body.get("task"))
    participant = str(body.get("participant_id") or body.get("agent_id") or "").strip()
    if task is None or not participant:
        return jsonify({"error": "task and participant_id are required",
                        "status": 400}), 400
    result = verify_participant(task, participant)
    vaas_meter().record(caller=caller, campaign_id=str(body.get("campaign_id") or ""),
                        task_id=task["task_id"], participant_id=participant,
                        verdict=result["verdict"])
    return jsonify({**result, "price_usd": price_per_check_usd(),
                    "currency": VAAS_CURRENCY})


@vaas_bp.post("/campaign/check")
def vaas_campaign_check():
    caller = _authenticated()
    if caller is None:
        return _unauthorized()
    body = request.get_json(silent=True) or {}
    task = _clean_task(body.get("task"))
    raw_participants = body.get("participants") or body.get("bids") or []
    participants: List[str] = []
    for item in raw_participants:
        if isinstance(item, dict):
            pid = item.get("participant_id") or item.get("agent_id")
        else:
            pid = item
        if pid:
            participants.append(str(pid))
    if task is None or not participants:
        return jsonify({"error": "task and participants are required",
                        "status": 400}), 400
    if len(participants) > 1000:
        return jsonify({"error": "batch too large (max 1000 participants)",
                        "status": 400}), 400
    campaign_id = str(body.get("campaign_id") or task["task_id"] or "")
    results = []
    for participant in participants:
        result = verify_participant(task, participant)
        vaas_meter().record(caller=caller, campaign_id=campaign_id,
                            task_id=task["task_id"], participant_id=participant,
                            verdict=result["verdict"])
        results.append(result)
    summary = {"eligible": sum(1 for r in results if r["verdict"] == "eligible"),
               "ineligible": sum(1 for r in results if r["verdict"] == "ineligible"),
               "unknown": sum(1 for r in results if r["verdict"] == "unknown")}
    return jsonify({
        "campaign_id": campaign_id,
        "task_id": task["task_id"],
        "results": results,
        "summary": summary,
        "checks": len(results),
        "price_per_check_usd": price_per_check_usd(),
        "total_price_usd": round(price_per_check_usd() * len(results), 4),
        "currency": VAAS_CURRENCY,
    })


def register_vaas(app) -> bool:
    """Idempotent registration for the app factory (kya pattern)."""
    if "vaas" in (getattr(app, "blueprints", {}) or {}):
        return False
    global _METER
    if _METER._log_path is None:
        default = _default_ledger_path()
        if default:
            _METER = VaasMeter(log_path=default)
    app.register_blueprint(vaas_bp)
    return True
