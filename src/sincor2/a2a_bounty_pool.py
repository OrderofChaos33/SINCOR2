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
  available + allocation records) — intentionally unauthenticated.
  Carries ``ledger_verification`` (verify_ledger() aggregate: ok flag +
  discrepancy list; no secrets, no per-agent data) and the W-119
  ``rehydrate_blocked`` flag with its live obligations.

Admin credential (G2.13 — single unified surface)
-------------------------------------------------
Every A2A admin-gated route (pool writes, task-board admin delete,
sponsored-stake, recovery) uses ONE credential: the operator's
``ADMIN_PASSWORD`` environment variable, presented ONLY via the
``X-Admin-Key`` request header. Credentials are never accepted in request
bodies (secret-persistence risk in logs) and the expected value is never
logged. An unset ``ADMIN_PASSWORD`` disables the admin surface
(deny-by-default: 503). The legacy ``SINCOR_BOUNTY_POOL_ADMIN_KEY`` is
retired and no longer read.

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
# Unified A2A admin credential (G2.13): the single admin surface for every
# A2A admin-gated route is the operator's ADMIN_PASSWORD, presented only
# via the X-Admin-Key header. SINCOR_BOUNTY_POOL_ADMIN_KEY is retired and
# no longer read. Never accept admin credentials in request bodies.
ADMIN_KEY_ENV = "ADMIN_PASSWORD"

# Task states in which a pool-backed task's allocation is provably
# consumed (spent on settlement, released on expiry/rejection/delete).
# Every other state still holds a live claim on the pool: open, auction,
# assigned, proof_submitted, held — and "failed", because settlement
# spends ONLY on receipt.ok, so a failed task is adjudication-pending,
# not done. Unknown/missing states count as live (fail-loud direction).
_TERMINAL_TASK_STATES = {"settled", "expired", "rejected", "canceled"}


