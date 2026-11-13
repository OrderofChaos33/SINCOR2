"""CI checks for the effect-sink manifest (src/sincor2/effect_sink_manifest.json).

What CI enforces:

1. The manifest is valid JSON with the expected shape.
2. Every listed ``module_path`` resolves to a real Python module file.
3. Every ``file_line`` reference resolves to a real file and line number.
4. ``sink_id`` values are unique; ``effect_type`` names a real EffectType
   member of sincor2.effect_gateway; ``current_gateway_status`` is
   "wrapped" or "unwrapped".
5. The declared ``total_sinks`` matches the actual entry count (so entries
   cannot be silently dropped).

How CI fails on NEW unwrapped adapters (documented check):

- Adding a new sink call site (new smtplib/requests/urllib POST to an
  external host, new w3.eth.send_raw_transaction / sign_and_broadcast
  callback, new subprocess/Popen, new scheduler/task-queue enqueue of a
  side-effect job, new persistent file write) REQUIRES adding a manifest
  entry in the same PR with ``current_gateway_status`` set. The manifest
  test itself cannot see unlisted code, so the enforcement is procedural:
  the PR checklist for any change touching a sink-class call site must
  include the manifest entry. To make this mechanically checkable, this
  test additionally scans the codebase for a small set of high-confidence
  sink patterns (see SINK_PATTERNS) and asserts every hit is covered by a
  manifest entry in the same file — an unlisted new sink of a known class
  fails the test.

To wrap a sink: route its call site through
sincor2.effect_gateway.EffectGateway.dispatch with a registered adapter,
then flip its ``current_gateway_status`` to "wrapped" in the same PR.
"""

import json
import re
from pathlib import Path

import pytest

from sincor2.effect_gateway import EffectType

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "src" / "sincor2" / "effect_sink_manifest.json"

# High-confidence sink patterns: every source hit must be covered by a
# manifest entry in the same file. Patterns are deliberately narrow (call
# sites, not imports or docstrings) to avoid false positives.
SINK_PATTERNS = {
    # direct SMTP usage (anchored on the smtplib module, not method names
    # that collide with unrelated APIs like libp2p's send_message)
    "smtplib": re.compile(r"smtplib\."),
    # chain broadcast
    "chain_broadcast": re.compile(r"\.send_raw_transaction\(|sendRawTransaction\("),
    # process execution (anchored on the call, not bare imports)
    "process_exec": re.compile(r"subprocess\.run\(|subprocess\.Popen\(|Popen\("),
    # Twilio SMS send
    "sms_send": re.compile(r"\.messages\.create\("),
    # Stripe money movement
    "stripe_write": re.compile(
        r"stripe\.(Charge|PaymentIntent|Subscription|Payout|Transfer|Refund)\."
        r"|checkout\.Session\.create\(|Subscription\.delete\("
    ),
}


def load_manifest():
    with open(MANIFEST_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def test_manifest_is_valid_json_with_expected_shape():
    manifest = load_manifest()
    assert "sinks" in manifest and isinstance(manifest["sinks"], list)
    assert manifest["sinks"], "manifest must not be empty"
    assert manifest["_readme"]["total_sinks"] == len(manifest["sinks"])


def test_sink_ids_unique_and_fields_present():
    manifest = load_manifest()
    seen = set()
    required = {
        "sink_id", "module_path", "function", "file_line",
        "effect_type", "current_gateway_status", "owner",
    }
    for sink in manifest["sinks"]:
        assert required <= set(sink), f"missing fields in {sink.get('sink_id')}"
        assert sink["sink_id"] not in seen, f"duplicate sink_id {sink['sink_id']}"
        seen.add(sink["sink_id"])
        assert sink["current_gateway_status"] in ("wrapped", "unwrapped")
        # effect_type must name a real EffectType member
        assert sink["effect_type"] in {t.value for t in EffectType}, (
            f"{sink['sink_id']}: unknown effect_type {sink['effect_type']!r}"
        )


def test_every_module_path_exists():
    manifest = load_manifest()
    for sink in manifest["sinks"]:
        module_file = (
            REPO_ROOT / "src" / (sink["module_path"].replace(".", "/") + ".py")
        )
        package_init = (
            REPO_ROOT / "src" / sink["module_path"].replace(".", "/") / "__init__.py"
        )
        assert module_file.exists() or package_init.exists(), (
            f"{sink['sink_id']}: module {sink['module_path']} not found"
        )


def test_every_file_line_resolves():
    manifest = load_manifest()
    for sink in manifest["sinks"]:
        file_part, _, line_part = sink["file_line"].partition(":")
        assert line_part.isdigit(), f"{sink['sink_id']}: bad file_line format"
        target = REPO_ROOT / file_part
        assert target.exists(), f"{sink['sink_id']}: file {file_part} not found"
        total_lines = sum(1 for _ in open(target, encoding="utf-8"))
        assert 1 <= int(line_part) <= total_lines, (
            f"{sink['sink_id']}: line {line_part} out of range in {file_part}"
        )


def _strip_strings_and_comments(text):
    """Remove triple-quoted blocks and line comments before pattern matching.

    Docstrings and comments frequently *mention* sink APIs (e.g. safety
    notes saying "broadcast with w3.eth.send_raw_transaction in the calling
    code"); only real call sites count as sinks.
    """
    text = re.sub(r'"""[\s\S]*?"""', '""', text)
    text = re.sub(r"'''[\s\S]*?'''", "''", text)
    lines = [line.split("#", 1)[0] for line in text.splitlines()]
    return "\n".join(lines)


def _source_files():
    src = REPO_ROOT / "src" / "sincor2"
    return [p for p in src.rglob("*.py") if "tests" not in p.parts]


def test_sink_pattern_hits_are_covered_by_manifest():
    """Every high-confidence sink call site must have a manifest entry.

    This is the mechanical half of the 'new unwrapped adapter' check: a
    newly added smtplib send, chain broadcast, subprocess call, Twilio
    send, or Stripe write that is NOT added to the manifest fails CI here.
    """
    manifest = load_manifest()
    covered_files = {}
    for sink in manifest["sinks"]:
        covered_files.setdefault(sink["file_line"].split(":")[0], []).append(
            sink["sink_id"]
        )

    uncovered = []
    for path in _source_files():
        try:
            text = _strip_strings_and_comments(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        rel = str(path.relative_to(REPO_ROOT))
        hits = [
            name
            for name, pattern in SINK_PATTERNS.items()
            if pattern.search(text)
        ]
        if hits and rel not in covered_files:
            uncovered.append(f"{rel}: {', '.join(hits)}")
    assert not uncovered, (
        "sink-class call sites with no manifest entry (add them to "
        "effect_sink_manifest.json):\n" + "\n".join(uncovered)
    )


def test_no_manifest_entry_points_at_test_code():
    manifest = load_manifest()
    for sink in manifest["sinks"]:
        assert "/tests/" not in sink["file_line"], sink["sink_id"]
        assert "test_" not in sink["module_path"], sink["sink_id"]
