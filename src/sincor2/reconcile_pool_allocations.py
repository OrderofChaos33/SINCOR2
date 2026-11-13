"""Bounty-pool allocation reconciliation — operator entry point.

The task lifecycle's pool calls are fail-safe by design (a failed
release/spend is logged and swallowed so accounting can never brick a
close or a settlement), which strands the allocation in state
``allocated`` when one of those calls fails — or when the process
restarts between the task-state save and the pool save.  The stranded
cohort found on 2026-11-13 was 16 allocations / 26.0 AXM (14 expired,
1 settled-and-paid, 1 deleted).  The classification and repair logic
lives in :mod:`sincor2.a2a_pool_reconciliation`; this CLI is the
operator's window onto it:

    python -m sincor2.reconcile_pool_allocations [--apply]
        [--pool-path PATH] [--tasks-path PATH]

DRY-RUN BY DEFAULT: prints the per-allocation classification and the
AXM totals as JSON and writes nothing.  ``--apply`` performs the
release/spend transitions through the pool's own paths.

OPERATOR CONTRACT — read before ``--apply``: the pool ledger is owned
by the running server process, which persists its in-memory state on
every pool write.  Running ``--apply`` from this CLI against the live
ledger file WHILE THE SERVER RUNS races that persistence (last
writer wins) and must not be done.  The safe routes are:

- let the in-process sweeper do it: reconciliation runs automatically
  at the tail of ``expire_stale_assignments`` (agent heartbeats +
  board reads) inside the server, which is how the stranded cohort
  normally clears after this fix deploys; or
- run this CLI with ``--apply`` during a maintenance window with the
  server stopped, pointed at the same data files the server uses
  (``SINCOR_BOUNTY_POOL_PATH`` / ``SINCOR_A2A_TASKS_PATH`` env, or the
  flags below).

Dry-run is read-only against the on-disk ledger and is safe anytime.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from sincor2.a2a_pool_reconciliation import (
    reconcile_allocations,
    tasks_from_board_file,
)


def _load_tasks_arg(tasks_path: Optional[str]) -> Optional[Dict[str, Dict[str, Any]]]:
    if not tasks_path:
        return None
    return tasks_from_board_file(Path(tasks_path))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="reconcile_pool_allocations",
        description=(
            "Reconcile bounty-pool allocations stranded in state "
            "'allocated' against terminal holder tasks. Dry-run "
            "unless --apply is given."))
    parser.add_argument(
        "--apply", action="store_true",
        help=("perform the release/spend transitions (default: dry-run). "
              "Only with the server stopped — see the module docstring."))
    parser.add_argument(
        "--pool-path", default=None,
        help="pool ledger JSON path (default: SINCOR_BOUNTY_POOL_PATH / data dir)")
    parser.add_argument(
        "--tasks-path", default=None,
        help="board tasks JSON path (default: fabric + persisted board resolution)")
    args = parser.parse_args(argv)

    from sincor2.a2a_bounty_pool import BountyPool

    # A fresh instance over the on-disk ledger (never the server
    # singleton): the CLI audits the persisted state, not a process's
    # memory.
    pool = BountyPool(path=args.pool_path) if args.pool_path else BountyPool()
    report = reconcile_allocations(
        pool=pool, tasks=_load_tasks_arg(args.tasks_path), apply=args.apply)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
