#!/usr/bin/env python3
"""Relist expired marketplace tasks in waves (Option A operator tool).

Companion to POST /v1/a2a/tasks/<task_id>/relist (admin-gated). Takes a
set of expired task IDs — from a file, a comma list, or fetched from the
board's expired pile — and relists them in waves, stopping at the
board cap (MAX_OPEN_TASKS = 200 open+auction) so a wave never tries to
push the board past its own limit. The endpoint re-checks the cap (and
every other precondition) server-side; this script is the pacing and
bookkeeping layer, not a safety layer.

DRY-RUN BY DEFAULT: without --execute nothing is written — the script
only reads the board (public GETs) and prints the wave plan.

Task sources (exactly one):
  --ids-file PATH     one task_id per line ('#' comments allowed)
  --ids a,b,c         inline comma-separated task IDs
  --fetch-expired     page through GET /v1/a2a/tasks?state=expired and
                      keep only relistable listings, applying the same
                      client-side pre-filter the endpoint enforces:
                      named poster, no 'p24' in skill/seed_key, no
                      onchain auction anchor. (The endpoint re-verifies
                      everything, including the live-allocation check
                      this script cannot see.)

Auth: the admin key comes from --admin-key or the ADMIN_PASSWORD
environment variable (the unified A2A admin credential, sent only as
the X-Admin-Key header). Required only with --execute.

Examples:
  # Plan the full run from the live expired pile (no writes):
  python scripts/relist_tasks.py --fetch-expired

  # Execute the first wave of 25:
  ADMIN_PASSWORD=... python scripts/relist_tasks.py \
      --fetch-expired --wave-size 25 --execute

  # Relist a hand-picked set from a file:
  ADMIN_PASSWORD=... python scripts/relist_tasks.py \
      --ids-file wave1.txt --execute

Exit codes: 0 = plan printed / all attempted relists succeeded;
1 = some relists were refused or failed; 2 = usage/config error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = os.environ.get("SINCOR_BASE_URL", "https://getsincor.com")
MAX_OPEN_TASKS = 200  # mirrors sincor2.a2a_inbound.MAX_OPEN_TASKS


def _request(base_url: str, method: str, path: str,
             admin_key: str | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(base_url.rstrip("/") + path, method=method)
    if admin_key:
        req.add_header("X-Admin-Key", admin_key)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        try:
            body = json.loads(err.read().decode("utf-8"))
        except Exception:
            body = {"error": f"HTTP {err.code}"}
        return err.code, body


def _board_total(base_url: str, states: str) -> int:
    status, body = _request(
        base_url, "GET",
        f"/v1/a2a/tasks?state={states}&per_page=1")
    if status != 200:
        raise RuntimeError(f"board read failed ({status}): {body}")
    return int(body.get("total") or 0)


def fetch_expired_ids(base_url: str) -> tuple[list[str], dict[str, int]]:
    """Page the expired pile and pre-filter to relistable listings."""
    kept: list[str] = []
    skipped = {"anonymous": 0, "p24": 0, "anchored": 0}
    page = 1
    while True:
        status, body = _request(
            base_url, "GET",
            f"/v1/a2a/tasks?state=expired&per_page=100&page={page}")
        if status != 200:
            raise RuntimeError(f"expired fetch failed ({status}): {body}")
        for task in body.get("tasks") or []:
            blob = " ".join(str(task.get(k) or "")
                            for k in ("skill", "seed_key")).lower()
            if not task.get("poster_id"):
                skipped["anonymous"] += 1
            elif "p24" in blob:
                skipped["p24"] += 1
            elif task.get("auction_id"):
                skipped["anchored"] += 1
            else:
                kept.append(str(task["task_id"]))
        if page >= int(body.get("pages") or 1):
            return kept, skipped
        page += 1


def load_ids_file(path: str) -> list[str]:
    ids = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if line:
                ids.append(line)
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Relist expired marketplace tasks in waves "
                    "(dry-run by default).")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--ids-file", help="file with one task_id per line")
    src.add_argument("--ids", help="comma-separated task IDs")
    src.add_argument("--fetch-expired", action="store_true",
                     help="fetch + pre-filter the board's expired pile")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--admin-key",
                        default=os.environ.get("ADMIN_PASSWORD"),
                        help="A2A admin key (default: $ADMIN_PASSWORD)")
    parser.add_argument("--wave-size", type=int, default=25,
                        help="tasks per wave (default 25)")
    parser.add_argument("--waves", type=int, default=1,
                        help="max waves to run (default 1)")
    parser.add_argument("--max-open", type=int, default=MAX_OPEN_TASKS,
                        help="board cap to stop at (default 200)")
    parser.add_argument("--execute", action="store_true",
                        help="actually POST the relists (default: dry-run)")
    args = parser.parse_args(argv)

    if args.execute and not args.admin_key:
        print("error: --execute needs an admin key "
              "(--admin-key or $ADMIN_PASSWORD)", file=sys.stderr)
        return 2

    skipped: dict[str, int] = {}
    if args.ids_file:
        task_ids = load_ids_file(args.ids_file)
    elif args.ids:
        task_ids = [t.strip() for t in args.ids.split(",") if t.strip()]
    else:
        try:
            task_ids, skipped = fetch_expired_ids(args.base_url)
        except RuntimeError as err:
            print(f"error: {err}", file=sys.stderr)
            return 2

    try:
        open_now = _board_total(args.base_url, "open,auction")
    except RuntimeError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    headroom = max(0, args.max_open - open_now)
    budget = min(len(task_ids), headroom, args.wave_size * args.waves)
    plan = task_ids[:budget]

    print(f"board: {open_now} open/auction, cap {args.max_open}, "
          f"headroom {headroom}")
    if skipped:
        print(f"expired pile pre-filter skipped: {skipped}")
    print(f"candidates: {len(task_ids)}, planned this run: {len(plan)} "
          f"({args.waves} wave(s) x {args.wave_size})")
    if len(task_ids) > budget:
        print(f"remaining after this run: {len(task_ids) - budget} "
              "(re-run to continue; settled/expired tasks free headroom)")

    if not args.execute:
        for task_id in plan:
            print(f"  DRY-RUN would relist {task_id}")
        print("dry-run only — pass --execute to relist.")
        return 0

    results = {"relisted": [], "refused": [], "failed": []}
    for i, task_id in enumerate(plan):
        if i and i % args.wave_size == 0:
            print(f"--- wave {i // args.wave_size + 1} ---")
        status, body = _request(
            args.base_url, "POST", f"/v1/a2a/tasks/{task_id}/relist",
            admin_key=args.admin_key)
        if status == 200:
            results["relisted"].append(task_id)
            print(f"  relisted {task_id} "
                  f"(pool_backed={body.get('pool_backed')}, "
                  f"alloc={body.get('pool_allocation_id')})")
        elif status == 409:
            results["refused"].append(
                {"task_id": task_id, "error": body.get("error")})
            print(f"  refused  {task_id}: {body.get('error')}")
            if "too many open" in str(body.get("error") or ""):
                print("board cap reached — stopping.")
                break
        else:
            results["failed"].append(
                {"task_id": task_id, "status": status,
                 "error": body.get("error")})
            print(f"  FAILED   {task_id} ({status}): {body.get('error')}")
    print(json.dumps({"summary": {
        "relisted": len(results["relisted"]),
        "refused": len(results["refused"]),
        "failed": len(results["failed"]),
    }, "refused": results["refused"], "failed": results["failed"]},
        indent=2))
    return 1 if (results["refused"] or results["failed"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
