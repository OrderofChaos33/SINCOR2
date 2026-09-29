"""P24 content-policy guard: the no_price_talk rule set.

Screens token name / symbol / description / creator bio at issuance and on
metadata updates. The deny-list is phrase-based (word-boundary matched, case
insensitive) — not substring-based — so legitimate text like "priceless
work" passes. Every rejection cites the rule-set version and the matched
phrase; the version is emitted in issuance events for auditability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

RULESET_VERSION = "1.0.0"

# Phrase-based deny-list: token price predictions, "moon"/"100x"-class
# promises, guaranteed-return language. Deliberately phrase-shaped to avoid
# substring false positives.
DENY_PHRASES = (
    "guaranteed returns",
    "guaranteed profit",
    "guaranteed gains",
    "risk-free gains",
    "risk free gains",
    "100x",
    "1000x",
    "to the moon",
    "will moon",
    "going to moon",
    "price will double",
    "price will triple",
    "price prediction",
    "get rich quick",
    "double your money",
    "triple your money",
    "10x your money",
    "pump and dump",
    "pump it",
    "guaranteed 10x",
    "sure profit",
    "can't lose",
    "cannot lose",
    "moonshot guaranteed",
)


def _phrase_re(phrase: str) -> "re.Pattern":
    # Word boundaries around the whole phrase; inner spaces match flexibly.
    body = r"\s+".join(re.escape(w) for w in phrase.split())
    return re.compile(r"(?<![a-z0-9])" + body + r"(?![a-z0-9])", re.IGNORECASE)


_PATTERNS = [(_phrase_re(p), p) for p in DENY_PHRASES]


@dataclass(frozen=True)
class ScreenResult:
    ok: bool
    field: str
    matched_phrase: Optional[str] = None
    ruleset_version: str = RULESET_VERSION


class PolicyViolation(Exception):
    """Metadata rejected by the no_price_talk guard."""

    def __init__(self, field: str, phrase: str):
        super().__init__(
            f"no_price_talk violation in {field!r}: matched phrase "
            f"{phrase!r} (ruleset {RULESET_VERSION})"
        )
        self.field = field
        self.matched_phrase = phrase
        self.ruleset_version = RULESET_VERSION


def screen_text(text: str, field: str) -> ScreenResult:
    for pattern, phrase in _PATTERNS:
        if pattern.search(text or ""):
            return ScreenResult(False, field, phrase)
    return ScreenResult(True, field)


def screen_metadata(
    name: str, symbol: str, description: str, bio: str
) -> List[ScreenResult]:
    """Screen all issuance metadata. Returns per-field results."""
    return [
        screen_text(name, "name"),
        screen_text(symbol, "symbol"),
        screen_text(description, "description"),
        screen_text(bio, "bio"),
    ]


def require_clean(name: str, symbol: str, description: str, bio: str) -> str:
    """Raise PolicyViolation on the first violating field; else the version."""
    for result in screen_metadata(name, symbol, description, bio):
        if not result.ok:
            raise PolicyViolation(result.field, result.matched_phrase or "")
    return RULESET_VERSION
