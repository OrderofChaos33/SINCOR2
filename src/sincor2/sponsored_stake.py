"""Sponsored stake for the genesis cohort.

The platform fronts an agent's first stake; the fronted amount is recouped
automatically from that agent's first earnings. Ledger-backed (JSON,
following the fee-executor ledger pattern), admin-gated, DEFAULT OFF.

Mechanics
---------
* Enablement: ``SINCOR_SPONSORED_STAKE_ENABLED=1`` (also accepts
  ``true``/``yes``). Default: off. The mechanism exists in code but never
  fronts funds unless the operator opts in — enabling it is the operator's
  call.
* Fronting: admin-only ``POST /v1/a2a/admin/sponsored-stake``
  (``X-Admin-Key`` header, same convention as the app's admin key check).
  One active sponsorship per agent. The fronted amount is deposited into
  the stake ledger with reference ``sponsored-stake:<agent_id>`` so it is
  immediately usable for commit-time stake locks.
* Recoup: hooked into ``submit_proof``'s settlement path
  (``a2a_inbound_market``). Every earnings event recoups
  ``min(outstanding, earnings)``; partial recoups keep the record in
  ``recouping`` until the outstanding balance hits zero (``settled``).
  Recoupment runs even if the operator later disables fronting — disabling
  stops *new* fronts, it does not forgive outstanding ones.
* Statuses: ``fronted`` -> ``recouping`` -> ``settled``.

Currency note: the stake ledger is AXM-denominated offchain accounting
(Pool 1). Sponsored stake moves ledger accounting only, never funds
on-chain. Recoup is accounted at payout-staging time (the earnings event);
on-chain broadcast of the payout itself is a separate step.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("sincor.market.sponsored_stake")

ENABLE_ENV = "SINCOR_SPONSORED_STAKE_ENABLED"
LEDGER_ENV = "SINCOR_SPONSORED_STAKE_LEDGER"
DEFAULT_LEDGER_PATH = os.path.expanduser(
    "~/workspace/ops/sponsored_stake_ledger.json")

STATUS_FRONTED = "fronted"
STATUS_RECOUPING = "recouping"
STATUS_SETTLED = "settled"

_ACTIVE = (STATUS_FRONTED, STATUS_RECOUPING)


class SponsoredStakeDisabled(RuntimeError):
    """Raised when fronting is attempted while the mechanism is off."""


def sponsored_stake_enabled() -> bool:
    """True only when the operator explicitly enabled sponsored stake."""
    return os.environ.get(ENABLE_ENV, "").strip().lower() in (
        "1", "true", "yes", "on")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class SponsoredStakeLedger:
    """JSON-persisted sponsored-stake ledger with atomic writes.

    One record per agent: fronted amount, recouped amount, status, and an
    event journal. Mirrors the ConversionLedger persistence pattern
    (tmp file + os.replace).
    """

    def __init__(self, path: Optional[str] = None):
        self.path = (
            path or os.environ.get(LEDGER_ENV) or DEFAULT_LEDGER_PATH)
        self._data: Dict[str, Any] = {"agents": {}, "events": []}
        self._load()

    # -- persistence ------------------------------------------------------
    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    self._data.update(raw)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "sponsored-stake ledger load failed (%s); starting empty",
                    exc)

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
            {"kind": kind, "at": _now(), **detail})
        if len(self._data["events"]) > 5000:
            self._data["events"] = self._data["events"][-5000:]

    # -- API --------------------------------------------------------------
    def status_of(self, agent_id: str) -> Optional[Dict[str, Any]]:
        rec = self._data["agents"].get(agent_id)
        return dict(rec) if rec else None

    def outstanding_wei(self, agent_id: str) -> int:
        rec = self._data["agents"].get(agent_id)
        if not rec or rec.get("status") == STATUS_SETTLED:
            return 0
        return int(rec.get("fronted_wei", "0")) - int(
            rec.get("recouped_wei", "0"))

    def front(self, agent_id: str, amount_wei: int,
              approved_by: str) -> Dict[str, Any]:
        """Front an agent's first stake. Admin path only.

        Raises SponsoredStakeDisabled when the mechanism is off, ValueError
        on bad input, RuntimeError when the agent already has an active
        sponsorship.
        """
        if not sponsored_stake_enabled():
            raise SponsoredStakeDisabled(
                f"sponsored stake is disabled; set {ENABLE_ENV}=1 to enable")
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            raise ValueError("agent_id is required")
        amount_wei = int(amount_wei)
        if amount_wei <= 0:
            raise ValueError("amount_wei must be positive")
        existing = self._data["agents"].get(agent_id)
        if existing and existing.get("status") in _ACTIVE:
            raise RuntimeError(
                f"agent {agent_id} already has an active sponsorship "
                f"({existing['status']}, outstanding "
                f"{self.outstanding_wei(agent_id)} wei)")
        from sincor2.onchain.stake_ledger import stake_ledger

        stake_ledger().deposit(
            agent_id, amount_wei,
            reference=f"sponsored-stake:{agent_id}")
        rec = {
            "agent_id": agent_id,
            "fronted_wei": str(amount_wei),
            "recouped_wei": "0",
            "status": STATUS_FRONTED,
            "approved_by": str(approved_by or "admin"),
            "fronted_at": _now(),
            "settled_at": None,
        }
        self._data["agents"][agent_id] = rec
        self._event("front", agent_id=agent_id,
                    amount_wei=str(amount_wei),
                    approved_by=str(approved_by or "admin"))
        self._save()
        return dict(rec)

    def recoup(self, agent_id: str, earnings_wei: int,
               task_id: Optional[str] = None) -> Dict[str, Any]:
        """Recoup ``min(outstanding, earnings)`` from an earnings event.

        No-op (zero recoup) when the agent has no active sponsorship.
        Runs regardless of the enablement flag: disabling stops new
        fronts, it never forgives outstanding ones.
        """
        earnings_wei = int(earnings_wei)
        outstanding = self.outstanding_wei(agent_id)
        if outstanding <= 0 or earnings_wei <= 0:
            return {"agent_id": agent_id, "recouped_wei": "0",
                    "outstanding_wei": str(outstanding),
                    "status": (self._data["agents"].get(agent_id) or {}).get(
                        "status")}
        take = min(outstanding, earnings_wei)
        rec = self._data["agents"][agent_id]
        rec["recouped_wei"] = str(int(rec["recouped_wei"]) + take)
        new_outstanding = outstanding - take
        rec["status"] = (STATUS_SETTLED if new_outstanding == 0
                         else STATUS_RECOUPING)
        if new_outstanding == 0:
            rec["settled_at"] = _now()
        self._event("recoup", agent_id=agent_id,
                    recouped_wei=str(take),
                    outstanding_wei=str(new_outstanding),
                    task_id=str(task_id or ""),
                    earnings_wei=str(earnings_wei))
        self._save()
        return {"agent_id": agent_id, "recouped_wei": str(take),
                "outstanding_wei": str(new_outstanding),
                "status": rec["status"]}


# --- process-wide singleton (overridable in tests) -----------------------------

_LEDGER: Optional[SponsoredStakeLedger] = None


def sponsored_ledger(path: Optional[str] = None) -> SponsoredStakeLedger:
    global _LEDGER
    if _LEDGER is None or path is not None:
        _LEDGER = SponsoredStakeLedger(path=path)
    return _LEDGER


def reset_sponsored_ledger(
        path: Optional[str] = None) -> SponsoredStakeLedger:
    global _LEDGER
    _LEDGER = SponsoredStakeLedger(path=path)
    return _LEDGER


# --- module-level entry points -------------------------------------------------


def front_sponsored_stake(agent_id: str, amount_wei: int,
                          approved_by: str = "admin") -> Dict[str, Any]:
    """Front an agent's stake (admin path). See SponsoredStakeLedger.front."""
    return sponsored_ledger().front(agent_id, amount_wei, approved_by)


