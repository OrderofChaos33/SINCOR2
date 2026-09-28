"""Bankruptcy recovery track — deterministic, durable, enterprise-grade.

Problem: a slashed agent sits at zero stake and zero reputation. Today the
only way back is its human operator re-funding it with fresh AXM. This
module defines the platform's recovery policy as LOCKED, IDEMPOTENT rules:

* Eligibility is a pure function of evidence + wallet history. Same inputs
  -> same verdict, every time. No discretion, no adaptive parameters, no
  case-by-case calls. Changing the policy is a code change, not a knob.
* Only HONEST_FAIL bankruptcies qualify: the agent committed, revealed,
  and submitted on time, but lost or failed. Classification is
  deterministic from auction evidence.
* Ghosts NEVER qualify. Committed-but-never-revealed/submitted is the
  tombstoned pattern; the KYA layer tombstones those wallets and any
  tombstoned wallet is ineligible here. The platform does not front money
  to wallets it has killed.
* Fixed escalation ladder per wallet (strikes follow the wallet, not the
  agent_id — the whitewash lesson):
      strike 1: front up to STANDARD_CAP_AXM, no cooldown
      strike 2: front up to 50% of cap, 7-day cooldown since strike 1
      strike 3+: permanently ineligible
* Fronting reuses the sponsored-stake ledger: one active sponsorship per
  agent, automatic recoup from earnings (fronted -> recouping -> settled).
  Recovery adds the classification gate, the tombstone gate, and the
  ladder on top. Disabling the track stops new sponsorships; it never
  forgives outstanding ones (same guarantee as sponsored stake).

Durability: JSON ledger with atomic writes (tmp + os.replace) under the
persistent data dir (SINCOR_DATA_DIR, i.e. the /data volume), so strike
history survives restarts and redeploys. Append-only event journal.

Operator opt-in: SINCOR_RECOVERY_TRACK_ENABLED=1 (default off), and the
sponsored-stake mechanism must also be enabled — it performs the front.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("sincor.market.recovery")

ENABLE_ENV = "SINCOR_RECOVERY_TRACK_ENABLED"
LEDGER_ENV = "SINCOR_RECOVERY_LEDGER"

AXM_WEI = 10 ** 18

# --- Locked policy ---------------------------------------------------------
# These are constants, not configuration. Changing them is a policy change:
# it ships as code, reviewed and deployed, never as a live parameter tweak.
# Rationale for the cap: covers KYA re-verification (10 AXM) plus a buffer
# for one task-stake cycle at current bounty sizes. Recoup is automatic
# from earnings, so an unused buffer simply returns faster.
STANDARD_CAP_AXM = 25.0
LADDER = {
    1: {"cap_axm": STANDARD_CAP_AXM, "cooldown_days": 0},
    2: {"cap_axm": STANDARD_CAP_AXM * 0.5, "cooldown_days": 7},
}
MAX_STRIKES = 2  # strike 3+ -> permanently ineligible
DAY_MS = 24 * 60 * 60 * 1000

# --- Failure classification --------------------------------------------------
VERDICT_HONEST_FAIL = "honest_fail"
VERDICT_GHOST = "ghost"
VERDICT_NO_STAKE = "no_stake"


def classify_failure(committed: bool, revealed: bool,
                     submitted_on_time: bool) -> Dict[str, Any]:
    """Deterministic failure classification from auction evidence.

    honest_fail: committed + revealed + submitted on time, but lost/failed.
        Eligible for sponsored recovery.
    ghost: committed but never revealed or never submitted on time. The
        tombstoned pattern. NEVER eligible.
    no_stake: never committed, so nothing was slashed. Nothing to recover.
    """
    committed, revealed = bool(committed), bool(submitted_on_time and revealed)
    submitted = bool(submitted_on_time)
    if not committed:
        return {"verdict": VERDICT_NO_STAKE,
                "reasons": ["agent never committed stake; nothing was slashed"]}
    if not revealed or not submitted:
        reasons = []
        if not revealed:
            reasons.append("committed but never revealed")
        if not submitted:
            reasons.append("committed but never submitted on time")
        return {"verdict": VERDICT_GHOST, "reasons": reasons}
    return {"verdict": VERDICT_HONEST_FAIL,
            "reasons": ["committed, revealed, and submitted on time; "
                        "bankruptcy was honest failure, not abandonment"]}


# --- errors -------------------------------------------------------------------
class RecoveryDisabled(RuntimeError):
    """Raised when sponsorship is attempted while the track is off."""


class RecoveryIneligible(RuntimeError):
    """Deterministic ineligibility. Carries a machine-readable reason code."""

    def __init__(self, reason_code: str, detail: str = "",
                 retry_after_ms: Optional[int] = None):
        super().__init__(detail or reason_code)
        self.reason_code = reason_code
        self.detail = detail or reason_code
        self.retry_after_ms = retry_after_ms


# --- enablement -----------------------------------------------------------------
def recovery_enabled() -> bool:
    """True only when the operator explicitly enabled the recovery track."""
    return os.environ.get(ENABLE_ENV, "").strip().lower() in (
        "1", "true", "yes", "on")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _default_ledger_path() -> str:
    override = os.environ.get(LEDGER_ENV, "").strip()
    if override:
        return override
    from sincor2.data_paths import data_dir
    return str(data_dir() / "recovery_ledger.json")


# --- tombstone gate ---------------------------------------------------------------
def wallet_tombstoned(wallet: str) -> bool:
    """Fail-closed tombstone check: any tombstoned wallet is ineligible.

    Tombstones are written by the KYA layer on revoke and on ghost-flagging.
    If the check itself cannot run, sponsorship is refused rather than
    granted blind.
    """
    try:
        from sincor2 import kya_registry
        check = getattr(kya_registry, "is_wallet_tombstoned", None)
        if check is None:  # pragma: no cover - defensive
            logger.error("recovery: kya_registry.is_wallet_tombstoned missing; "
                         "refusing sponsorship")
            return True
        return bool(check(wallet))
    except Exception as exc:  # fail closed
        logger.error("recovery: tombstone check failed (%s); refusing", exc)
        return True


# --- ledger -------------------------------------------------------------------------
class RecoveryLedger:
    """Durable per-wallet strike history + idempotency registry.

    State shape:
      {"wallets": {wallet_l: {"strikes": int,
                              "sponsorships": [sponsorship...],
                              "last_sponsored_at_ms": int|None}},
       "events": [append-only journal, capped]}
    A sponsorship is idempotent on bankruptcy_event_id: the same event can
    never consume two strikes.
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path or _default_ledger_path()
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {"wallets": {}, "events": []}
        self._load()

    # -- persistence ------------------------------------------------------
    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    if isinstance(raw.get("wallets"), dict):
                        self._data["wallets"] = raw["wallets"]
                    if isinstance(raw.get("events"), list):
                        self._data["events"] = raw["events"]
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("recovery ledger load failed (%s); "
                               "starting empty", exc)

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2)
        os.replace(tmp, self.path)

    def _event(self, kind: str, **detail: Any) -> None:
        self._data["events"].append(
            {"kind": kind, "at_ms": _now_ms(), **detail})
        if len(self._data["events"]) > 5000:
            self._data["events"] = self._data["events"][-5000:]

    # -- reads ------------------------------------------------------------
    def wallet_state(self, wallet: str) -> Dict[str, Any]:
        w = self._data["wallets"].get((wallet or "").lower())
        if not w:
            return {"strikes": 0, "sponsorships": [],
                    "last_sponsored_at_ms": None}
        return {"strikes": int(w.get("strikes", 0)),
                "sponsorships": list(w.get("sponsorships", [])),
                "last_sponsored_at_ms": w.get("last_sponsored_at_ms")}

    def find_by_event(self, bankruptcy_event_id: str) -> Optional[Dict[str, Any]]:
        for w in self._data["wallets"].values():
            for s in w.get("sponsorships", []):
                if s.get("bankruptcy_event_id") == bankruptcy_event_id:
                    return dict(s)
        return None

    # -- writes -----------------------------------------------------------
    def record_sponsorship(self, wallet: str, sponsorship: Dict[str, Any]
                           ) -> Dict[str, Any]:
        with self._lock:
            key = (wallet or "").lower()
            w = self._data["wallets"].setdefault(
                key, {"strikes": 0, "sponsorships": [],
                      "last_sponsored_at_ms": None})
            w["strikes"] = int(w.get("strikes", 0)) + 1
            w.setdefault("sponsorships", []).append(dict(sponsorship))
            w["last_sponsored_at_ms"] = _now_ms()
            self._event("sponsored", wallet=key,
                        strike=w["strikes"],
                        bankruptcy_event_id=sponsorship.get(
                            "bankruptcy_event_id"),
                        agent_id=sponsorship.get("agent_id"),
                        cap_wei=sponsorship.get("cap_wei"))
            self._save()
            return {"strikes": w["strikes"],
                    "last_sponsored_at_ms": w["last_sponsored_at_ms"]}


