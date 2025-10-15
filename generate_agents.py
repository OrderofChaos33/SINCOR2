#!/usr/bin/env python3
"""
Generate 43 agent identity stubs for SINCOR swarm architecture.
Each agent gets a unique star name, archetype assignment, and personalized configuration.
"""

import yaml
import os
from datetime import datetime

# Agent definitions: name, archetype, secondary_archetype, specializations
AGENTS = [
    # Scout agents (8)
    ("Auriga", "Scout", "Synthesizer", ["market_intelligence", "competitive_analysis"]),
    ("Vega", "Scout", "Negotiator", ["lead_prospecting", "contact_discovery"]),  
    ("Rigel", "Scout", "Builder", ["technical_research", "api_discovery"]),
    ("Altair", "Scout", "Caretaker", ["data_source_validation", "quality_monitoring"]),
    ("Spica", "Scout", "Auditor", ["compliance_scanning", "risk_identification"]),
    ("Deneb", "Scout", "Director", ["strategic_intelligence", "market_analysis"]),
    ("Capella", "Scout", "Synthesizer", ["news_monitoring", "trend_tracking"]),
    ("Sirius", "Scout", "Negotiator", ["relationship_mapping", "influence_analysis"]),
    
    # Synthesizer agents (6)
    ("Polaris", "Synthesizer", "Director", ["executive_briefings", "strategic_synthesis"]),
    ("Arcturus", "Synthesizer", "Scout", ["intelligence_fusion", "threat_analysis"]),
    ("Betelgeuse", "Synthesizer", "Builder", ["technical_documentation", "system_analysis"]),
    ("Aldebaran", "Synthesizer", "Auditor", ["compliance_reporting", "risk_summaries"]),
    ("Antares", "Synthesizer", "Caretaker", ["data_curation", "knowledge_organization"]),
    ("Procyon", "Synthesizer", "Negotiator", ["stakeholder_communications", "proposal_writing"]),
    
    # Builder agents (7)
    ("Canopus", "Builder", "Director", ["architecture_design", "system_integration"]),
    ("Achernar", "Builder", "Scout", ["automation_discovery", "tool_development"]),
    ("Bellatrix", "Builder", "Synthesizer", ["data_pipeline_development", "analytics_tools"]),
    ("Castor", "Builder", "Negotiator", ["client_tools", "integration_apis"]),
    ("Pollux", "Builder", "Caretaker", ["maintenance_automation", "monitoring_systems"]),
    ("Regulus", "Builder", "Auditor", ["testing_frameworks", "quality_tools"]),
    ("Mizar", "Builder", "Builder", ["core_development", "infrastructure"]),
    
    # Negotiator agents (6) 
    ("Fomalhaut", "Negotiator", "Director", ["partnership_negotiations", "strategic_deals"]),
    ("Acrux", "Negotiator", "Scout", ["lead_qualification", "initial_outreach"]),
    ("Mimosa", "Negotiator", "Synthesizer", ["proposal_development", "value_articulation"]),
    ("Gacrux", "Negotiator", "Builder", ["technical_sales", "solution_architecture"]),
    ("Shaula", "Negotiator", "Caretaker", ["client_success", "relationship_maintenance"]),
    ("Kaus", "Negotiator", "Auditor", ["contract_negotiation", "compliance_discussions"]),
    
    # Caretaker agents (5)
    ("Alkaid", "Caretaker", "Auditor", ["compliance_maintenance", "audit_preparation"]),
    ("Dubhe", "Caretaker", "Builder", ["system_maintenance", "automation_oversight"]),
    ("Merak", "Caretaker", "Synthesizer", ["knowledge_management", "documentation_curation"]),
    ("Phecda", "Caretaker", "Scout", ["data_source_monitoring", "quality_assurance"]),
    ("Megrez", "Caretaker", "Director", ["operational_excellence", "process_optimization"]),
    
    # Auditor agents (4)
    ("Alioth", "Auditor", "Director", ["governance_oversight", "strategic_compliance"]),
    ("Meback", "Auditor", "Synthesizer", ["audit_reporting", "findings_analysis"]),
    ("Benetnash", "Auditor", "Caretaker", ["operational_audits", "process_compliance"]),
    ("Cor_Caroli", "Auditor", "Builder", ["technical_audits", "security_reviews"]),
    
    # Director agents (7)
    ("Alphard", "Director", "Negotiator", ["strategic_partnerships", "market_coordination"]),
    ("Alpheratz", "Director", "Scout", ["intelligence_coordination", "mission_planning"]),
    ("Mirach", "Director", "Synthesizer", ["information_architecture", "knowledge_strategy"]),
    ("Almaak", "Director", "Builder", ["technology_strategy", "development_coordination"]),
    ("Hamal", "Director", "Caretaker", ["operations_management", "quality_governance"]),
    ("Sheratan", "Director", "Auditor", ["risk_management", "compliance_strategy"]),
    ("Mesarthim", "Director", "Director", ["executive_coordination", "strategic_oversight"])
]