def recoup_sponsored_stake(agent_id: str, earnings_wei: int,
                           task_id: Optional[str] = None) -> Dict[str, Any]:
    """Recoup from an earnings event. Called by the settlement path."""
    return sponsored_ledger().recoup(agent_id, earnings_wei, task_id)


# --- admin routes ----------------------------------------------------------------

def _admin_key_ok() -> bool:
    """Admin gate: X-Admin-Key header == ADMIN_PASSWORD (constant-time).

    Same convention as the app's own admin key check; kept local so the A2A
    blueprint does not import the full app module.
    """
    expected = os.environ.get("ADMIN_PASSWORD", "")
    if not expected:
        return False
    try:
        from flask import request

        key = request.headers.get("X-Admin-Key", "")
    except Exception:
        return False
    return bool(key) and hmac.compare_digest(str(key), str(expected))


def attach_sponsored_stake_routes(bp: Any) -> None:
    """Mount the admin-gated sponsored-stake routes on an A2A blueprint."""
    from flask import jsonify, request

    from sincor2.a2a_inbound import _http_error, get_fabric

    @bp.post("/v1/a2a/admin/sponsored-stake")
    def v1_sponsored_front():
        """Front an agent's first stake (admin only, mechanism opt-in).

        Body: {agent_id, amount_axm}. 401 without a valid X-Admin-Key;
        403 while SINCOR_SPONSORED_STAKE_ENABLED is unset; 404 for an
        unknown agent; 409 when the agent already has an active
        sponsorship.
        """
        if not _admin_key_ok():
            return jsonify({"error": "Unauthorized"}), 401
        body = request.get_json(silent=True) or {}
        try:
            agent_id = str(body.get("agent_id") or "").strip()
            if not agent_id:
                raise ValueError("agent_id is required")
            with get_fabric().lock:
                if agent_id not in get_fabric().agents:
                    raise KeyError("unknown agent")
            raw = body.get("amount_axm")
            if raw is None:
                raise ValueError("amount_axm is required")
            amount_axm = float(raw)
            if not amount_axm > 0:
                raise ValueError("amount_axm must be positive")
            record = front_sponsored_stake(
                agent_id, int(round(amount_axm * 1e18)))
            return jsonify(record), 201
        except SponsoredStakeDisabled as err:
            return _http_error(str(err), 403)
        except ValueError as err:
            return _http_error(str(err), 400)
        except KeyError as err:
            return _http_error(str(err), 404)
        except RuntimeError as err:
            return _http_error(str(err), 409)

    @bp.get("/v1/a2a/admin/sponsored-stake/<agent_id>")
    def v1_sponsored_status(agent_id):
        """Sponsorship status for one agent (admin only)."""
        if not _admin_key_ok():
            return jsonify({"error": "Unauthorized"}), 401
        record = sponsored_ledger().status_of(str(agent_id or "").strip())
        if record is None:
            return _http_error("no sponsorship record", 404)
        return jsonify(record), 200
