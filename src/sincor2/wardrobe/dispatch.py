"""Bind tool/skill execution to wardrobe permissions. Fail closed."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from sincor2.wardrobe.ids import valid_agent_id, wardrobe_path

EXECUTABLE = {"SandboxPass", "Active"}
KILL_ALL = "AGENT_KILL_ALL"

DANGEROUS = {
    "shell_unrestricted",
    "git_force_push",
    "sign_arbitrary_tx",
    "contact_human_off_allowlist",
    "python_exec",
    "execution",
}


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_wardrobe(agent_id: str) -> dict[str, Any] | None:
    if not valid_agent_id(agent_id):
        return None
    path = wardrobe_path(_root(), agent_id)
    if path is None:
        return None
    doc = yaml.safe_load(path.read_text())
    return doc if isinstance(doc, dict) else None


def killed(agent_id: str) -> bool:
    if os.environ.get(KILL_ALL) in {"1", "true", "TRUE", "yes"}:
        return True
    flag = f"AGENT_KILL_{agent_id.replace('-', '_')}"
    return os.environ.get(flag) in {"1", "true", "TRUE", "yes"}


def authorize(agent_id: str | None, tool: str) -> dict[str, Any]:
    """Return {ok, reason}. Unknown agent or draft status cannot run tools."""
    tool = (tool or "").strip()
    if not agent_id:
        return {"ok": False, "reason": "agent_id_required"}
    if not valid_agent_id(agent_id):
        return {"ok": False, "reason": "invalid_agent_id"}
    if killed(agent_id):
        return {"ok": False, "reason": "kill_switch"}
    doc = load_wardrobe(agent_id)
    if not doc:
        return {"ok": False, "reason": "wardrobe_missing"}
    ident = doc.get("identity") or {}
    status = ident.get("status")
    if status not in EXECUTABLE:
        return {"ok": False, "reason": f"status_{status}_not_executable"}
    perm = doc.get("permissions") or {}
    deny = set(perm.get("tools_deny") or [])
    allow = set(perm.get("tools_allow") or [])
    if tool in deny or tool in DANGEROUS and tool not in allow:
        return {"ok": False, "reason": "tool_denied"}
    if allow and tool not in allow:
        aliases = {
            "web_search": "web_search_readonly",
            "search": "web_search_readonly",
            "data_scraping": "fetch_url_readonly",
            "file_read": "read_repo",
        }
        mapped = aliases.get(tool, tool)
        if mapped not in allow:
            return {"ok": False, "reason": "tool_not_allowlisted"}
    return {"ok": True, "reason": "allow", "status": status, "agent_id": agent_id}
