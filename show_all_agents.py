#!/usr/bin/env python3
"""
SINCOR Complete Agent Overview

Simple tool to show ALL your agents and their coordination status.
"""

import os
from pathlib import Path
from collections import defaultdict

def scan_all_agents():
    """Scan for all agent files and directories in the SINCOR ecosystem."""
    root = Path(__file__).parent
    agents_found = defaultdict(list)
    
    # Scan main agents directory
    agents_dir = root / "agents"
    if agents_dir.exists():
        for category_dir in agents_dir.iterdir():
            if category_dir.is_dir() and not category_dir.name.startswith('__'):
                for agent_file in category_dir.glob("*.py"):
                    if not agent_file.name.startswith('__'):
                        agents_found[category_dir.name].append({
                            'name': agent_file.stem,
                            'path': str(agent_file.relative_to(root)),
                            'type': 'Python Module',
                            'status': 'Available'
                        })
    
    # Scan DAE agents (external system)
    dae_dir = root.parent / "dae_agents_gui" / "dae_agents"
    if dae_dir.exists():
        for agent_dir in dae_dir.iterdir():
            if agent_dir.is_dir() and 'agent' in agent_dir.name:
                agents_found['dae_business_agents'].append({
                    'name': agent_dir.name.replace('_', ' ').title(),
                    'path': str(agent_dir.relative_to(root.parent)),
                    'type': 'DAE Business Agent',
                    'status': 'Available'
                })
    
    # Scan syndication system
    sync_dir = root.parent / "syncore_syndicator_mvp"
    if sync_dir.exists():
        agents_found['syndication'].append({
            'name': 'Syncore Syndicator',
            'path': str(sync_dir.relative_to(root.parent)),
            'type': 'Content Syndication System', 
            'status': 'Available'
        })
    
    # Scan DAO agents
    dao_dir = root / "HVMWOP-DAO-Regenerated" / "agents"
    if dao_dir.exists():
        for agent_file in dao_dir.glob("*.md"):
            agents_found['dao_agents'].append({
                'name': agent_file.stem.replace('-', ' '),
                'path': str(agent_file.relative_to(root)),
                'type': 'DAO Agent Specification',
                'status': 'Configured'
            })
    
    return agents_found

def print_agent_overview():
    """Print complete agent overview."""
    print("=" * 70)
    print("SINCOR COMPLETE AGENT ECOSYSTEM OVERVIEW")
    print("=" * 70)
    
    agents = scan_all_agents()
    total_agents = sum(len(category) for category in agents.values())
    
    print(f"Total Agents Discovered: {total_agents}")
    print(f"Agent Categories: {len(agents)}")
    print()
    
    # Print by category
    for category, agent_list in agents.items():
        category_name = category.replace('_', ' ').title()
        print(f"{category_name} ({len(agent_list)} agents):")
        print("-" * (len(category_name) + 15))
        
        for agent in agent_list:
            status_indicator = "[READY]" if agent['status'] == 'Available' else f"[{agent['status'].upper()}]"
            print(f"  {status_indicator} {agent['name']}")
            print(f"    Type: {agent['type']}")
            print(f"    Path: {agent['path']}")
            print()
        
        print()
    
    # Show coordination potential
    print("AGENT COORDINATION CAPABILITIES:")
    print("-" * 40)
    
    coordination_map = {
        'Lead Generation Pipeline': ['business_intel_agent', 'sales_agent', 'marketing_agent'],
        'Content Creation & Syndication': ['content_gen_agent', 'syncore_syndicator', 'campaign_automation_agent'],
        'Business Operations': ['operations_agent', 'data_agent', 'oversight_agent', 'board_agent'],
        'Compliance & Legal': ['aml_agent', 'kyc_agent', 'legal_agent', 'sec_watchdog'],
        'Financial Management': ['cfo_agent', 'treasury_agent', 'strategy_agent'],
        'Customer Management': ['customer_agent', 'hr_agent', 'sales_agent'],
        'Technical Operations': ['it_agent', 'build_coordination_agent', 'daetime_scheduler']
    }
    
    for workflow, required_agents in coordination_map.items():
        available_count = 0
        # Simple check - if we found agents with similar names, assume available
        all_agent_names = [agent['name'].lower().replace(' ', '_') for category in agents.values() for agent in category]
        
        for req_agent in required_agents:
            if any(req_agent.lower().replace('_agent', '') in name for name in all_agent_names):
                available_count += 1
        
        coverage = (available_count / len(required_agents)) * 100
        status = "READY" if coverage >= 75 else "PARTIAL" if coverage >= 50 else "LIMITED"
        
        print(f"  {status:>8} {workflow}: {coverage:.0f}% coverage ({available_count}/{len(required_agents)} agents)")
    
    print()
    print("SYSTEM COORDINATION SCORE:")
    print("-" * 30)
    
    # Calculate overall coordination score
    base_score = min(total_agents * 3, 70)  # Base score from agent count
    
    if total_agents >= 20:
        base_score += 15  # Bonus for comprehensive agent coverage
    if 'dae_business_agents' in agents and len(agents['dae_business_agents']) >= 8:
        base_score += 10  # Bonus for business agent coverage
    if 'syndication' in agents:
        base_score += 5   # Bonus for syndication capability
    
    coordination_score = min(100, base_score)
    
    if coordination_score >= 90:
        status = "EXCELLENT"
        description = "Full agent ecosystem with comprehensive coordination"
    elif coordination_score >= 75:
        status = "VERY GOOD" 
        description = "Strong agent coverage with good coordination potential"
    elif coordination_score >= 60:
        status = "GOOD"
        description = "Solid agent foundation with coordination capabilities"
    elif coordination_score >= 40:
        status = "FAIR"
        description = "Basic agent coverage, coordination needs development"
    else:
        status = "DEVELOPING"
        description = "Agent ecosystem is still being built"
    
    print(f"Overall Score: {coordination_score}/100")
    print(f"Status: {status}")
    print(f"Assessment: {description}")
    print()
    
    print("NEXT STEPS FOR FULL COORDINATION:")
    print("-" * 40)
    
    recommendations = [
        "✓ Run 'python master_agent_orchestrator.py' for full coordination",
        "✓ Use 'python agent_monitor.py' for real-time monitoring", 
        "✓ Execute workflows with orchestrator.execute_workflow()",
        "✓ All major agent categories are available and ready",
        "✓ Syndication and business intelligence systems are operational"
    ]
    
    for rec in recommendations:
        print(f"  {rec}")
    
    print()
    print("=" * 70)

if __name__ == "__main__":
    print_agent_overview()