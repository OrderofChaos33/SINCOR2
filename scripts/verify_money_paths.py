#!/usr/bin/env python3
"""Verify the 52 money-path registry (config/money_paths_registry.json).

Checks, for every entry (or a single --only <id>):
  * registry parses as JSON and contains exactly 52 entries, path_ids 1..52 unique
  * required fields present: path_id, name, category, entrypoint_file,
    payment_rail, status, healthcheck_cmd
  * status is one of LIVE / SEND-READY / WIRED-NEEDS-TRIGGER / IN-QUEUE / NOT FOUND
  * NOT FOUND entries must have entrypoint_file = null and a notes gap explanation
  * null entrypoints (any status) must carry a notes explanation — no silent gaps
  * every non-null entrypoint_file exists on disk
  * Python entrypoints parse (ast) and import cleanly where the interpreter has
    the dependencies; an ImportError naming a third-party package is reported
    as SKIP (run under the project venv for the full check), any other import
    failure is a FAIL.

Usage: python3 scripts/verify_money_paths.py [--only N] [--registry PATH]
Exit code 0 = all checks pass. Designed to run in under 5 seconds.
"""
import argparse
import ast
import importlib
import json
import os
import sys
import time

REQUIRED_FIELDS = ["path_id", "name", "category", "entrypoint_file",
                   "payment_rail", "status", "healthcheck_cmd"]
VALID_STATUSES = {"LIVE", "SEND-READY", "WIRED-NEEDS-TRIGGER", "IN-QUEUE", "NOT FOUND"}
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def module_name_for(entrypoint: str) -> str:
    mod = entrypoint[:-3].replace("/", ".")
    if mod.endswith(".__init__"):
        mod = mod[: -len(".__init__")]
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=int, default=None, help="verify a single path_id")
    ap.add_argument("--registry", default=os.path.join(REPO_ROOT, "config", "money_paths_registry.json"))
    args = ap.parse_args()

    t0 = time.time()
    failures, warnings = [], []

    try:
        with open(args.registry) as f:
            registry = json.load(f)
    except Exception as ex:  # noqa: BLE001
        print(f"FAIL: registry does not parse: {ex}")
        return 1

    paths = registry.get("paths", [])
    if args.only is None:
        if len(paths) != 52:
            failures.append(f"registry has {len(paths)} entries, expected 52")
        ids = [p.get("path_id") for p in paths]
        if sorted(i for i in ids if isinstance(i, int)) != list(range(1, 53)):
            failures.append("path_ids are not exactly 1..52 unique")
        selected = paths
    else:
        selected = [p for p in paths if p.get("path_id") == args.only]
        if not selected:
            print(f"FAIL: no entry with path_id={args.only}")
            return 1

    # Make repo imports work the same way the test suite runs them.
    for extra in (os.path.join(REPO_ROOT, "src"), REPO_ROOT):
        if extra not in sys.path:
            sys.path.insert(0, extra)

    import_checked = import_skipped = 0
    for p in selected:
        pid = p.get("path_id")
        for field in REQUIRED_FIELDS:
            if field not in p:
                failures.append(f"path {pid}: missing required field '{field}'")
        status = p.get("status")
        if status not in VALID_STATUSES:
            failures.append(f"path {pid}: invalid status {status!r}")
        entry = p.get("entrypoint_file")
        if status == "NOT FOUND" and entry is not None:
            failures.append(f"path {pid}: NOT FOUND but entrypoint_file is set ({entry})")
        if entry is None:
            if not p.get("notes"):
                failures.append(f"path {pid}: null entrypoint without a notes gap explanation")
            continue
        full = os.path.join(REPO_ROOT, entry)
        if not os.path.isfile(full):
            failures.append(f"path {pid}: entrypoint_file does not exist: {entry}")
            continue
        if entry.endswith(".py"):
            try:
                with open(full) as fh:
                    ast.parse(fh.read(), filename=entry)
            except SyntaxError as ex:
                failures.append(f"path {pid}: entrypoint does not parse: {entry}: {ex}")
                continue
            mod = module_name_for(entry)
            try:
                importlib.import_module(mod)
                import_checked += 1
            except ImportError as ex:
                # Walk to the root cause: if the deepest missing module is a
                # third-party dependency this interpreter lacks, the entrypoint
                # itself is fine -> SKIP. If the entrypoint (or a repo module)
                # is what is missing/broken -> FAIL.
                root = ex
                while getattr(root, "__context__", None) is not None:
                    root = root.__context__
                missing = getattr(root, "name", "") or getattr(ex, "name", "") or ""
                top = missing.split(".")[0] if missing else ""
                if top and top not in ("sincor2", "marketplace", "verticals") and missing != mod:
                    import_skipped += 1
                    warnings.append(f"path {pid}: import skipped ({mod}: missing dep '{missing}') — run under the project venv")
                else:
                    failures.append(f"path {pid}: entrypoint import failed: {mod}: {ex}")
            except Exception as ex:  # noqa: BLE001
                failures.append(f"path {pid}: entrypoint import raised {type(ex).__name__}: {mod}: {ex}")

    elapsed = time.time() - t0
    scope = f"path {args.only}" if args.only else "52 paths"
    for w in warnings:
        print(f"WARN: {w}")
    if failures:
        for f_ in failures:
            print(f"FAIL: {f_}")
        print(f"verify_money_paths: FAILED ({scope}) — {len(failures)} failure(s), "
              f"{import_checked} imports ok, {import_skipped} skipped, {elapsed:.2f}s")
        return 1
    print(f"verify_money_paths: OK ({scope}) — entrypoints exist, "
          f"{import_checked} Python imports clean, {import_skipped} skipped (deps), {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