def generate_persona_variations(archetype_base, agent_index):
    """Generate slight persona variations from archetype defaults"""
    
    # Base persona values by archetype
    persona_bases = {
        "Scout": {"O": 0.85, "C": 0.65, "E": 0.40, "A": 0.70, "N": 0.30},
        "Synthesizer": {"O": 0.75, "C": 0.85, "E": 0.30, "A": 0.60, "N": 0.25},
        "Builder": {"O": 0.70, "C": 0.90, "E": 0.25, "A": 0.65, "N": 0.20},
        "Negotiator": {"O": 0.60, "C": 0.70, "E": 0.85, "A": 0.80, "N": 0.35},
        "Caretaker": {"O": 0.45, "C": 0.95, "E": 0.20, "A": 0.85, "N": 0.15},
        "Auditor": {"O": 0.55, "C": 0.90, "E": 0.35, "A": 0.50, "N": 0.25},
        "Director": {"O": 0.75, "C": 0.80, "E": 0.70, "A": 0.65, "N": 0.25}
    }
    
    base = persona_bases[archetype_base]
    
    # Add small variations (+/- 0.05) based on agent index
    variations = {}
    for trait, value in base.items():
        # Use agent_index to create consistent but different variations
        variation = ((agent_index * 7) % 11 - 5) * 0.01  # Range: -0.05 to +0.05
        new_value = max(0.1, min(1.0, value + variation))  # Clamp to valid range
        variations[trait] = round(new_value, 2)
    
    return variations

def generate_agent_config(name, archetype, secondary_archetype, specializations, index):
    """Generate a complete agent configuration"""
    
    agent_id = f"E-{name.lower().replace('_', '-')}-{index:02d}"
    
    config = {
        "name": name,
        "id": agent_id,
        "archetype": archetype,
        "secondary_archetype": secondary_archetype,
        
        # Identity & Authority
        "sigil_key": f"did:key:z6Mk{name}{index:03d}...",  # Placeholder
        "sbt_role": {
            "family": archetype,
            "grade": 1 if index <= 10 else 2,  # First 10 agents start at grade 2
            "competencies": specializations[:4]  # Limit to 4 competencies
        },
        "constitution_refs": [
            "constitution/global.md", 
            f"agents/archetypes/{archetype}.yaml"
        ],
        
        # Persona with variations
        "persona": {
            "traits": generate_persona_variations(archetype, index),
            "style": {
                "risk": round(0.3 + (index % 7) * 0.05, 2),
                "humor": round(0.2 + (index % 5) * 0.1, 2), 
                "directness": round(0.6 + (index % 4) * 0.1, 2)
            },
            "modality": {
                "code": round(0.2 + ((index * 3) % 8) * 0.1, 2),
                "tables": round(0.5 + ((index * 2) % 5) * 0.1, 2),
                "story": round(0.2 + ((index * 5) % 7) * 0.1, 2)
            }
        },
        
        # Individual specializations
        "specializations": specializations,
        
        # Budget allocations (vary by archetype)
        "budgets": {
            "daily_tokens": {
                "Scout": 12000, "Synthesizer": 15000, "Builder": 20000,
                "Negotiator": 18000, "Caretaker": 10000, "Auditor": 14000,
                "Director": 25000
            }[archetype],
            "tool_calls": {
                "Scout": 200, "Synthesizer": 180, "Builder": 300,
                "Negotiator": 250, "Caretaker": 150, "Auditor": 200,
                "Director": 400
            }[archetype],
            "play_time_mins": {
                "Scout": 30, "Synthesizer": 25, "Builder": 40,
                "Negotiator": 35, "Caretaker": 20, "Auditor": 15,
                "Director": 45
            }[archetype]
        },
        
        # Memory limits
        "memory_limits": {
            "episodic_days": {
                "Scout": 21, "Synthesizer": 28, "Builder": 14,
                "Negotiator": 35, "Caretaker": 60, "Auditor": 90,
                "Director": 45
            }[archetype],
            "semantic_items": {
                "Scout": 15000, "Synthesizer": 20000, "Builder": 25000,
                "Negotiator": 12000, "Caretaker": 18000, "Auditor": 22000,
                "Director": 30000
            }[archetype]
        },
        
        # Status
        "status": "Hatch",
        "created_date": datetime.now().strftime("%Y-%m-%d"),
        "constitution_sha256": f"hash_{agent_id}_{archetype.lower()}"
    }
    
    return config

def main():
    """Generate all 43 agent configurations"""
    
    agents_dir = "agents"
    os.makedirs(agents_dir, exist_ok=True)
    
    print("Generating 43 SINCOR agents...")
    
    for index, (name, archetype, secondary, specializations) in enumerate(AGENTS, 1):
        config = generate_agent_config(name, archetype, secondary, specializations, index)
        
        filename = f"{agents_dir}/{config['id']}.yaml"
        
        with open(filename, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False, indent=2)
        
        print(f"Created {filename} - {name} ({archetype}/{secondary})")
    
    print(f"\nGenerated {len(AGENTS)} agents across 7 archetypes:")
    
    # Count by archetype
    archetype_counts = {}
    for _, archetype, _, _ in AGENTS:
        archetype_counts[archetype] = archetype_counts.get(archetype, 0) + 1
    
    for archetype, count in sorted(archetype_counts.items()):
        print(f"  {archetype}: {count} agents")
    
    print("\nAll agents ready for persona sculpting and deployment!")

if __name__ == "__main__":
    main()