def _obligation_from_task(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A live pool-backed task record -> one rehydrate obligation."""
    if not task.get("pool_backed"):
        return []
    state = str(task.get("state") or task.get("status") or "")
    if state in _TERMINAL_TASK_STATES:
        return []
    try:
        bounty = float(task.get("bounty_axm") or 0)
    except (TypeError, ValueError):
        bounty = 0.0
    return [{"task_id": str(task.get("task_id") or task.get("id") or ""),
             "bounty_axm": bounty, "state": state}]


def _board_tasks_path() -> Optional[Path]:
    """Marketplace board file: SINCOR_A2A_TASKS_PATH override, else
    data_dir()/a2a_inbound_tasks.json. Mirrors
    a2a_inbound._tasks_persist_path without importing that module (the
    pool must stay import-cycle-free)."""
    override = os.environ.get("SINCOR_A2A_TASKS_PATH", "").strip()
    if override:
        return Path(override)
    try:
        from sincor2.data_paths import data_dir
        return data_dir() / "a2a_inbound_tasks.json"
    except Exception:
        return None


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
        self._spent_wei = 0
        self._allocations: Dict[str, Dict[str, Any]] = {}
        self._history: List[Dict[str, Any]] = []
        # Total history records ever written (persists across restarts).
        # _save() trims in-memory history to the last 500 entries, so
        # len(self._history) < self._history_writes means the journal was
        # trimmed and a full replay is impossible -> verify_ledger()
        # reports inconclusive instead of a false pass.
        self._history_writes = 0
        self._history_maybe_trimmed = False
        # Set by _load() when the persisted file exists but cannot be
        # parsed (corrupt JSON / unreadable): the in-memory state is
        # empty, but that emptiness is NOT a clean boot.
        self._load_failed = False
        # W-119: set when the ledger file was missing or corrupt on boot
        # while live pool-backed tasks still hold claims. While set, the
        # pool refuses to auto-fund AND refuses manual fund(): re-funding
        # the full reserve would promise the same AXM twice. Recovery is
        # by restart after the live tasks resolve (settlement/expiry
        # release or spend their backing, fail-safe) or after the ledger
        # file is restored from backup — a fresh boot then re-checks.
        self._rehydrate_blocked = False
        self._rehydrate_obligations: List[Dict[str, Any]] = []
        self._load()
        file_missing = not self.path.is_file()
        if self.reserve_wei > 0 and (file_missing or self._load_failed):
            obligations, inconclusive = self._live_pool_obligations()
            if obligations or inconclusive:
                self._rehydrate_blocked = True
                self._rehydrate_obligations = obligations
                logger.error(
                    "bounty pool ledger %s on boot with live pool-backed "
                    "tasks still holding claims; REFUSING to re-fund the "
                    "reserve (would double-promise %s AXM). Resolve the "
                    "live tasks or restore the ledger file, then restart. "
                    "Obligations: %s%s",
                    "missing" if file_missing else "corrupt",
                    _wei_to_axm(sum(
                        int(round(float(o.get("bounty_axm") or 0) * 1e18))
                        for o in obligations)),
                    obligations,
                    f" (check inconclusive: {inconclusive})"
                    if inconclusive else "")
                # Deliberately no _save(): a corrupt file is evidence for
                # the operator, and creating a warm empty file would make
                # the next boot trust the wiped state as-is.
            elif file_missing:
                # Fresh disk (first boot, or a redeploy wiped the ephemeral
                # ledger) with NO live obligations: the operator configured
                # a reserve via SINCOR_LAUNCH_BOUNTY_AXM, so rehydrate it
                # instead of silently refusing allocations until someone
                # re-funds by hand. A warm ledger file is always trusted
                # as-is; a corrupt file is never auto-funded (see above).
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
            self._spent_wei = int(raw.get("spent_wei") or 0)
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
            # Total-write counter lets verify_ledger() detect a trimmed
            # journal. Files written before the counter existed carry a
            # history but no counter -> treat as maybe-trimmed.
            hw = raw.get("history_writes")
            if hw is None:
                self._history_maybe_trimmed = bool(self._history)
            else:
                try:
                    self._history_writes = max(0, int(hw))
                except (TypeError, ValueError):
                    self._history_maybe_trimmed = bool(self._history)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            self._load_failed = True
            logger.warning("bounty pool load failed (%s); starting empty", exc)

    def _live_pool_obligations(self):
        """Live pool-backed tasks visible in durable task sources.

        Returns (obligations, inconclusive). ``obligations`` is a list of
        {task_id, bounty_axm, state}. ``inconclusive`` is a string when a
        source could not be read at all — fail-closed: the caller must
        not auto-fund on an unproven empty answer.

        Sources (both read, either can block):
        1. The marketplace board file — the actual home of pool_backed
           task records (create_task persists pool_backed there).
        2. The pluggable task store (lazy import, no import cycle) as a
           second source, in case pool-backed records land there.
        Runs ONLY on the missing/corrupt-file boot path, never on a warm
        boot, so it adds no cost or side effects to normal operation.
        """
        obligations: List[Dict[str, Any]] = []
        tasks_path = _board_tasks_path()
        if tasks_path is None:
            return obligations, "board tasks path could not be determined"
        try:
            if tasks_path.is_file():
                raw = json.loads(tasks_path.read_text(encoding="utf-8"))
                tasks = raw.get("tasks") if isinstance(raw, dict) else raw
                items = (tasks.values() if isinstance(tasks, dict)
                         else (tasks or []))
                for task in items:
                    if isinstance(task, dict):
                        obligations.extend(_obligation_from_task(task))
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            return obligations, f"board tasks file {tasks_path} unreadable ({exc})"
        try:
            from sincor2.a2a_task_store import get_task_store
            for task in get_task_store().list_all():
                if isinstance(task, dict):
                    obligations.extend(_obligation_from_task(task))
        except Exception as exc:  # store backend failure must not read as "no tasks"
            return obligations, f"task store unreadable ({exc})"
        return obligations, None

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        payload = {
            "reserve_wei": str(self.reserve_wei),
            "funded_wei": str(self._funded_wei),
            "spent_wei": str(self._spent_wei),
            "allocations": {
                aid: {**a, "amount_wei": str(int(a.get("amount_wei") or 0))}
                for aid, a in self._allocations.items()
            },
            "history": self._history[-500:],
            "history_writes": self._history_writes,
            "saved_at": _now_ms(),
        }
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def _record(self, action: str, amount_wei: int, detail: Dict[str, Any]) -> None:
        entry = {"action": action, "amount_wei": str(amount_wei),
                 "at": _now_ms(), "detail": detail}
        self._history.append(entry)
        self._history_writes += 1

    def verify_ledger(self) -> Dict[str, Any]:
        """Reconcile the append-only history journal against record state.

        Genuine double-entry check (W-101 fix): the journal is replayed to
        compute expected funded/allocated totals, which must match the
        record-derived state. Catches record drift, phantom allocations,
        and journal/record disagreement — the UNCX bug class — which the
        derived identity ``available = funded - allocated`` cannot see
        (it holds for corrupted books by construction).

        W-118 extension: the replay ALSO covers the spent counter — the
        exact field allocate() gates on via
        ``available = funded - spent - allocated``. Journal spend totals
        must match both the _spent_wei counter and the sum of spent-state
        allocation records; a drift mutant zeroing spent_wei previously
        passed verify green while inflating available.

        Returns {"ok": bool, "discrepancies": [...], "inconclusive": str|None}.
        ``inconclusive`` is set (instead of a false pass) when the
        in-memory journal does not cover the pool's full lifetime, e.g.
        after loading a file whose history was trimmed to the last 500
        entries, or when an unknown action appears in the journal.
        """
        with self._lock:
            discrepancies = []
            inconclusive = None
            if self._history_maybe_trimmed or (
                    self._history_writes
                    and len(self._history) < self._history_writes):
                inconclusive = (
                    "journal trimmed (loaded history shorter than total "
                    "writes); replay cannot cover full lifetime")
            else:
                journal_funded = 0
                journal_allocated = 0
                journal_spent = 0
                for e in self._history:
                    action = e.get("action")
                    try:
                        amt = int(e.get("amount_wei") or 0)
                    except (TypeError, ValueError):
                        amt = 0
                    if action == "fund":
                        journal_funded += amt
                    elif action == "allocate":
                        journal_allocated += amt
                    elif action == "release":
                        journal_allocated -= amt
                    elif action == "spend":
                        journal_allocated -= amt
                        journal_spent += amt
                    else:
                        inconclusive = (
                            f"unknown journal action {action!r}; "
                            "replay incomplete")
                        break
                if inconclusive is None:
                    if journal_funded != self._funded_wei:
                        discrepancies.append(
                            f"funded: journal={journal_funded} "
                            f"records={self._funded_wei}")
                    record_allocated = self._allocated_wei()
                    if journal_allocated != record_allocated:
                        discrepancies.append(
                            f"allocated: journal={journal_allocated} "
                            f"records={record_allocated}")
                    if journal_spent != self._spent_wei:
                        discrepancies.append(
                            f"spent: journal={journal_spent} "
                            f"counter={self._spent_wei}")
                    record_spent = sum(
                        int(a.get("amount_wei") or 0)
                        for a in self._allocations.values()
                        if a.get("state") == "spent")
                    if record_spent != self._spent_wei:
                        discrepancies.append(
                            f"spent: spent_records={record_spent} "
                            f"counter={self._spent_wei}")
            return {"ok": not discrepancies and inconclusive is None,
                    "discrepancies": discrepancies,
                    "inconclusive": inconclusive}

    def _allocated_wei(self) -> int:
        return sum(int(a.get("amount_wei") or 0)
                   for a in self._allocations.values()
                   if a.get("state") == "allocated")

    def _available_wei(self) -> int:
        """Spendable right now: funded minus spent minus live allocations."""
        return self._funded_wei - self._spent_wei - self._allocated_wei()

    # -- API --------------------------------------------------------------
    def fund(self, amount_axm: Optional[float] = None) -> Dict[str, Any]:
        """Move reserve into the spendable pool.

        ``amount_axm=None`` funds the full remaining reserve, and is
        idempotent: when nothing remains it returns the current status
        instead of raising. Raises ``PoolNotConfigured`` when the operator
        has not set a reserve (``SINCOR_LAUNCH_BOUNTY_AXM`` defaults to 0).
        """
        with self._lock:
            if self._rehydrate_blocked:
                raise PoolError(
                    "bounty pool rehydrate blocked: the pool ledger file "
                    "was missing or corrupt on boot while live pool-backed "
                    "tasks still hold claims; refusing to re-fund the full "
                    "reserve until those tasks resolve or the ledger file "
                    "is restored, then restart. Live obligations: "
                    f"{self._rehydrate_obligations}")
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
            available = self._available_wei()
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

    def spend(self, allocation_id: str) -> Dict[str, Any]:
        """Consume an allocation: the backed bounty was paid out on settlement.

        Terminal transition allocated -> spent. Unlike ``release`` (which
        returns funds to available unspent), spent funds left the pool:
        they are no longer counted as allocated *or* available, and the
        cumulative ``spent_wei`` counter is bumped so ``available`` stays
        exact. Raises ``KeyError`` for an unknown allocation and
        ``PoolError`` unless the allocation is currently allocated.
        """
        allocation_id = str(allocation_id or "").strip()
        with self._lock:
            alloc = self._allocations.get(allocation_id)
            if alloc is None:
                raise KeyError("unknown allocation")
            if alloc.get("state") != "allocated":
                raise PoolError(
                    f"allocation already {alloc.get('state')}")
            amount_wei = int(alloc.get("amount_wei") or 0)
            alloc["state"] = "spent"
            alloc["history"].append({"state": "spent", "at": _now_ms()})
            self._spent_wei += amount_wei
            self._record("spend", amount_wei,
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
            "spent_axm": _wei_to_axm(self._spent_wei),
            "allocated_axm": _wei_to_axm(allocated),
            "available_axm": _wei_to_axm(self._available_wei()),
            "allocation_count": sum(1 for a in self._allocations.values()
                                  if a.get("state") == "allocated"),
            "allocations": [self._public_alloc(a)
                            for a in sorted(self._allocations.values(),
                                            key=lambda x: x.get("created_at") or 0)],
            # W-119: True when boot found a missing/corrupt ledger file
            # with live pool-backed tasks still holding claims; while
            # True the pool refuses to (re-)fund. Surfaced by
            # GET /v1/a2a/pool alongside the ledger verification.
            "rehydrate_blocked": self._rehydrate_blocked,
            "rehydrate_blocked_obligations": [
                dict(o) for o in self._rehydrate_obligations],
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
