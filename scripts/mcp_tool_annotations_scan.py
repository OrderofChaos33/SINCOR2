#!/usr/bin/env python3
"""CI scanner: every SINCOR2 MCP tool with real-world side effects must
declare load-bearing ToolAnnotations (destructive_hint / idempotent_hint /
open_world_hint, all non-null booleans).

Context: mcp/python-sdk #3737 — only 25/289 upstream tools declare
ToolAnnotations, so money-moving tools give agent clients no machine-readable
signal that approval has real-world cost. This scanner fails the build when
any of OUR tools omits that signal.

Annotation convention (kept minimal; mirrors MCP ToolAnnotations):
    tool["annotations"] = {
        "title": str,            # human title, optional
        "read_only_hint": bool,  # True => side-effecting hints not required
        "destructive_hint": bool,# True => may delete/irrevocably change state
        "idempotent_hint": bool, # True => repeat calls have no extra effect
        "open_world_hint": bool, # True => interacts with external entities
    }
For side-effecting tools all three load-bearing hints must be real booleans
(never None, never missing). Read-only tools set read_only_hint=True and are
exempt — but claiming read_only_hint=True AND destructive_hint=True is a
contradiction and fails.

Exit 0 when clean; exit 1 listing every violating tool.
"""

import sys
from typing import Any, Dict, List, Tuple

# Load-bearing hints required on every side-effecting tool.
REQUIRED_HINTS = ("destructive_hint", "idempotent_hint", "open_world_hint")


def registries() -> List[Tuple[str, List[Dict[str, Any]]]]:
    """(registry_name, tool list) pairs for every SINCOR2 MCP registry."""
    from sincor2 import mcp_server
    from sincor2.swarm_command_mcp import _tool_defs

    return [
        ("mcp_server", mcp_server.TOOLS),
        ("swarm_command_mcp", _tool_defs()),
    ]


def _check_tool(tool: Dict[str, Any], registry_name: str) -> List[str]:
    name = tool.get("name", "<unnamed>")
    tag = "%s/%s" % (registry_name, name)
    ann = tool.get("annotations")
    if ann is None:
        return ["%s: no annotations declared (side-effecting tools must declare "
                "destructive_hint/idempotent_hint/open_world_hint)" % tag]
    if not isinstance(ann, dict):
        return ["%s: annotations must be a dict, got %s" % (tag, type(ann).__name__)]
    violations: List[str] = []
    read_only = ann.get("read_only_hint") is True
    if read_only:
        if ann.get("destructive_hint") is True:
            violations.append(
                "%s: contradictory — read_only_hint=True with destructive_hint=True" % tag)
        return violations
    for hint in REQUIRED_HINTS:
        value = ann.get(hint, "__missing__")
        if not isinstance(value, bool):
            violations.append(
                "%s: %s must be a boolean, got %r (null/missing is not a signal)"
                % (tag, hint, None if value == "__missing__" else value))
    return violations


def scan_tools(tools: List[Dict[str, Any]], registry_name: str) -> List[str]:
    """Check one registry's tools; return the list of violations (empty = clean)."""
    violations: List[str] = []
    for tool in tools:
        violations.extend(_check_tool(tool, registry_name))
    return violations


def run() -> List[str]:
    """Scan all real registries; return the list of violations."""
    violations: List[str] = []
    for registry_name, tools in registries():
        violations.extend(scan_tools(tools, registry_name))
    return violations


def main() -> int:
    violations = run()
    if violations:
        print("MCP ToolAnnotations scan FAILED: %d violation(s)" % len(violations))
        for v in violations:
            print("  - %s" % v)
        return 1
    print("MCP ToolAnnotations scan passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
