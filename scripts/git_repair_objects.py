#!/usr/bin/env python3
"""Restore missing git blobs from working tree files (OneDrive corruption recovery)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True, errors="replace")


def main() -> int:
    files = run("git", "ls-files").splitlines()
    restored = 0
    missing = 0
    for rel in files:
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            h = run("git", "hash-object", "-w", rel).strip()
            restored += 1
            # Re-stage so index picks up writable blob
            subprocess.run(["git", "update-index", "--cacheinfo", "100644", h, rel], cwd=ROOT, check=False)
        except subprocess.CalledProcessError:
            missing += 1
            print(f"failed: {rel}", file=sys.stderr)
    print(f"restored_blobs={restored} failed={missing}")
    return 0 if missing == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())