"""Bounty-pool allocation reconciliation (stranded-allocation sweeper).

The task lifecycle moves pool money through fail-safe inline calls:
release on auction-timeout expiry and ghost expiry, spend on
settlement, release on sybil-hold rejection and on admin delete (see
``a2a_inbound_market``).  Fail-safe means a failure is logged and
swallowed *by design* — the close/settlement must never brick on
accounting — but it also means a swallowed failure (or a process
restart between the task-state save and the pool save) strands the
allocation in state ``allocated`` forever: nothing ever looks at it
again, ``available_axm`` stays understated, and a settled task's
stranded allocation is a double-count hazard for any later manual
release/spend against it.

This module is the missing sweeper.  For every allocation still in
state ``allocated`` it joins against the holder task's state and
classifies:

- task live (any non-terminal state) -> leave untouched;
- task expired / rejected / canceled (terminal, no payout) or the
  task record is gone (deleted) -> ``release`` through the pool's
  existing release path — the exact transition the inline call would
  have made;
- task settled WITH a recorded payout -> ``spend`` through the pool's
  existing spend path — again the exact transition (and journal
  entry) the settlement tail would have made, so totals conserve
  exactly and the paid bounty is never counted twice;
- task settled but NO payout is recorded -> flag for manual review
  and touch nothing: the settle paths stamp ``payout_axm`` in the
  same lock block that sets state ``settled``, so a settled task
  without one is evidence of a state the code never produces — a
  human decides, the sweeper does not guess on the money path.

The sweep is idempotent: applied allocations leave state
``allocated``, so a second run finds nothing to do.  It runs in two
places:

- automatically, fail-safe, at the tail of
  :func:`sincor2.a2a_inbound_market.expire_stale_assignments` — the
  existing sweeper cadence (agent heartbeats + board reads) — and
- manually via ``python -m sincor2.reconcile_pool_allocations``
  (dry-run by default; see that module for the operator contract).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Terminal task states, mirrored from
# sincor2.a2a_bounty_pool._TERMINAL_TASK_STATES (kept local so this
# module never has to reach into the pool's privates).
TERMINAL_TASK_STATES = {"settled", "expired", "rejected", "canceled"}

CLASS_LIVE = "live"
CLASS_RELEASE = "release"
CLASS_SPEND = "spend"
CLASS_MANUAL_REVIEW = "manual_review"


def _task_state(task: Dict[str, Any]) -> str:
    return str(task.get("state") or task.get("status") or "").strip().lower()


def _payout_recorded(task: Dict[str, Any]) -> bool:
    """True when the task carries settlement payout evidence.

    ``submit_proof`` and the sybil-hold release stamp ``payout_axm``
    (and ``settled_at``) in the same lock block that flips the state
    to ``settled`` — and only on the ``receipt.ok`` branch that also
    triggers the pool spend.  Either field is therefore reliable
    evidence the payout happened and the spend is owed.
    """
    return task.get("payout_axm") is not None or task.get("settled_at") is not None


def classify_allocation(task: Optional[Dict[str, Any]]) -> "tuple[str, str]":
    """(classification, reason) for one live allocation's holder task."""
    if task is None:
        return CLASS_RELEASE, "task record missing (deleted); no payout possible"
    state = _task_state(task)
    if not state:
        return CLASS_MANUAL_REVIEW, "task record carries no state; refusing to guess"
    if state not in TERMINAL_TASK_STATES:
        return CLASS_LIVE, f"task state {state!r} is not terminal"
    if state == "settled":
        if _payout_recorded(task):
            return CLASS_SPEND, "task settled with a recorded payout; spend owed"
        return CLASS_MANUAL_REVIEW, (
            "task settled but no payout recorded (payout_axm/settled_at "
            "absent); the settle paths never produce this state")
    detail = f" ({task.get('expired_reason')})" if task.get("expired_reason") else ""
    return CLASS_RELEASE, f"task {state}{detail}; terminal with no payout, release owed"


