"""P1_P6: claim register + blocking copy-test.

The register (data/claim_register.json) is the source of truth. This test
scans the exact same sources with the exact same scanner (sincor2.claim_scan)
and:

  * test_no_unregistered_claims (GREEN): every forbidden-pattern hit in
    rendered prose must have a register entry — new unsupported claims
    cannot be added silently.
  * test_no_unsubstantiated_claims (RED BY DESIGN while flagged claims
    exist): every hit whose register entry is kind=assertion must have
    verdict=substantiated with real evidence pointers. disclaimer/policy/
    roadmap entries are not assertions of fact and do not block.

Clearing the red test (founder decision per claim):
  1. Substantiate: add evidence {code_refs, test_refs, deployment_proof,
     customer_evidence} and flip verdict to "substantiated"; or
  2. Reword the copy so the claim is gone, then drop/retire the entry.

Do NOT "fix" this test by weakening the patterns or widening exemptions —
that defeats the gate. See the adversarial tests below.
"""
from __future__ import annotations

import json
import os

import pytest

from sincor2 import claim_scan

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REGISTER_PATH = os.path.join(REPO_ROOT, "data", "claim_register.json")

REQUIRED_FIELDS = {"claim_id", "pattern", "kind", "category", "verdict",
                   "locations", "claim_text", "evidence",
                   "downgrade_suggestion", "notes", "owner"}
KINDS = {"assertion", "disclaimer", "policy", "roadmap"}
VERDICTS = {"substantiated", "unproven", "downgrade_recommended"}
CATEGORIES = {"uptime", "autonomy", "certification", "financial", "regulated", "performance"}


def load_register():
    with open(REGISTER_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def entry_index(register):
    """(file, pattern) -> [entries]. pattern None entries are register-only."""
    idx = {}
    for e in register["entries"]:
        for loc in e["locations"]:
            f, _, _ = loc.rpartition(":")
            idx.setdefault((f, e["pattern"]), []).append(e)
    return idx


class TestRegisterSchema:
    def test_register_loads(self):
        reg = load_register()
        assert reg["entries"], "claim register is empty"

    def test_entry_schema(self):
        for e in load_register()["entries"]:
            assert REQUIRED_FIELDS <= set(e), f"{e.get('claim_id')}: missing fields"
            assert e["kind"] in KINDS, e["claim_id"]
            assert e["verdict"] in VERDICTS, e["claim_id"]
            assert e["category"] in CATEGORIES, e["claim_id"]
            assert e["locations"], f"{e['claim_id']}: no locations"
            assert e["claim_text"].strip(), f"{e['claim_id']}: empty claim_text"
            if e["verdict"] == "substantiated" and e["kind"] in ("disclaimer", "policy", "roadmap"):
                assert e["evidence"], f"{e['claim_id']}: substantiated needs evidence pointers"

    def test_claim_ids_unique(self):
        ids = [e["claim_id"] for e in load_register()["entries"]]
        assert len(ids) == len(set(ids))

    def test_no_unregistered_claims(self):
        """Every forbidden-pattern hit in prose must have a register entry."""
        reg = load_register()
        idx = entry_index(reg)
        missing = []
        for h in claim_scan.scan(REPO_ROOT):
            if (h["file"], h["pattern"]) not in idx:
                missing.append(f"{h['file']}:{h['line']} [{h['pattern']}] {h['sentence'][:80]}")
        assert missing == [], (
            "Unregistered claim hits (add a register entry or remove the copy):\n"
            + "\n".join(missing)
        )


class TestWeaselResistance:
    """The scanner must catch separator/case/unicode evasions of the patterns."""

    @pytest.mark.parametrize("variant", [
        "24/7", "24-7", "24 7", "24⁄7", "24\u20117", "24\u200b/\u200b7", "24/7/365",
    ])
    def test_247_variants_detected(self, variant):
        assert claim_scan.FORBIDDEN_PATTERNS["24/7"].search(claim_scan.normalize(variant))

    @pytest.mark.parametrize("variant,pattern", [
        ("zero-human", "zero-human"), ("zero human", "zero-human"), ("ZERO\u2014HUMAN", "zero-human"),
        ("certified", "certified"), ("CERTIFICATION", "certified"), ("certify", "certified"),
        ("guaranteed", "guaranteed"), ("GUARANTEE", "guaranteed"), ("guarantees", "guaranteed"),
        ("HIPAA-compliant", "hipaa"), ("hipaa compliant", "hipaa"),
        ("SOC 2", "soc2"), ("SOC2", "soc2"),
        ("audited", "audited"), ("AUDITED", "audited"),
        ("ADGM-compliant", "adgm"),
    ])
    def test_pattern_variants_detected(self, variant, pattern):
        assert claim_scan.FORBIDDEN_PATTERNS[pattern].search(claim_scan.normalize(variant)), variant

    @pytest.mark.parametrize("text", [
        "1,247 businesses", "SVG viewBox 0 24 24", "version 24.7 released",
        "call extension 247", "order 1247",
    ])
    def test_247_lookalikes_not_detected(self, text):
        assert not claim_scan.FORBIDDEN_PATTERNS["24/7"].search(claim_scan.normalize(text)), text

    def test_prose_extraction_skips_code(self):
        html = ('<style>.guarantee { color: red; }</style>'
                '<script>var x = "24/7";</script>'
                '<!-- 24/7 comment -->'
                '<div title="24/7">visible 24/7 text</div>')
        lines = list(claim_scan.html_prose_lines(html))
        joined = " ".join(p for _, p in lines)
        assert "visible 24/7 text" in joined
        assert ".guarantee" not in joined and 'var x' not in joined and "comment" not in joined

    def test_disclaimer_exemption_is_narrow(self):
        # "does not guarantee" excuses "guaranteed", never "24/7".
        assert claim_scan.classify("guaranteed", "We does not guarantee uptime.", []) == "disclaimer"
        assert claim_scan.classify("24/7", "We does not guarantee uptime, but run 24/7.", []) == "assertion"
        assert claim_scan.classify("certified", "Is this a certification? Your auditor still audits.", []) == "disclaimer"
        assert claim_scan.classify("hipaa", "We take HIPAA seriously.", []) == "assertion"

    def test_policy_exemption_needs_refund_context(self):
        assert claim_scan.classify("guaranteed", "30-day money-back guarantee.", []) == "policy"
        assert claim_scan.classify("guaranteed", "Minimum-output guaranteed on every fill.", []) == "assertion"


class TestNoUnsubstantiatedClaims:
    """BLOCKING GATE — red while flagged claims exist (see module docstring).

    Fails with one aggregated, actionable report: every assertion-kind hit
    whose register verdict is not substantiated.
    """

    def test_no_unsubstantiated_claims(self):
        reg = load_register()
        idx = entry_index(reg)
        violations = []
        for h in claim_scan.scan(REPO_ROOT):
            entries = idx.get((h["file"], h["pattern"]), [])
            for e in entries:
                if e["kind"] == "assertion" and e["verdict"] != "substantiated":
                    locs = ", ".join(e["locations"])
                    violations.append(
                        f"[{e['claim_id']}] {locs} verdict={e['verdict']}\n"
                        f"    quote: {e['claim_text'][:140]}\n"
                        f"    fix: {e['downgrade_suggestion'][:140]}"
                    )
        # de-duplicate: one entry may cover several hits
        violations = sorted(set(violations))
        assert violations == [], (
            f"{len(violations)} unsubstantiated marketing claims are blocking.\n"
            "Founder decision required per claim: substantiate with evidence (flip verdict) "
            "or reword the copy (drop the entry).\n\n" + "\n".join(violations)
        )
