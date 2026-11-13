"""Terms §6 regression tests (compliance HIGH-2, founder decision 2026-11-07).

The 30-day money-back guarantee in §6.1 was STRUCK per the founder's
2026-11-07 decision (site-liabilities directive 2026-11-03: money-back
guarantees are removed outright). These tests pin that removal and the
renumbering of the remaining Refund Policy subsections, while keeping
the legitimate refund mechanics (failed-delivery refund, how-to-request,
on-chain refund mechanics) intact.
"""
from __future__ import annotations

import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def read(rel):
    with open(os.path.join(REPO_ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def section6_subsections(terms):
    """Return the ordered list of (number, title) for <h3> headings in §6."""
    start = terms.index("<h2>6. Refund Policy</h2>")
    end = terms.index("<h2>7.", start)
    section = terms[start:end]
    return re.findall(r"<h3>6\.(\d+)\s+(.*?)</h3>", section)


class TestNoMoneyBackGuarantee:
    def test_moneyback_guarantee_absent(self):
        terms = read("templates/terms.html")
        assert "Money-Back Guarantee" not in terms
        assert "money-back guarantee" not in terms.lower()

    def test_guarantee_sentence_absent(self):
        terms = read("templates/terms.html")
        assert "not satisfied with SINCOR within the first 30 days" not in terms
        assert "This guarantee applies once per customer" not in terms

    def test_renewals_no_longer_references_guarantee_period(self):
        terms = read("templates/terms.html")
        assert "After the initial 30-day period" not in terms


class TestSection6Renumbering:
    def test_subsections_present_and_sequential(self):
        terms = read("templates/terms.html")
        subs = section6_subsections(terms)
        assert [num for num, _title in subs] == ["1", "2", "3"]
        assert [title for _num, title in subs] == [
            "Renewals",
            "One-Time Reports",
            "How to Request a Refund",
        ]

    def test_renewals_states_non_refundable_plainly(self):
        terms = read("templates/terms.html")
        assert "Subscription payments are non-refundable." in terms
        assert "work in good faith to resolve any service issues" in terms

    def test_legitimate_refund_mechanics_kept(self):
        terms = read("templates/terms.html")
        # Failed-delivery refund for one-time reports stays.
        assert "If we fail to deliver within the stated timeframe, you are entitled to a full refund upon request." in terms
        # How-to-request + on-chain refund mechanics stay.
        assert "We process eligible refunds within 5" in terms
        assert "On-chain payments are refunded on-chain to the wallet that paid" in terms
