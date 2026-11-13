"""Claim scanner coverage for Python copy modules (HIGH-1 compliance fix).

The autonomous content agent (src/sincor2/content_agent.py) carried token
price/floor claims ("$0.15 floor ($150M FDV)", "SINC official price $0.15")
that the scanner never saw because source_files() only covered Markdown,
HTML templates, JSON and YAML. These tests pin:

  * content_agent.py is a scanned source;
  * the new "fdv" / "price-floor" patterns catch the original claim forms
    planted in a scratch tree;
  * the cleaned real tree scans clean for content_agent.py and for the new
    patterns anywhere (no false positives on legitimate copy such as the
    NFT appraisal "floor price" wording or the token-canon disclaimer);
  * SINCOR_BRAND values contain none of the banned claim strings.
"""
from __future__ import annotations

import os
import re

from sincor2 import claim_scan

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONTENT_AGENT_REL = os.path.join("src", "sincor2", "content_agent.py")
CONTENT_AGENT = os.path.join(REPO_ROOT, CONTENT_AGENT_REL)

BANNED_IN_BRAND = ["$0.15", "FDV", "$150M", "official price"]


def _brand_block() -> str:
    """The SINCOR_BRAND dict source text (parsed, not imported: importing
    content_agent creates directories and pulls onchain constants)."""
    text = open(CONTENT_AGENT, encoding="utf-8").read()
    m = re.search(r"SINCOR_BRAND\s*=\s*\{(.*?)\n\}", text, re.S)
    assert m, "SINCOR_BRAND dict not found in content_agent.py"
    return m.group(1)


class TestPythonCopyInScope:
    def test_content_agent_is_a_scanned_source(self):
        files = claim_scan.source_files(REPO_ROOT)
        assert CONTENT_AGENT in files

    def test_new_patterns_registered(self):
        assert "fdv" in claim_scan.FORBIDDEN_PATTERNS
        assert "price-floor" in claim_scan.FORBIDDEN_PATTERNS


class TestPlantedClaimCaught:
    def test_scanner_catches_planted_floor_claim(self, tmp_path):
        planted = tmp_path / CONTENT_AGENT_REL
        planted.parent.mkdir(parents=True)
        planted.write_text(
            'SINCOR_BRAND = {\n'
            '    "description": "SINC is 1B supply at a $0.15 floor '
            '($150M FDV). Pay at /buy.",\n'
            '    "pricing_note": "Platform from $297/mo; SINC official '
            'price $0.15.",\n'
            '}\n',
            encoding="utf-8",
        )
        hits = [h for h in claim_scan.scan(str(tmp_path))
                if h["file"] == CONTENT_AGENT_REL]
        assert hits, "planted token-price claims were not caught"
        patterns = {h["pattern"] for h in hits}
        assert "price-floor" in patterns, patterns
        assert "fdv" in patterns, patterns


class TestCleanedTreePasses:
    def test_no_hits_in_content_agent(self):
        hits = [h for h in claim_scan.scan(REPO_ROOT)
                if h["file"] == CONTENT_AGENT_REL]
        assert hits == [], hits

    def test_no_new_pattern_hits_anywhere(self):
        hits = [h for h in claim_scan.scan(REPO_ROOT)
                if h["pattern"] in ("fdv", "price-floor")]
        assert hits == [], hits


class TestBrandCopyClean:
    def test_brand_values_have_no_banned_strings(self):
        block = _brand_block()
        low = block.lower()
        for banned in BANNED_IN_BRAND:
            assert banned.lower() not in low, f"banned string present: {banned}"
        assert not re.search(r"\bfloor\b", low), "word 'floor' present in SINCOR_BRAND"
