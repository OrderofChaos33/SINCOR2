#!/usr/bin/env python3
"""Pre-commit claim-blocklist scan (SINCOR2 compliance hedge).

Fails the commit (exit 1) when the STAGED DIFF introduces blocklisted
marketing-claim phrases in public-facing copy, unless the (file, pattern)
pair is already covered in data/claim_register.json by a substantiated
disclaimer/policy/roadmap entry (same semantics as
tests/pytest/test_marketing_claims.py: the register is the source of
truth; disclaimed or policy-backed language does not block).

Only ADDED lines (``git diff --cached``) are inspected — pre-existing
copy never blocks a commit. Rendered prose is extracted the same way
src/sincor2/claim_scan.py does (tags/scripts/comments stripped), so the
word "audit" in code never triggers — only customer-visible copy counts.

Scan paths: README.md, templates/**/*.html (repo-relative).
Exempt by path (never scanned): data/, docs/, tests/, anything under
your_files/ or hidden_files/ — compliance artifacts, audit docs and
test fixtures may legitimately contain the words.

Usage: run from the repo root. No third-party deps (stdlib only, plus
the repo's own claim_scan module when importable).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

REPO_ROOT = subprocess.run(
    ["git", "rev-parse", "--show-toplevel"],
    capture_output=True, text=True, check=True,
).stdout.strip()

BLOCKLIST_PATH = os.path.join(REPO_ROOT, "scripts", "claim-blocklist.txt")
REGISTER_PATH = os.path.join(REPO_ROOT, "data", "claim_register.json")

EXEMPT_PREFIXES = ("data/", "docs/", "tests/")
EXEMPT_CONTAINS = ("your_files", "hidden_files")

SCAN_SUFFIXES = (".html", ".htm")

NON_BLOCKING_KINDS = ("disclaimer", "policy", "roadmap")

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def load_patterns(path):
    """Parse claim-blocklist.txt -> [(name, compiled_regex)]."""
    patterns = []
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            name, sep, body = line.partition(":")
            if not sep:
                print(f"claim-blocklist.txt:{lineno}: missing 'name:' prefix: {line!r}",
                      file=sys.stderr)
                sys.exit(2)
            # strip a trailing "  # reason" comment after the regex
            body = re.split(r"\s{2,}#", body.strip(), maxsplit=1)[0].strip()
            if not body:
                continue
            try:
                rx = re.compile(body)
            except re.error as exc:
                print(f"claim-blocklist.txt:{lineno}: bad regex {body!r}: {exc}",
                      file=sys.stderr)
                sys.exit(2)
            patterns.append((name.strip(), rx))
    return patterns


def load_register_index(path):
    """(file, pattern) -> [entries] from the claim register."""
    try:
        with open(path, encoding="utf-8") as fh:
            register = json.load(fh)
    except (OSError, ValueError):
        return {}
    idx = {}
    for entry in register.get("entries", []):
        for loc in entry.get("locations", []):
            f, _, _ = loc.rpartition(":")
            idx.setdefault((f, entry.get("pattern")), []).append(entry)
    return idx


def covered_by_register(idx, path, name):
    """True when a substantiated disclaimer/policy/roadmap entry covers it."""
    for entry in idx.get((path, name), []):
        if (entry.get("kind") in NON_BLOCKING_KINDS
                and entry.get("verdict") == "substantiated"):
            return True
    return False


def in_scan_scope(path):
    if path == "README.md":
        return True
    if path.startswith("templates/") and path.endswith(SCAN_SUFFIXES):
        return True
    return False


def is_exempt(path):
    if path.startswith(EXEMPT_PREFIXES):
        return True
    return any(part in path for part in EXEMPT_CONTAINS)


def staged_files():
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM", "-z"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def diff_hunks(path):
    """Parse `git diff --cached -U0` into hunks.

    Returns a list of (new_start, removed_prose_lines, added_lines) where
    added_lines is [(new_lineno, raw_line)] and removed_prose_lines is
    [raw_line]. Only hunks touching the file are returned.
    """
    proc = subprocess.run(
        ["git", "diff", "--cached", "-U0", "--", path],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    hunks = []
    new_lineno = None
    removed, added = [], []
    new_start = None

    def flush():
        if new_lineno is not None:
            hunks.append((new_start, removed, added))

    for line in proc.stdout.splitlines():
        m = _HUNK_RE.match(line)
        if m:
            flush()
            new_start = int(m.group(1))
            new_lineno = new_start
            removed, added = [], []
            continue
        if new_lineno is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added.append((new_lineno, line[1:]))
            new_lineno += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(line[1:])
        else:
            new_lineno += 1  # context line (none with -U0, kept for safety)
    flush()
    return hunks


def prose_of_line(raw, path):
    """Extract rendered prose from a single raw line (best effort)."""
    for _, prose in prose_lines(raw, path):
        return prose
    return ""


def prose_lines(text, path):
    """Yield (lineno, prose) using the repo's own extraction when available."""
    try:
        sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
        from sincor2 import claim_scan  # noqa: E402
        yield from claim_scan.text_prose_lines(text, path)
        return
    except Exception:
        pass
    # fallback: crude tag strip (still better than raw scan)
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = re.sub(r"<[^>]*>", " ", raw)
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            yield lineno, line


def normalize(text):
    try:
        sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
        from sincor2 import claim_scan  # noqa: E402
        return claim_scan.normalize(text)
    except Exception:
        import unicodedata
        s = unicodedata.normalize("NFKC", text).lower()
        s = re.sub(r"(?<=\d),(?=\d)", "", s)
        s = re.sub(r"[\u2010-\u2015\u2212/\u2044\u00b7\-_\u200b\u200c\u200d\ufeff|]+", " ", s)
        return re.sub(r"\s+", " ", s).strip()


def main():
    if not os.path.isfile(BLOCKLIST_PATH):
        print("claim scan: blocklist not found, skipping.")
        return 0
    patterns = load_patterns(BLOCKLIST_PATH)
    if not patterns:
        print("claim scan: blocklist empty, skipping.")
        return 0
    register_idx = load_register_index(REGISTER_PATH)

    violations = []
    for path in staged_files():
        if not in_scan_scope(path) or is_exempt(path):
            continue
        hunks = diff_hunks(path)
        if not hunks:
            continue
        for _new_start, removed_raw, added in hunks:
            # Phrases already present in the hunk's removed lines are
            # pre-existing copy, not introduced by this commit.
            removed_norm = " ".join(
                normalize(prose_of_line(r, path)) for r in removed_raw
            )
            for lineno, raw in added:
                prose = prose_of_line(raw, path)
                if not prose:
                    continue
                norm = normalize(prose)
                for name, rx in patterns:
                    if not rx.search(norm):
                        continue
                    if rx.search(removed_norm):
                        continue  # pre-existing in this hunk, not introduced
                    if covered_by_register(register_idx, path, name):
                        continue  # substantiated disclaimer/policy/roadmap
                    violations.append((path, lineno, name, prose[:160]))

    if violations:
        print("BLOCKED: staged changes introduce a blocklisted marketing claim:")
        seen = set()
        for path, lineno, name, snippet in violations:
            key = (path, lineno, name)
            if key in seen:
                continue
            seen.add(key)
            print(f"  {path}:{lineno}  [{name}]")
            print(f"    {snippet}")
        print()
        print("Reword or remove the claim before committing "
              "(or register it as a substantiated disclaimer/policy/roadmap "
              "entry in data/claim_register.json). "
              "See scripts/claim-blocklist.txt for the list and rationale.")
        return 1

    print("Claim-blocklist scan clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
