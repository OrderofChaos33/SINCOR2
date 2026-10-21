"""Marketing-claim scanner (P1_P6).

Scans the public-claim sources (README.md, product-page templates,
data/sku_catalog.json, config/x402_pricing.yaml) for assertion-of-fact
patterns and classifies each hit. Shared by the claim-register generator
and tests/pytest/test_marketing_claims.py so the register and the
blocking test can never disagree about what was found.

Design notes:
  * Only *rendered prose* is scanned: <script>/<style> bodies, HTML
    comments, tags, and CSS are excluded, because claims must be
    customer-visible to count. Comment bodies stay excluded, but inline
    comments are stripped to the empty string (not a space) so a banned
    phrase split by a comment ("zer<!-- -->o risk") matches exactly as
    the browser renders it ("zero risk").
  * Normalization is adversarial: NFKC, case-folded, separator
    canonicalization (dashes, slashes, zero-width chars -> space), and
    digit-grouping commas removed, so "24/7", "24-7", "24 7", "24⁄7"
    all match — but "1,247" does not.
  * Classification is deliberately narrow. A "disclaimer" or "policy"
    label only applies when the disclaiming language is in the SAME
    sentence as the matched phrase AND semantically targets that
    phrase (e.g. "does not guarantee" only excuses "guarantee", never
    "24/7"). "Roadmap" applies to future-intent statements, not
    present-tense facts.

Known limitations (documented, not silent): deliberate misspellings
("guarenteed"), word-paraphrases ("twenty-four seven", "24 hours a day,
7 days a week"), and claims expressed only in images/video are not
caught by regex. The register + human review remain the backstop; the
test blocks what the patterns can see and forces every hit to be
registered.
"""
from __future__ import annotations

import glob
import html as _html
import os
import re
import unicodedata
from typing import Dict, Iterator, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def source_files(root: Optional[str] = None) -> List[str]:
    """The claim sources the register covers, in stable order."""
    root = root or REPO_ROOT
    files = [os.path.join(root, "README.md")]
    files += sorted(glob.glob(os.path.join(root, "templates", "**", "*.html"), recursive=True))
    files += [os.path.join(root, "data", "sku_catalog.json"),
              os.path.join(root, "config", "x402_pricing.yaml")]
    return [f for f in files if os.path.isfile(f)]


def normalize(text: str) -> str:
    """Adversarial normalization for claim matching."""
    s = unicodedata.normalize("NFKC", text).lower()
    s = re.sub(r"(?<=\d),(?=\d)", "", s)  # 1,247 -> 1247 (not "24 7")
    s = re.sub(r"[\u2010-\u2015\u2212/\u2044\u00b7\-_\u200b\u200c\u200d\ufeff|]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


#: Blocking patterns: {name: regex over NORMALIZED text}.
FORBIDDEN_PATTERNS: Dict[str, re.Pattern] = {
    # "24/7" requires a real separator: normalization turns every dash,
    # slash, or zero-width joiner into a space, so "24 7" matches but the
    # bare number "247" (and "1,247" -> "1247") can never match.
    "24/7": re.compile(r"\b24\s+7\b"),
    "zero-human": re.compile(r"\bzero\s*human\b"),
    "certified": re.compile(r"\bcertif(?:ied|ication|ications|y)\b"),
    "guaranteed": re.compile(r"\bguarantee[sd]?\b"),
    "hipaa": re.compile(r"\bhipaa\b"),
    "soc2": re.compile(r"\bsoc\s*2\b"),
    "audited": re.compile(r"\baudited\b"),
    "adgm": re.compile(r"\badgm\b"),
}

_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"[ \t]+")