def tasks_from_board_file(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Load task records from the persisted board JSON file.

    Mirrors the parsing in ``a2a_inbound._load_tasks``: the file is
    either a dict carrying ``tasks`` (a list or an id-keyed dict) or a
    bare list.  Returns {} when no file is available — never raises.
    """
    if path is None:
        try:
            from sincor2.a2a_bounty_pool import _board_tasks_path

            path = _board_tasks_path()
        except Exception:
            return {}
    if path is None:
        return {}
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as err:
        logger.warning("allocation reconciliation: board file %s unreadable: %s",
                       path, err)
        return {}
    tasks = raw.get("tasks") if isinstance(raw, dict) else raw
    items = tasks.values() if isinstance(tasks, dict) else (tasks or [])
    out: Dict[str, Dict[str, Any]] = {}
    for task in items:
        if isinstance(task, dict) and task.get("task_id"):
            out[str(task["task_id"])] = task
    return out


def _snapshot_tasks(tasks: Optional[Dict[str, Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """Resolve the task universe for one sweep.

    An explicit mapping wins (tests, the operator CLI).  Otherwise the
    persisted board file is the base and the in-process fabric — the
    live truth inside the server — overlays it.
    """
    if tasks is not None:
        return dict(tasks)
    out = tasks_from_board_file()
    try:
        from sincor2.a2a_inbound import get_fabric

        fabric = get_fabric()
        with fabric.lock:
            for task_id, task in fabric.tasks.items():
                out[str(task_id)] = dict(task)
    except Exception as err:  # fabric unavailable (e.g. CLI before boot)
        logger.debug("allocation reconciliation: fabric snapshot skipped: %s", err)
    return out


def reconcile_allocations(pool: Any = None,
                          tasks: Optional[Dict[str, Dict[str, Any]]] = None,
                          apply: bool = False) -> Dict[str, Any]:
    """Classify every ``allocated`` allocation against its task.

    ``apply=False`` (default) is a dry run: classifications and totals
    only, no pool writes.  ``apply=True`` performs the release/spend
    for the ``release``/``spend`` classes through the pool's own
    terminal transitions; ``manual_review`` rows are never applied.

    Per-allocation failures during apply (e.g. a concurrent lifecycle
    transition won the race) are recorded on the row, never raised —
    ``release``/``spend`` raise unless the allocation is still
    ``allocated``, so a lost race is a no-op here, exactly as with the
    inline lifecycle calls.
    """
    if pool is None:
        from sincor2.a2a_bounty_pool import bounty_pool

        pool = bounty_pool()
    task_map = _snapshot_tasks(tasks)
    before = pool.status()
    actions: List[Dict[str, Any]] = []
    totals_wei = {CLASS_RELEASE: 0, CLASS_SPEND: 0,
                  CLASS_MANUAL_REVIEW: 0, CLASS_LIVE: 0}
    for alloc in before.get("allocations") or []:
        if alloc.get("state") != "allocated":
            continue
        task_id = str(alloc.get("task_id") or "")
        task = task_map.get(task_id)
        classification, reason = classify_allocation(task)
        try:
            amount_wei = int(alloc.get("amount_wei") or 0)
        except (TypeError, ValueError):
            amount_wei = 0
        totals_wei[classification] += amount_wei
        row: Dict[str, Any] = {
            "allocation_id": alloc.get("allocation_id"),
            "task_id": task_id,
            "amount_axm": amount_wei / 1e18,
            "task_state": (_task_state(task) if task is not None
                           else None),
            "classification": classification,
            "reason": reason,
            "applied": False,
            "result": "planned" if classification in (CLASS_RELEASE, CLASS_SPEND)
                      else ("flagged" if classification == CLASS_MANUAL_REVIEW
                            else "untouched"),
        }
        if apply and classification in (CLASS_RELEASE, CLASS_SPEND):
            try:
                if classification == CLASS_RELEASE:
                    pool.release(str(alloc.get("allocation_id")))
                    row["result"] = "released"
                else:
                    pool.spend(str(alloc.get("allocation_id")))
                    row["result"] = "spent"
                row["applied"] = True
            except Exception as err:  # lost race / pool degraded: record, move on
                row["result"] = f"error: {err}"
                logger.warning(
                    "allocation reconciliation: %s of %s for task %s failed: %s",
                    classification, alloc.get("allocation_id"), task_id, err)
        actions.append(row)
    report: Dict[str, Any] = {
        "dry_run": not apply,
        "scanned_allocations": len(actions),
        "actions": actions,
        "totals_axm": {k: v / 1e18 for k, v in totals_wei.items()},
        "pool_before": {
            "funded_axm": before.get("funded_axm"),
            "allocated_axm": before.get("allocated_axm"),
            "spent_axm": before.get("spent_axm"),
            "available_axm": before.get("available_axm"),
            "allocation_count": before.get("allocation_count"),
        },
    }
    if apply:
        after = pool.status()
        report["pool_after"] = {
            "funded_axm": after.get("funded_axm"),
            "allocated_axm": after.get("allocated_axm"),
            "spent_axm": after.get("spent_axm"),
            "available_axm": after.get("available_axm"),
            "allocation_count": after.get("allocation_count"),
        }
        acted = [r for r in actions if r["applied"]]
        if acted:
            logger.warning(
                "allocation reconciliation applied: %s",
                ", ".join(f"{r['result']} {r['amount_axm']:g} AXM "
                          f"({r['task_id']})" for r in acted))
    return report
