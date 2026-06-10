"""Wire marketplace, orchestration core, and vertical packs into the Flask runtime."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

from flask import Flask

from core.policy import ExecutionPolicy
from core.reliability import ReliabilityControls
from core.router import TaskRouter
from marketplace.registry import AgentCardRegistry
from marketplace.reputation import ReputationEngine
from marketplace.settlement import SettlementCoordinator
from verticals.loader import (
    SKILL_VERTICAL_MAP,
    instantiate_vertical_agents,
    load_agent_cards,
)

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def bootstrap_platform(app: Flask) -> Dict[str, Any]:
    """
    Register vertical Agent Cards, initialize routing, and expose platform
    services on ``app.extensions['sincor_platform']``.
    """
    storage_path = _REPO_ROOT / "marketplace" / "agent_cards.json"
    registry = AgentCardRegistry(storage_path=storage_path)

    registered = 0
    for card in load_agent_cards():
        registry.register(card)
        registered += 1

    platform_card_path = _REPO_ROOT / "marketplace" / "platform_agent_card.json"
    if platform_card_path.exists():
        try:
            platform_card = json.loads(platform_card_path.read_text(encoding="utf-8"))
            registry.register(platform_card)
            registered += 1
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load platform agent card: %s", exc)

    reputation = ReputationEngine()
    router = TaskRouter(registry=registry, reputation=reputation)
    vertical_agents = instantiate_vertical_agents()
    settlement = SettlementCoordinator()
    policy = ExecutionPolicy()
    reliability = ReliabilityControls()

    platform_state = {
        "registry": registry,
        "router": router,
        "vertical_agents": vertical_agents,
        "reputation": reputation,
        # reputation_engine alias used by marketplace blueprint staking endpoints
        "reputation_engine": reputation,
        "settlement": settlement,
        "policy": policy,
        "reliability": reliability,
        "skill_vertical_map": dict(SKILL_VERTICAL_MAP),
        "registered_cards": registered,
    }
    app.extensions["sincor_platform"] = platform_state

    _extend_a2a_skills_from_registry(registry)

    logger.info(
        "Platform bootstrap complete: %s agent cards, %s vertical agents, "
        "settlement + policy + reliability initialized",
        registered,
        len(vertical_agents),
    )
    return platform_state


def _extend_a2a_skills_from_registry(registry: AgentCardRegistry) -> None:
    """Append vertical marketplace skills to the live A2A skill catalogue."""
    try:
        from sincor2.a2a_integration import AgentSkill, SINCOR_SKILLS
    except ImportError:
        return

    existing_ids = {skill.id for skill in SINCOR_SKILLS}
    for record in registry.list_all():
        for skill in record.skills:
            skill_id = skill.get("id")
            if not skill_id or skill_id in existing_ids:
                continue
            SINCOR_SKILLS.append(
                AgentSkill(
                    id=skill_id,
                    name=skill.get("name", skill_id),
                    description=skill.get("description", ""),
                    tags=list(skill.get("tags", [])),
                    examples=list(skill.get("examples", [])),
                )
            )
            existing_ids.add(skill_id)