def html_prose_lines(text: str) -> Iterator[Tuple[int, str]]:
    """Yield (line_no, visible prose) for an HTML document.

    Skips <script>/<style> bodies and comments with a per-line state
    machine so line numbers stay accurate for the register.
    """
    in_skip: Optional[str] = None  # 'script' | 'style' | 'comment'
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw
        if in_skip == "comment":
            end = line.find("-->")
            if end == -1:
                continue
            line = line[end + 3:]
            in_skip = None
        # strip inline comments to "": browsers drop comments entirely, so
        # prose on either side renders joined ("zer<!-- -->o risk" reads
        # "zero risk"). Replacing with a space would let comment-split
        # banned phrases evade matching (red-team #496 bypass 2).
        while "<!--" in line:
            start = line.find("<!--")
            end = line.find("-->", start)
            if end == -1:
                line = line[:start]
                in_skip = "comment"
                break
            line = line[:start] + line[end + 3:]
        if in_skip in ("script", "style"):
            low = line.lower()
            close = f"</{in_skip}"
            if close in low:
                line = line[low.find(close):]
                in_skip = None
            else:
                continue
        low = line.lower()
        for tag in ("script", "style"):
            # naive but adequate: <script ...> ... </script> on one line,
            # or an opening tag that starts a skipped region
            for m in re.finditer(rf"<{tag}\b[^>]*>", low):
                rest = low[m.end():]
                close = f"</{tag}>"
                if close in rest:
                    line = line[:m.start()] + " " + line[m.end() + rest.find(close) + len(close):]
                    low = line.lower()
                else:
                    line = line[:m.start()]
                    in_skip = tag
                    low = line.lower()
        prose = _TAG_RE.sub(" ", line)
        prose = _html.unescape(prose)  # defeat &#47; / &sol; evasions
        prose = _WS_RE.sub(" ", prose).strip()
        if prose:
            yield lineno, prose


def text_prose_lines(text: str, path: str) -> Iterator[Tuple[int, str]]:
    """Yield (line_no, prose) for any source file."""
    if path.endswith((".html", ".htm")):
        yield from html_prose_lines(text)
    else:
        for lineno, raw in enumerate(text.splitlines(), 1):
            line = _WS_RE.sub(" ", raw).strip()
            if line:
                yield lineno, line


_SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+")


# --- classification ---------------------------------------------------------

_POLICY_RE = re.compile(r"money[\s-]*back|satisfaction|refund", re.I)

_DISCLAIMER_RES: Dict[str, re.Pattern] = {
    # phrase -> disclaimer patterns that excuse it (same sentence)
    "guaranteed": re.compile(
        r"not financial advice|does?\s+not\s+guarantee|do\s+not\s+guarantee"
        r"|no\s+[\w\s]*guarantees?|never\s+guarantee|in development|dyor",
        re.I),
    "certified": re.compile(
        r"not\s+a\s+certif|isn'?t\s+certif|is\s+not\s+certif|never\s+certif"
        r"|your auditor still audits|propose,\s+never\s+certif",
        re.I),
    "audited": re.compile(r"your auditor still audits|not\s+audited", re.I),
}

_ROADMAP_RE = re.compile(r"\broadmap\b", re.I)


def classify(pattern: str, sentence: str, prev_prose: List[str]) -> str:
    """First-pass classification: disclaimer | policy | roadmap | assertion.

    Narrow by design (see module docstring). The register is the source
    of truth; this only drafts it.
    """
    if pattern == "guaranteed" and _POLICY_RE.search(sentence):
        return "policy"
    dis = _DISCLAIMER_RES.get(pattern)
    if dis and dis.search(sentence):
        return "disclaimer"
    if _ROADMAP_RE.search(sentence) or any(_ROADMAP_RE.search(p) for p in prev_prose[-5:]):
        return "roadmap"
    return "assertion"


def scan(root: Optional[str] = None):
    """Yield dicts: file, line, pattern, sentence, snippet, auto_kind."""
    for path in source_files(root):
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        rel = os.path.relpath(path, root or REPO_ROOT)
        prose_lines = list(text_prose_lines(text, path))
        prev: List[str] = []
        for lineno, prose in prose_lines:
            # Match per sentence so classification sees the right context.
            for sent in _SENT_SPLIT.split(prose):
                sent = sent.strip()
                if not sent:
                    continue
                norm = normalize(sent)
                for name, pat in FORBIDDEN_PATTERNS.items():
                    if pat.search(norm):
                        yield {
                            "file": rel,
                            "line": lineno,
                            "pattern": name,
                            "sentence": sent[:300],
                            "snippet": prose[:160],
                            "auto_kind": classify(name, sent, prev),
                        }
            prev.append(prose)
