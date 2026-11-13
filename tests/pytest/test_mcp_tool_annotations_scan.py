"""Tests for scripts/mcp_tool_annotations_scan.py.

Dev-watch item 265 hedge (mcp/python-sdk #3737): every SINCOR2 MCP tool
with real-world side effects must declare load-bearing ToolAnnotations
(destructive_hint / idempotent_hint / open_world_hint, never null),
enforced by a CI scanner that fails the build otherwise. Read-only tools
are exempt (read_only_hint=True).
"""

import importlib.util
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")


def _load_scanner():
    path = os.path.join(SCRIPTS_DIR, "mcp_tool_annotations_scan.py")
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    spec = importlib.util.spec_from_file_location("mcp_tool_annotations_scan", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scanner = _load_scanner()


def _annotated_side_effecting():
    return [
        {
            "name": "submit_bid",
            "description": "WRITE — place a bid.",
            "inputSchema": {"type": "object"},
            "annotations": {
                "title": "Submit bid",
                "read_only_hint": False,
                "destructive_hint": False,
                "idempotent_hint": False,
                "open_world_hint": False,
            },
        }
    ]


def test_passes_on_fully_annotated_registry():
    tools = _annotated_side_effecting() + [
        {
            "name": "list_tasks",
            "description": "Read-only list.",
            "inputSchema": {"type": "object"},
            "annotations": {"title": "List tasks", "read_only_hint": True},
        }
    ]
    assert scanner.scan_tools(tools, "fixture") == []


def test_fails_nonzero_on_bare_side_effecting_tool():
    tools = [
        {
            "name": "submit_bid",
            "description": "WRITE — place a bid.",
            "inputSchema": {"type": "object"},
            # no annotations at all
        }
    ]
    violations = scanner.scan_tools(tools, "fixture")
    assert violations, "expected violations for a bare side-effecting tool"
    assert any("submit_bid" in v for v in violations)


def test_fails_on_null_hints():
    tools = [
        {
            "name": "submit_bid",
            "description": "WRITE — place a bid.",
            "inputSchema": {"type": "object"},
            "annotations": {
                "title": "Submit bid",
                "read_only_hint": False,
                "destructive_hint": None,
                "idempotent_hint": True,
                "open_world_hint": False,
            },
        }
    ]
    violations = scanner.scan_tools(tools, "fixture")
    assert violations, "null destructive_hint must be a violation"
    assert any("destructive_hint" in v for v in violations)


def test_fails_on_missing_hint_keys():
    tools = [
        {
            "name": "register_agent",
            "description": "WRITE — register.",
            "inputSchema": {"type": "object"},
            "annotations": {"title": "Register", "read_only_hint": False},
        }
    ]
    violations = scanner.scan_tools(tools, "fixture")
    assert violations, "missing hint keys must be violations"
    joined = " ".join(violations)
    assert "idempotent_hint" in joined and "open_world_hint" in joined


def test_read_only_tools_exempt():
    tools = [
        {
            "name": "list_tasks",
            "description": "Read-only list.",
            "inputSchema": {"type": "object"},
            "annotations": {"title": "List tasks", "read_only_hint": True},
        }
    ]
    assert scanner.scan_tools(tools, "fixture") == []


def test_read_only_claiming_destructive_is_violation():
    tools = [
        {
            "name": "list_tasks",
            "description": "Read-only list.",
            "inputSchema": {"type": "object"},
            "annotations": {
                "title": "List tasks",
                "read_only_hint": True,
                "destructive_hint": True,
            },
        }
    ]
    violations = scanner.scan_tools(tools, "fixture")
    assert violations, "read-only + destructive=True is contradictory"


def test_scanner_covers_both_real_registries():
    registries = scanner.registries()
    names = {r[0] for r in registries}
    assert "mcp_server" in names, "mcp_server TOOLS must be scanned"
    assert "swarm_command_mcp" in names, "swarm_command_mcp tools must be scanned"
    total = sum(len(r[1]) for r in registries)
    assert total == 16, "expected 6 discovery + 10 swarm-command tools, got %d" % total


def test_real_registries_pass_scan():
    violations = scanner.run()
    assert violations == [], "real registries must be fully annotated: %r" % violations
