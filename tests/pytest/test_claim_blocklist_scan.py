"""Regression tests for the claim-blocklist pre-commit hedge bypasses (red-team #496).

Bypass 1 (line-split): a banned phrase broken across two source lines evaded
the per-line scan, though browsers collapse the newline and visitors read
the claim. The hedge must also scan the joined added-lines text (with and
without inter-line spacing) in addition to the per-line pass.

Bypass 2 (comment-split): ``zer<!-- -->o risk`` rendered as "zero risk" in
browsers, but the scan replaced comments with a space ("zer o risk") and
missed it. Comments must be stripped to "" before matching.

The end-to-end tests below build a scratch git repo, commit a clean
baseline, stage a malicious template change, and run the REAL
scripts/claim-blocklist-scan.py against it (same path the pre-commit hook
takes). Unit tests cover the shared html_prose_lines extraction fix.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

from sincor2 import claim_scan

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "claim-blocklist-scan.py")
BLOCKLIST_SRC = os.path.join(REPO_ROOT, "scripts", "claim-blocklist.txt")
CLAIM_SCAN_SRC = os.path.join(REPO_ROOT, "src", "sincor2", "claim_scan.py")

# Exact fixtures from the red-team PoC (w496_claim_blocklist_bypass.py).
POC_BYPASS1 = "<p>100% guaran\nteed returns by our experts.</p>\n"
POC_BYPASS2 = "<p>100% zer<!-- -->o risk, g<!--x-->uaranteed!</p>\n"
POC_CONTROL = "<p>100% guaranteed returns, zero risk, audited.</p>\n"

CLEAN_BASELINE = "<html><body><p>Welcome to SINCOR Business Solutions.</p></body></html>\n"


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True)


@pytest.fixture()
def staged_repo(tmp_path):
    """Scratch git repo wired like SINCOR2 for the scanner: templates/ page,
    scripts/claim-blocklist.txt, src/sincor2/claim_scan.py, data/claim_register.json."""
    repo = str(tmp_path / "repo")
    os.makedirs(os.path.join(repo, "templates"))
    os.makedirs(os.path.join(repo, "scripts"))
    os.makedirs(os.path.join(repo, "src", "sincor2"))
    os.makedirs(os.path.join(repo, "data"))
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    shutil.copy(BLOCKLIST_SRC, os.path.join(repo, "scripts", "claim-blocklist.txt"))
    shutil.copy(CLAIM_SCAN_SRC, os.path.join(repo, "src", "sincor2", "claim_scan.py"))
    with open(os.path.join(repo, "data", "claim_register.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"entries": []}, fh)
    with open(os.path.join(repo, "templates", "page.html"), "w",
              encoding="utf-8") as fh:
        fh.write(CLEAN_BASELINE)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "clean baseline")
    return repo


def stage_and_scan(repo, relpath, new_content, register_entries=None):
    """Overwrite relpath, optionally seed the register, stage, run the real
    scanner script, return the CompletedProcess."""
    with open(os.path.join(repo, relpath), "w", encoding="utf-8") as fh:
        fh.write(new_content)
    if register_entries is not None:
        with open(os.path.join(repo, "data", "claim_register.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"entries": register_entries}, fh)
    _git(repo, "add", "-A")
    return subprocess.run([sys.executable, SCRIPT], cwd=repo,
                          capture_output=True, text=True)


class TestBypass1LineSplit:
    def test_poc_line_split_word_is_blocked(self, staged_repo):
        proc = stage_and_scan(staged_repo, "templates/page.html", POC_BYPASS1)
        assert proc.returncode == 1, proc.stdout
        assert "[guaranteed]" in proc.stdout

    def test_interword_line_split_is_blocked(self, staged_repo):
        # "zero\nrisk" renders as "zero risk" (space-joined pass).
        proc = stage_and_scan(staged_repo, "templates/page.html",
                              "<p>100% zero\nrisk free trial.</p>\n")
        assert proc.returncode == 1, proc.stdout
        assert "[zero-risk]" in proc.stdout

    def test_split_across_three_lines_is_blocked(self, staged_repo):
        proc = stage_and_scan(staged_repo, "templates/page.html",
                              "<p>fully\ndiluted\nvaluation</p>\n")
        assert proc.returncode == 1, proc.stdout
        assert "[fully-diluted]" in proc.stdout


class TestBypass2CommentSplit:
    def test_poc_comment_split_is_blocked(self, staged_repo):
        proc = stage_and_scan(staged_repo, "templates/page.html", POC_BYPASS2)
        assert proc.returncode == 1, proc.stdout
        assert "[zero-risk]" in proc.stdout
        assert "[guaranteed]" in proc.stdout

    def test_multiline_comment_split_is_blocked(self, staged_repo):
        proc = stage_and_scan(staged_repo, "templates/page.html",
                              "<p>guaran<!-- hidden -->\nteed returns.</p>\n")
        assert proc.returncode == 1, proc.stdout
        assert "[guaranteed]" in proc.stdout


class TestExistingBehaviorPreserved:
    def test_control_plain_banned_copy_still_blocked(self, staged_repo):
        proc = stage_and_scan(staged_repo, "templates/page.html", POC_CONTROL)
        assert proc.returncode == 1, proc.stdout
        assert "[guaranteed]" in proc.stdout
        assert "[zero-risk]" in proc.stdout
        assert "[audited]" in proc.stdout

    def test_clean_diff_passes(self, staged_repo):
        proc = stage_and_scan(
            staged_repo, "templates/page.html",
            "<html><body><p>Welcome to SINCOR Business Solutions. "
            "Contact us for a demo.</p></body></html>\n")
        assert proc.returncode == 0, proc.stdout
        assert "clean" in proc.stdout.lower()

    def test_register_covered_split_phrase_passes(self, staged_repo):
        # A substantiated disclaimer/policy/roadmap register entry for the
        # (file, pattern) pair excuses even a line-split occurrence.
        register = [{
            "claim_id": "t1", "pattern": "guaranteed", "kind": "disclaimer",
            "verdict": "substantiated",
            "locations": ["templates/page.html:1"],
            "claim_text": "x", "evidence": {}, "downgrade_suggestion": "",
            "notes": "", "owner": "test",
        }]
        proc = stage_and_scan(staged_repo, "templates/page.html", POC_BYPASS1,
                              register_entries=register)
        assert proc.returncode == 0, proc.stdout

    def test_preexisting_phrase_in_hunk_does_not_block(self, staged_repo):
        # Rewording a line that already carried the phrase is pre-existing
        # copy, not an introduced claim.
        with open(os.path.join(staged_repo, "templates", "page.html"), "w",
                  encoding="utf-8") as fh:
            fh.write("<p>100% guaranteed returns.</p>\n")
        _git(staged_repo, "add", "-A")
        _git(staged_repo, "commit", "-q", "-m", "baseline with claim")
        proc = stage_and_scan(staged_repo, "templates/page.html",
                              "<p>100% guaranteed returns, terms apply.</p>\n")
        assert proc.returncode == 0, proc.stdout

    def test_word_boundary_still_required(self, staged_repo):
        # The glue pass must not flag innocent words that merely contain a
        # banned substring.
        proc = stage_and_scan(
            staged_repo, "templates/page.html",
            "<p>Our safeguar\nanteed approach to onboarding.</p>\n")
        # "safeguar"+"anteed" -> "safeguaranteed": no \bguarantee[sd]?\b match.
        assert proc.returncode == 0, proc.stdout


class TestHtmlProseLines:
    def test_comment_split_words_glue(self):
        lines = list(claim_scan.html_prose_lines(
            "<p>100% zer<!-- -->o risk, g<!--x-->uaranteed!</p>"))
        assert lines, "no prose extracted"
        prose = lines[0][1]
        assert "zero risk" in prose, prose
        assert "guaranteed" in prose, prose

    def test_comment_bodies_still_excluded(self):
        lines = list(claim_scan.html_prose_lines(
            "<!-- 24/7 uptime comment -->\n<p>clean copy</p>"))
        joined = " ".join(p for _, p in lines)
        assert "24/7" not in joined
        assert "clean copy" in joined

    def test_multiline_comment_region_still_skipped(self):
        lines = list(claim_scan.html_prose_lines(
            "<!-- start\nmiddle guaranteed\nend -->\n<p>visible</p>"))
        joined = " ".join(p for _, p in lines)
        assert "guaranteed" not in joined
        assert "visible" in joined
