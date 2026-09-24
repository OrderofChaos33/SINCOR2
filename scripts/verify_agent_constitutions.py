#!/usr/bin/env python3
"""Verify agents/*.yaml constitution_sha256 fields against the real sha256
of each agent's constitution_refs files.

Exit 0 when every agent config carries a valid, current hash; exit 1 otherwise.
Run in CI to stop the integrity field drifting from the constitution docs.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agents"

FIELD_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def digest_for(refs: list[str]) -> str:
    h = hashlib.sha256()
    for ref in refs:
        p = ROOT / ref
        if not p.is_file():
            raise FileNotFoundError(f"constitution ref not found: {ref}")
        h.update(p.read_bytes())
    return f"sha256:{h.hexdigest()}"


def main() -> int:
    failures: list[str] = []
    checked = 0
    for path in sorted(AGENTS.glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        refs = data.get("constitution_refs") or []
        field = data.get("constitution_sha256")
        if not refs:
            failures.append(f"{path.name}: missing constitution_refs")
            continue
        if not field or not FIELD_RE.match(str(field)):
            failures.append(f"{path.name}: constitution_sha256 missing or not a real sha256")
            continue
        try:
            expected = digest_for(refs)
        except FileNotFoundError as e:
            failures.append(f"{path.name}: {e}")
            continue
        if field != expected:
            failures.append(f"{path.name}: hash stale (constitution docs changed?)")
            continue
        checked += 1
    if failures:
        print("constitution verification FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"constitution verification OK ({checked} agents)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
