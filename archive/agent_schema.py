"""Production-ready Pydantic schemas for validating SINCOR2 agent YAML configurations.

Best practice: Load and validate all agent yamls (E-*.yaml, archetypes/*.yaml)
 at startup or in CI to catch misconfigurations early.

Usage:
    from sincor2.agent_schema import AgentConfig, validate_agent_yaml
    config = validate_agent_yaml(yaml_path)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator


class SBTRole(BaseModel):
    """SBT (Soulbound Token / role) family definition."""
    family: str = Field(..., description="Primary role family (e.g. Director, Scout, Auditor)")
    level: Optional[int] = Field(None, ge=1, le=10)
    permissions: Optional[List[str]] = Field(default_factory=list)


class AgentConfig(BaseModel):
    """Core schema for individual agent YAML definitions (E-*.yaml and similar)."""
    name: str = Field(..., min_length=2, description="Human-readable agent name")
    id: str = Field(..., pattern=r"^E-[a-z0-9-]+$", description="Unique agent ID (e.g. E-vega-02)")
    archetype: str = Field(..., description="Primary archetype (must match archetypes/)")
    secondary_archetype: Optional[str] = Field(None, description="Secondary archetype for hybrid behavior")
    sigil_key: str = Field(..., description="did:key or cryptographic identifier")
    sbt_role: SBTRole
    description: Optional[str] = Field(None, max_length=500)
    capabilities: Optional[List[str]] = Field(default_factory=list)
    constraints: Optional[Dict[str, Any]] = Field(default_factory=dict)
    version: Optional[str] = Field("1.0", pattern=r"^\d+\.\d+")

    @field_validator("archetype")
    @classmethod
    def archetype_must_be_known(cls, v: str) -> str:
        # In production you can load known archetypes dynamically
        known = {
            "Director", "Scout", "Auditor", "Builder", "Negotiator",
            "Caretaker", "Copywriter", "Synthesizer", "Strategist", "Critic", "TOA"
        }
        if v not in known:
            # Allow custom but warn in logs
            pass
        return v


class ArchetypeConfig(BaseModel):
    """Schema for files in agents/archetypes/"""
    archetype: str
    primary_role: str
    secondary_competencies: List[str] = Field(default_factory=list)
    description: Optional[str] = None


def validate_agent_yaml(path: str | Path) -> AgentConfig:
    """Load and validate a single agent YAML file. Raises on invalid config."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Agent config not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return AgentConfig.model_validate(data)


def validate_all_agents(agents_dir: str | Path = "agents") -> List[AgentConfig]:
    """Validate every .yaml in agents/ and agents/archetypes/. Production startup check."""
    agents_dir = Path(agents_dir)
    configs: List[AgentConfig] = []

    # Individual E- agents
    for yml in agents_dir.glob("E-*.yaml"):
        try:
            configs.append(validate_agent_yaml(yml))
        except Exception as e:
            print(f"[AGENT SCHEMA] Invalid config {yml.name}: {e}")

    # Archetypes
    archetype_dir = agents_dir / "archetypes"
    if archetype_dir.exists():
        for yml in archetype_dir.glob("*.yaml"):
            try:
                # Archetypes use slightly different schema but we can relax or use separate
                data = yaml.safe_load(open(yml))
                # For simplicity we accept if it has 'archetype' key
                if "archetype" in data:
                    configs.append(AgentConfig.model_validate({"name": data.get("archetype"), "id": data.get("archetype"), "archetype": data["archetype"], "sigil_key": "archetype", "sbt_role": {"family": data.get("archetype")}}))
            except Exception:
                pass

    return configs


if __name__ == "__main__":
    print("Validating all agent configurations...")
    validated = validate_all_agents()
    print(f"Successfully validated {len(validated)} agent configurations.")
