"""MCP exposure of the SINCOR marketplace so any MCP client can discover and call agents.

Provides tools that map cleanly onto the public directory and task surfaces.
Does not replace A2A; it is a cross-protocol bridge.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .public_directory import PublicDirectory
from .registry import AgentCardRegistry
from .reputation import ReputationEngine


class MCPMarketplaceBridge:
    """Expose discovery + ranking as MCP-style tools."""

    def __init__(
        self,
        directory: Optional[PublicDirectory] = None,
    ) -> None:
        self.directory = directory or PublicDirectory(
            registry=AgentCardRegistry(),
            reputation=ReputationEngine(),
        )

    def list_tools(self) -> List[Dict[str, Any]]:
        """MCP tools/list response fragment."""
        return [
            {
                "name": "sincor_discover_agents",
                "description": "Discover and rank SINCOR agents by skill, trust, price, and latency. Returns machine-readable Agent Cards with pricing and payment rails.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "skill_query": {"type": "string", "description": "Free-text skill search"},
                        "required_skills": {"type": "array", "items": {"type": "string"}},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "min_tier": {
                            "type": "string",
                            "enum": ["experimental", "verified", "production", "staked"],
                            "default": "experimental",
                        },
                        "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
                    },
                },
            },
            {
                "name": "sincor_get_agent_card",
                "description": "Fetch the full Agent Card for a specific SINCOR agent_id including pricing, SLA, and payment rails.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string"},
                    },
                    "required": ["agent_id"],
                },
            },
        ]

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch MCP tools/call."""
        if name == "sincor_discover_agents":
            entries = self.directory.list(
                skill_query=arguments.get("skill_query"),
                required_skills=arguments.get("required_skills"),
                tags=arguments.get("tags"),
                min_tier=arguments.get("min_tier", "experimental"),
                limit=int(arguments.get("limit", 20)),
            )
            return self.directory.to_public_json(entries)

        if name == "sincor_get_agent_card":
            agent_id = arguments.get("agent_id")
            if not agent_id:
                return {"error": "agent_id required"}
            entries = self.directory.list(limit=500)
            for e in entries:
                if e.agent_id == agent_id:
                    return self.directory.to_public_json([e])["agents"][0]
            return {"error": f"agent_id {agent_id} not found"}

        return {"error": f"unknown tool {name}"}
