"""Canonical agent id allowlist. Fail closed on path tricks."""

from __future__ import annotations

import re
from pathlib import Path

AGENT_ID_RE = re.compile(r"^E-[a-z0-9-]+-\d{2}$")


def valid_agent_id(agent_id: str) -> bool:
    return bool(agent_id) and bool(AGENT_ID_RE.fullmatch(agent_id))


def wardrobe_path(root: Path, agent_id: str) -> Path | None:
    if not valid_agent_id(agent_id):
        return None
    agents = (root / "agents").resolve()
    path = (agents / f"{agent_id}.yaml").resolve()
    try:
        path.relative_to(agents)
    except ValueError:
        return None
    return path if path.is_file() else None
