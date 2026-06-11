"""Load vertical agent packs and register their Agent Cards."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Type

from verticals.agent import VerticalAgent
from verticals.compliance.agent import ComplianceAgent
from verticals.dental.agent import DentalAgent
from verticals.healthcare.agent import HealthcareAgent
from verticals.lead_gen.agent import LeadGenAgent
from verticals.trading.agent import TradingAgent

logger = logging.getLogger(__name__)

_VERTICALS_ROOT = Path(__file__).resolve().parent

VERTICAL_AGENT_CLASSES: Tuple[Type[VerticalAgent], ...] = (
    HealthcareAgent,
    DentalAgent,
    ComplianceAgent,
    TradingAgent,
    LeadGenAgent,
)

# Maps A2A skill ids (from Agent Cards or SINCOR catalogue) to vertical agents.
SKILL_VERTICAL_MAP: Dict[str, str] = {
    "healthcare-rcm": "healthcare_rcm_agent",
    "provider-credentialing": "healthcare_rcm_agent",
    "dental-ops": "dental_ops_agent",
    "dental-compliance": "dental_ops_agent",
    "regulatory-compliance": "compliance_automation_agent",
    "n8n-workflow-bridge": "compliance_automation_agent",
    "compliance-sbom": "compliance_automation_agent",
    "compliance-filing": "compliance_automation_agent",
    "openclaw-trading": "trading_intelligence_agent",
    "polymarket-execution": "trading_intelligence_agent",
    "trading-signals": "trading_intelligence_agent",
    "polymarket-eval": "trading_intelligence_agent",
    "lead-enrichment-outbound": "lead_generation_agent",
    "icp-matching": "lead_generation_agent",
    "lead-enrichment": "lead_generation_agent",
    "lead-outreach": "lead_generation_agent",
}


def load_agent_cards() -> List[dict]:
    """Return Agent Card JSON payloads from each vertical pack."""
    cards: List[dict] = []
    for pack_dir in sorted(_VERTICALS_ROOT.iterdir()):
        card_path = pack_dir / "agent_card.json"
        if not card_path.is_file():
            continue
        try:
            cards.append(json.loads(card_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping invalid agent card %s: %s", card_path, exc)
    return cards


def instantiate_vertical_agents() -> Dict[str, VerticalAgent]:
    """Instantiate all vertical agents keyed by agent name."""
    agents: Dict[str, VerticalAgent] = {}
    for agent_cls in VERTICAL_AGENT_CLASSES:
        instance = agent_cls()
        agents[instance.name] = instance
    return agents


def resolve_vertical_agent(skill_id: str, agents: Dict[str, VerticalAgent]) -> VerticalAgent | None:
    """Resolve a skill id to a vertical agent instance."""
    agent_name = SKILL_VERTICAL_MAP.get(skill_id)
    if agent_name:
        return agents.get(agent_name)

    normalized = skill_id.replace("-", "_")
    for agent in agents.values():
        if normalized in agent.name or skill_id in agent.tags:
            return agent
    return None