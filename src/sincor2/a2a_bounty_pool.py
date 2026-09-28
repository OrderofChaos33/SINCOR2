"""Launch bounty pool — offchain AXM reservation ledger for external-agent bounties.

Tracks AXM the operator has *reserved* for launch bounties (e.g. to fund
seeded task bounties that attract external agents). This is ledger-only
accounting in the same class as the offchain stake ledger: it does not move
funds on any chain. Onchain settlement of bounties, when it arrives, reads
this ledger as the reservation source of truth.

Funding
-------
The pool's reserve comes from the environment::

    SINCOR_LAUNCH_BOUNTY_AXM   total AXM the operator earmarks (default 0)

DEFAULT ZERO: with the variable unset the pool is unconfigured and
``fund()`` refuses — no real money moves unless the operator configures it.

Admin surface (see attach_market_routes in a2a_inbound_market.py)
-----------------------------------------------------------------
* ``POST /v1/a2a/pool/fund``      move reserve into the spendable pool
* ``POST /v1/a2a/pool/allocate``  reserve pool funds for a task (creates an
  allocation record; the task's bounty is then pool-backed)
* ``POST /v1/a2a/pool/release``   return an allocation to the pool unspent
* ``GET  /v1/a2a/pool``           public status (reserve/funded/allocated/
  available + allocation records)

Write endpoints are admin-gated by ``SINCOR_BOUNTY_POOL_ADMIN_KEY``
(``X-Admin-Key`` header or ``admin_key`` body field); unset key means the
admin surface is disabled (503), deny-by-default.

Persistence: JSON file with atomic writes (tmp + os.replace), following the
fee_conversion_executor.py ledger pattern. ``SINCOR_BOUNTY_POOL_PATH``
overrides the file location (tests).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sincor.a2a.pool")

RESERVE_ENV = "SINCOR_LAUNCH_BOUNTY_AXM"
PATH_ENV = "SINCOR_BOUNTY_POOL_PATH"
ADMIN_KEY_ENV = "SINCOR_BOUNTY_POOL_ADMIN_KEY"


class PoolError(Exception):
    """Base pool error (maps to 400)."""


class PoolNotConfigured(PoolError):
    """Reserve is zero / operator has not configured the pool (maps to 503)."""


class InsufficientPoolFunds(PoolError):
    """Allocation exceeds available pool balance (maps to 400)."""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _axm_to_wei(value: Any) -> int:
    return int(round(float(value) * 1e18))


def _wei_to_axm(wei: int) -> float:
    return int(wei) / 1e18


def _default_path() -> Path:
    override = os.environ.get(PATH_ENV, "").strip()
    if override:
        return Path(override)
    try:
        from sincor2.data_paths import data_dir
        return data_dir() / "a2a_bounty_pool.json"
    except Exception:
        return Path("a2a_bounty_pool.json")


def pool_admin_configured() -> bool:
    return bool(os.environ.get(ADMIN_KEY_ENV, "").strip())


class BountyPool:
    """JSON-persisted launch-bounty reservation ledger."""

    def __init__(self, path: Optional[str] = None, reserve_axm: Optional[float] = None) -> None:
        self.path = Path(path) if path else _default_path()
        if reserve_axm is None:
            try:
                reserve_axm = float(os.environ.get(RESERVE_ENV, "0") or 0)
            except (TypeError, ValueError):
                reserve_axm = 0.0
        self.reserve_wei = max(0, _axm_to_wei(reserve_axm))
        self._lock = threading.Lock()
        self._funded_wei = 0
        self._allocations: Dict[str, Dict[str, Any]] = {}
        self._history: List[Dict[str, Any]] = []
        self._load()
        if not self.path.is_file() and self.reserve_wei > 0:
            # Fresh disk (first boot, or a redeploy wiped the ephemeral
            # ledger): the operator configured a reserve via
            # SINCOR_LAUNCH_BOUNTY_AXM, so rehydrate it instead of silently
            # refusing allocations until someone re-funds by hand. A warm
            # ledger file is always trusted as-is.
            self._funded_wei = self.reserve_wei
            self._record("fund", self.reserve_wei,
                         {"auto_rehydrate": True,
                          "reason": "fresh disk; reserve configured"})
            self._save()
            logger.warning(
                "bounty pool ledger missing on boot; auto-funded %s AXM "
                "from configured reserve", _wei_to_axm(self.reserve_wei))

    # -- persistence ------------------------------------------------------
    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._funded_wei = int(raw.get("funded_wei") or 0)
            allocs = raw.get("allocations") or {}
            if isinstance(allocs, dict):
                for aid, alloc in allocs.items():
                    if isinstance(alloc, dict):
                        alloc = dict(alloc)
                        alloc["amount_wei"] = int(alloc.get("amount_wei") or 0)
                        self._allocations[aid] = alloc
            hist = raw.get("history")
            if isinstance(hist, list):
                self._history = hist
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning("bounty pool load failed (%s); starting empty", exc)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        payload = {
            "reserve_wei": str(self.reserve_wei),
            "funded_wei": str(self._funded_wei),
            "allocations": {
                aid: {**a, "amount_wei": str(int(a.get("amount_wei") or 0))}
                for aid, a in self._allocations.items()
            },
            "history": self._history[-500:],
            "saved_at": _now_ms(),
        }
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def _record(self, action: str, amount_wei: int, detail: Dict[str, Any]) -> None:
        entry = {"action": action, "amount_wei": str(amount_wei),
                 "at": _now_ms(), "detail": detail}
        self._history.append(entry)

    def _allocated_wei(self) -> int:
        return sum(int(a.get("amount_wei") or 0)
                   for a in self._allocations.values()
                   if a.get("state") == "allocated")

    # -- API --------------------------------------------------------------
    def fund(self, amount_axm: Optional[float] = None) -> Dict[str, Any]:
        """Move reserve into the spendable pool.

        ``amount_axm=None`` funds the full remaining reserve, and is
        idempotent: when nothing remains it returns the current status
        instead of raising. Raises ``PoolNotConfigured`` when the operator
        has not set a reserve (``SINCOR_LAUNCH_BOUNTY_AXM`` defaults to 0).
        """
        with self._lock:
            if self.reserve_wei <= 0:
                raise PoolNotConfigured(
                    f"launch bounty pool not configured: set {RESERVE_ENV} "
                    "to earmark AXM for launch bounties")
            remaining = self.reserve_wei - self._funded_wei
            if amount_axm is None:
                if remaining <= 0:
                    return self._status_locked()
                amount_wei = remaining
            else:
                amount_wei = _axm_to_wei(amount_axm)
                if amount_wei <= 0:
                    raise PoolError("amount_axm must be positive")
                if amount_wei > remaining:
                    raise PoolError(
                        f"amount exceeds remaining reserve "
                        f"({_wei_to_axm(remaining):g} AXM)")
            self._funded_wei += amount_wei
            self._record("fund", amount_wei, {})
            self._save()
            return self._status_locked()

    def allocate(self, task_id: str, amount_axm: float, reason: str = "") -> Dict[str, Any]:
        """Reserve pool funds for a task. Returns the allocation record."""
        task_id = str(task_id or "").strip()
        if not task_id:
            raise PoolError("task_id is required")
        amount_wei = _axm_to_wei(amount_axm)
        if amount_wei <= 0:
            raise PoolError("amount_axm must be positive")
        with self._lock:
            available = self._funded_wei - self._allocated_wei()
            if amount_wei > available:
                raise InsufficientPoolFunds(
                    f"allocation of {_wei_to_axm(amount_wei):g} AXM exceeds "
                    f"available pool balance ({_wei_to_axm(available):g} AXM)")
            alloc_id = "alloc_" + uuid.uuid4().hex[:10]
            alloc = {
                "allocation_id": alloc_id,
                "task_id": task_id,
                "amount_wei": amount_wei,
                "amount_axm": _wei_to_axm(amount_wei),
                "state": "allocated",
                "reason": str(reason or ""),
                "created_at": _now_ms(),
                "history": [{"state": "allocated", "at": _now_ms()}],
            }
            self._allocations[alloc_id] = alloc
            self._record("allocate", amount_wei,
                         {"allocation_id": alloc_id, "task_id": task_id,
                          "reason": str(reason or "")})
            self._save()
            return self._public_alloc(alloc)

    def release(self, allocation_id: str) -> Dict[str, Any]:
        """Return an allocation to the pool unspent."""
        allocation_id = str(allocation_id or "").strip()
        with self._lock:
            alloc = self._allocations.get(allocation_id)
            if alloc is None:
                raise KeyError("unknown allocation")
            if alloc.get("state") != "allocated":
                raise PoolError(
                    f"allocation already {alloc.get('state')}")
            alloc["state"] = "released"
            alloc["history"].append({"state": "released", "at": _now_ms()})
            self._record("release", int(alloc.get("amount_wei") or 0),
                         {"allocation_id": allocation_id,
                          "task_id": alloc.get("task_id")})
            self._save()
            return self._public_alloc(alloc)

    def allocations_for_task(self, task_id: str) -> List[Dict[str, Any]]:
        """Live allocations attached to one task (for admin delist)."""
        task_id = str(task_id or "").strip()
        with self._lock:
            return [self._public_alloc(a) for a in self._allocations.values()
                    if a.get("task_id") == task_id and a.get("state") == "allocated"]

    def _public_alloc(self, alloc: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(alloc)
        out["amount_wei"] = str(int(alloc.get("amount_wei") or 0))
        return out

    def _status_locked(self) -> Dict[str, Any]:
        allocated = self._allocated_wei()
        return {
            "reserve_axm": _wei_to_axm(self.reserve_wei),
            "funded_axm": _wei_to_axm(self._funded_wei),
            "allocated_axm": _wei_to_axm(allocated),
            "available_axm": _wei_to_axm(self._funded_wei - allocated),
            "allocation_count": sum(1 for a in self._allocations.values()
                                  if a.get("state") == "allocated"),
            "allocations": [self._public_alloc(a)
                            for a in sorted(self._allocations.values(),
                                            key=lambda x: x.get("created_at") or 0)],
            # Currency note: offchain AXM-denominated reservation ledger;
            # it does not move funds on any chain.
            "ledger": "offchain-axm",
        }

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return self._status_locked()


_POOL: Optional[BountyPool] = None
_POOL_LOCK = threading.Lock()


def bounty_pool(path: Optional[str] = None) -> BountyPool:
    """Process-wide pool singleton (mirrors stake_ledger())."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is None or path is not None:
            _POOL = BountyPool(path=path)
        return _POOL


def reset_bounty_pool(path: Optional[str] = None,
                      reserve_axm: Optional[float] = None) -> BountyPool:
    """Test isolation hook — rebuild the singleton."""
    global _POOL
    with _POOL_LOCK:
        _POOL = BountyPool(path=path, reserve_axm=reserve_axm)
        return _POOL