# --- process-wide singleton (overridable in tests) -------------------------------
_LEDGER: Optional[RecoveryLedger] = None


def recovery_ledger(path: Optional[str] = None) -> RecoveryLedger:
    global _LEDGER
    if _LEDGER is None or path is not None:
        _LEDGER = RecoveryLedger(path=path)
    return _LEDGER


def reset_recovery_ledger(path: Optional[str] = None) -> RecoveryLedger:
    global _LEDGER
    _LEDGER = RecoveryLedger(path=path)
    return _LEDGER


# --- eligibility --------------------------------------------------------------------
def _terms_for_strike(strike: int) -> Dict[str, Any]:
    t = LADDER[strike]
    return {"strike": strike, "cap_axm": t["cap_axm"],
            "cap_wei": str(int(t["cap_axm"] * AXM_WEI)),
            "cooldown_days": t["cooldown_days"]}


def check_eligibility(wallet: str, agent_id: str,
                      evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic eligibility verdict. Reads state, writes nothing.

    Returns {"eligible": bool, "reason_code": str, "detail": str,
             "strike": int|None, "terms": {...}|None,
             "retry_after_ms": int|None}.
    """
    from sincor2.sponsored_stake import (STATUS_FRONTED, STATUS_RECOUPING,
                                         sponsored_ledger,
                                         sponsored_stake_enabled)

    wallet_l = (wallet or "").strip().lower()
    agent_id = (agent_id or "").strip()
    if not wallet_l or not agent_id:
        return {"eligible": False, "reason_code": "bad_request",
                "detail": "wallet and agent_id are required",
                "strike": None, "terms": None, "retry_after_ms": None}

    classification = classify_failure(
        evidence.get("committed"), evidence.get("revealed"),
        evidence.get("submitted_on_time"))
    verdict = classification["verdict"]
    if verdict == VERDICT_GHOST:
        return {"eligible": False, "reason_code": "ghost_ineligible",
                "detail": "ghost pattern (" + "; ".join(
                    classification["reasons"]) + "); ghosts are tombstoned "
                "and never eligible for sponsored recovery",
                "strike": None, "terms": None, "retry_after_ms": None,
                "classification": classification}
    if verdict == VERDICT_NO_STAKE:
        return {"eligible": False, "reason_code": "no_stake_nothing_to_recover",
                "detail": classification["reasons"][0],
                "strike": None, "terms": None, "retry_after_ms": None,
                "classification": classification}

    if wallet_tombstoned(wallet_l):
        return {"eligible": False, "reason_code": "tombstoned_wallet",
                "detail": "wallet carries a KYA tombstone (revoked or "
                "ghost-flagged); the platform does not front recovery "
                "stake to tombstoned wallets",
                "strike": None, "terms": None, "retry_after_ms": None,
                "classification": classification}

    existing = sponsored_ledger().status_of(agent_id)
    if existing and existing.get("status") in (STATUS_FRONTED,
                                               STATUS_RECOUPING):
        return {"eligible": False, "reason_code": "active_sponsorship",
                "detail": f"agent already has an active sponsorship "
                f"({existing['status']})",
                "strike": None, "terms": None, "retry_after_ms": None,
                "classification": classification}

    state = recovery_ledger().wallet_state(wallet_l)
    strikes = state["strikes"]
    if strikes >= MAX_STRIKES:
        return {"eligible": False, "reason_code": "strike_limit_reached",
                "detail": f"wallet has {strikes} prior recovery "
                "sponsorships; the ladder is exhausted and the wallet is "
                "permanently ineligible",
                "strike": None, "terms": None, "retry_after_ms": None,
                "classification": classification}

    strike = strikes + 1
    terms = _terms_for_strike(strike)
    retry_after_ms = None
    if terms["cooldown_days"] and state["last_sponsored_at_ms"]:
        wait_ms = (terms["cooldown_days"] * DAY_MS
                   - (_now_ms() - int(state["last_sponsored_at_ms"])))
        if wait_ms > 0:
            retry_after_ms = wait_ms
            return {"eligible": False, "reason_code": "cooldown_active",
                    "detail": f"strike-{strike} terms require a "
                    f"{terms['cooldown_days']}-day cooldown since the last "
                    "recovery sponsorship",
                    "strike": strike, "terms": terms,
                    "retry_after_ms": retry_after_ms,
                    "classification": classification}

    if not sponsored_stake_enabled():
        # Checked last so the verdict still reports the real policy reason
        # when the mechanism is off; sponsor() raises before reaching here.
        return {"eligible": False, "reason_code": "sponsor_mechanism_disabled",
                "detail": "sponsored-stake mechanism is disabled",
                "strike": strike, "terms": terms, "retry_after_ms": None,
                "classification": classification}

    return {"eligible": True, "reason_code": "ok",
            "detail": f"eligible under strike-{strike} terms",
            "strike": strike, "terms": terms, "retry_after_ms": None,
            "classification": classification}


# --- sponsorship ----------------------------------------------------------------------
def sponsor_recovery(agent_id: str, wallet: str, bankruptcy_event_id: str,
                     evidence: Dict[str, Any],
                     approved_by: str = "admin") -> Dict[str, Any]:
    """Front recovery stake for an honestly-bankrupt agent. Idempotent.

    The same bankruptcy_event_id always returns the same record
    (dedupe=True on repeat) and never consumes an extra strike.
    Raises RecoveryDisabled while the track is off, RecoveryIneligible with
    a reason_code otherwise.
    """
    from sincor2.sponsored_stake import front_sponsored_stake

    if not recovery_enabled():
        raise RecoveryDisabled(
            f"recovery track is disabled; set {ENABLE_ENV}=1 to enable")
    agent_id = (agent_id or "").strip()
    wallet = (wallet or "").strip()
    bankruptcy_event_id = (bankruptcy_event_id or "").strip()
    if not agent_id or not wallet or not bankruptcy_event_id:
        raise ValueError("agent_id, wallet, and bankruptcy_event_id "
                         "are required")
    evidence = dict(evidence or {})

    ledger = recovery_ledger()
    prior = ledger.find_by_event(bankruptcy_event_id)
    if prior is not None:
        return {**prior, "dedupe": True}

    verdict = check_eligibility(wallet, agent_id, evidence)
    if not verdict["eligible"]:
        raise RecoveryIneligible(
            verdict["reason_code"], verdict["detail"],
            retry_after_ms=verdict.get("retry_after_ms"))

    terms = verdict["terms"]
    cap_wei = int(terms["cap_wei"])
    fronted = front_sponsored_stake(
        agent_id, cap_wei, approved_by=f"recovery:{approved_by}")
    sponsorship = {
        "bankruptcy_event_id": bankruptcy_event_id,
        "agent_id": agent_id,
        "wallet": wallet.lower(),
        "strike": verdict["strike"],
        "cap_axm": terms["cap_axm"],
        "cap_wei": terms["cap_wei"],
        "fronted_at_ms": _now_ms(),
        "fronted_status": fronted.get("status"),
        "approved_by": approved_by,
        "evidence": {k: bool(evidence.get(k)) for k in
                     ("committed", "revealed", "submitted_on_time")},
    }
    wallet_state = ledger.record_sponsorship(wallet, sponsorship)
    return {**sponsorship, "strikes": wallet_state["strikes"],
            "dedupe": False}


def recovery_status(wallet: str) -> Dict[str, Any]:
    """Durable per-wallet recovery standing: strikes, history, next terms."""
    wallet_l = (wallet or "").strip().lower()
    state = recovery_ledger().wallet_state(wallet_l)
    strikes = state["strikes"]
    if strikes >= MAX_STRIKES:
        nxt: Dict[str, Any] = {"eligible": False,
                               "reason_code": "strike_limit_reached",
                               "terms": None}
    else:
        nxt = {"eligible": True, "reason_code": "ok",
               "terms": _terms_for_strike(strikes + 1)}
        terms = nxt["terms"]
        assert isinstance(terms, dict)
        if terms["cooldown_days"] and state["last_sponsored_at_ms"]:
            wait_ms = (terms["cooldown_days"] * DAY_MS
                       - (_now_ms() - int(state["last_sponsored_at_ms"])))
            nxt["cooldown_retry_after_ms"] = max(0, wait_ms)
    return {"wallet": wallet_l, "strikes": strikes,
            "tombstoned": wallet_tombstoned(wallet_l),
            "sponsorships": state["sponsorships"],
            "last_sponsored_at_ms": state["last_sponsored_at_ms"],
            "next": nxt,
            "ladder": {str(k): {"cap_axm": v["cap_axm"],
                                "cooldown_days": v["cooldown_days"]}
                       for k, v in LADDER.items()},
            "max_strikes": MAX_STRIKES}


# --- HTTP -------------------------------------------------------------------------------
def attach_recovery_routes(bp: Any) -> None:
    """Mount the admin-gated recovery routes on an A2A blueprint."""
    from flask import jsonify, request

    from sincor2.a2a_inbound import _http_error
    from sincor2.sponsored_stake import _admin_key_ok

    @bp.post("/v1/a2a/admin/recovery/sponsor")
    def v1_recovery_sponsor():
        """Front recovery stake for an honestly-bankrupt agent (admin only).

        Body: {agent_id, wallet, bankruptcy_event_id,
               evidence: {committed, revealed, submitted_on_time}}.
        401 without a valid X-Admin-Key; 403 while the track is disabled;
        409 with a machine-readable reason_code when ineligible;
        201 on a new sponsorship, 200 with dedupe=true on a repeat of the
        same bankruptcy_event_id.
        """
        if not _admin_key_ok():
            return jsonify({"error": "Unauthorized"}), 401
        body = request.get_json(silent=True) or {}
        try:
            record = sponsor_recovery(
                agent_id=str(body.get("agent_id") or ""),
                wallet=str(body.get("wallet") or ""),
                bankruptcy_event_id=str(
                    body.get("bankruptcy_event_id") or ""),
                evidence=body.get("evidence") or {},
                approved_by="http-admin",
            )
            return jsonify(record), (200 if record.get("dedupe") else 201)
        except RecoveryDisabled as err:
            return _http_error(str(err), 403)
        except RecoveryIneligible as err:
            payload: Dict[str, Any] = {"reason_code": err.reason_code}
            if err.retry_after_ms is not None:
                payload["retry_after_ms"] = err.retry_after_ms
            return _http_error(err.detail, 409, **payload)
        except ValueError as err:
            return _http_error(str(err), 400)

    @bp.get("/v1/a2a/admin/recovery/status")
    def v1_recovery_status():
        """Per-wallet recovery standing: strikes, history, next terms."""
        if not _admin_key_ok():
            return jsonify({"error": "Unauthorized"}), 401
        wallet = request.args.get("wallet", "")
        if not wallet:
            return _http_error("wallet query param is required", 400)
        return jsonify(recovery_status(wallet)), 200
