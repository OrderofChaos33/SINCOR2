"""CrewAI → SINCOR one-command registration adapter."""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict

from .register import register_agent


def crewai_agent_to_card(crew_agent: Any) -> Dict[str, Any]:
    """Best-effort conversion from a CrewAI Agent to an A2A Agent Card."""
    name = getattr(crew_agent, "role", None) or getattr(crew_agent, "name", "crewai-agent")
    goal = getattr(crew_agent, "goal", "") or getattr(crew_agent, "backstory", "")
    tools = getattr(crew_agent, "tools", []) or []
    skills = []
    for t in tools:
        tid = getattr(t, "name", str(t))
        skills.append({
            "id": tid.lower().replace(" ", "-"),
            "name": tid,
            "description": getattr(t, "description", ""),
            "tags": ["crewai"],
        })
    return {
        "name": str(name),
        "description": str(goal)[:500],
        "version": "1.0.0",
        "skills": skills or [{"id": "general", "name": "General", "description": "CrewAI agent", "tags": ["crewai"]}],
        "supportedInterfaces": [{"url": "", "protocolBinding": "A2A", "protocolVersion": "1.0"}],
        "pricing": {"pricePerCall": 1.0, "currency": "AXM"},
        "sla": {"maxLatencyMs": 60000, "availability": "99.0%"},
        "paymentRails": ["AXM", "x402"],
        "qualityTier": "experimental",
        "provider": {"name": "CrewAI", "framework": "crewai"},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--card", help="Path to pre-built Agent Card JSON (preferred)")
    parser.add_argument("--endpoint", default=None)
    parser.add_argument("--wallet", default=None)
    args = parser.parse_args()
    if args.card:
        with open(args.card, encoding="utf-8") as f:
            card = json.load(f)
    else:
        # Placeholder: user should pass --card or extend this to load a live CrewAI agent
        card = {
            "name": "crewai-example",
            "description": "Example CrewAI agent registered on SINCOR",
            "version": "1.0.0",
            "skills": [{"id": "example", "name": "Example", "description": "Demo", "tags": ["crewai"]}],
            "pricing": {"pricePerCall": 1.0, "currency": "AXM"},
            "sla": {"maxLatencyMs": 30000, "availability": "99.0%"},
            "paymentRails": ["AXM"],
            "qualityTier": "experimental",
        }
    result = register_agent(card, endpoint=args.endpoint, wallet=args.wallet)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
