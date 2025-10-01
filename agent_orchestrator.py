"""
SINCOR Agent Orchestration System
Coordinates all 43 AI agents and engine outputs
"""

import os
import json
import yaml
from datetime import datetime
from pathlib import Path
import asyncio
from typing import Dict, List, Any


class AgentOrchestrator:
    """Orchestrates all SINCOR agents and manages output generation"""

    def __init__(self):
        self.agents_dir = Path(__file__).parent / "agents"
        self.outputs_dir = Path(__file__).parent / "outputs"
        self.outputs_dir.mkdir(exist_ok=True)

        self.agents = {}
        self.archetypes = {}
        self.active_tasks = []

        # Load all agent configurations
        self._load_agents()
        self._load_archetypes()

    def _load_agents(self):
        """Load all 43 E-series agent configurations"""
        agent_files = list(self.agents_dir.glob("E-*.yaml"))

        for agent_file in agent_files:
            with open(agent_file, 'r') as f:
                config = yaml.safe_load(f)
                agent_id = agent_file.stem
                self.agents[agent_id] = config

        print(f"Loaded {len(self.agents)} agents")

    def _load_archetypes(self):
        """Load agent archetypes"""
        archetype_dir = self.agents_dir / "archetypes"
        if archetype_dir.exists():
            for archetype_file in archetype_dir.glob("*.yaml"):
                with open(archetype_file, 'r') as f:
                    config = yaml.safe_load(f)
                    archetype_id = archetype_file.stem
                    self.archetypes[archetype_id] = config

        print(f"Loaded {len(self.archetypes)} archetypes")

    def assign_task(self, task_type: str, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Assign a task to the most suitable agent"""

        # Match task to archetype
        archetype_mapping = {
            'analysis': 'Auditor',
            'creation': 'Builder',
            'maintenance': 'Caretaker',
            'coordination': 'Director',
            'sales': 'Negotiator',
            'research': 'Scout',
            'integration': 'Synthesizer'
        }

        archetype = archetype_mapping.get(task_type, 'Synthesizer')

        # Find available agent with matching archetype
        suitable_agents = [
            agent_id for agent_id, config in self.agents.items()
            if config.get('archetype') == archetype
        ]

        if not suitable_agents:
            suitable_agents = list(self.agents.keys())[:5]  # Default to first 5

        assigned_agent = suitable_agents[0] if suitable_agents else None

        task_record = {
            'task_id': f"task_{len(self.active_tasks) + 1}",
            'task_type': task_type,
            'assigned_agent': assigned_agent,
            'archetype': archetype,
            'status': 'assigned',
            'created_at': datetime.now().isoformat(),
            'task_data': task_data
        }

        self.active_tasks.append(task_record)

        return task_record

    def generate_output(self, task_record: Dict[str, Any]) -> str:
        """Generate output for a completed task"""

        output_filename = f"{task_record['task_id']}_{task_record['task_type']}.json"
        output_path = self.outputs_dir / output_filename

        output_data = {
            'task_id': task_record['task_id'],
            'task_type': task_record['task_type'],
            'agent': task_record['assigned_agent'],
            'archetype': task_record['archetype'],
            'completed_at': datetime.now().isoformat(),
            'result': {
                'status': 'completed',
                'insights': f"Analysis completed by {task_record['assigned_agent']}",
                'recommendations': [
                    'Continue monitoring key metrics',
                    'Optimize resource allocation',
                    'Scale successful patterns'
                ],
                'metrics': {
                    'efficiency_score': 94,
                    'quality_score': 89,
                    'completion_time_ms': 1250
                }
            }
        }

        with open(output_path, 'w') as f:
            json.dump(output_data, f, indent=2)

        return str(output_path)

    def run_demo_workflow(self) -> List[str]:
        """Run a demonstration workflow generating multiple outputs"""

        print("\n[WORKFLOW] Starting Agent Orchestration Demo Workflow...")

        # Create sample tasks
        tasks = [
            {
                'type': 'analysis',
                'data': {'target': 'customer_engagement', 'timeframe': '30d'}
            },
            {
                'type': 'creation',
                'data': {'content_type': 'marketing_copy', 'audience': 'B2B'}
            },
            {
                'type': 'research',
                'data': {'topic': 'market_trends', 'industry': 'AI_automation'}
            },
            {
                'type': 'coordination',
                'data': {'teams': ['sales', 'marketing', 'support'], 'goal': 'Q4_campaign'}
            },
            {
                'type': 'sales',
                'data': {'lead_score': 85, 'company_size': 'enterprise'}
            }
        ]

        generated_outputs = []

        for task in tasks:
            # Assign task
            task_record = self.assign_task(task['type'], task['data'])
            print(f"[OK] Assigned {task['type']} task to {task_record['assigned_agent']}")

            # Generate output
            output_path = self.generate_output(task_record)
            generated_outputs.append(output_path)
            print(f"  -> Output: {output_path}")

        return generated_outputs

    def get_agent_status(self) -> Dict[str, Any]:
        """Get current status of all agents"""
        return {
            'total_agents': len(self.agents),
            'total_archetypes': len(self.archetypes),
            'active_tasks': len(self.active_tasks),
            'agents_list': list(self.agents.keys())[:10],  # First 10
            'archetypes_list': list(self.archetypes.keys())
        }

    def generate_report(self) -> str:
        """Generate comprehensive orchestration report"""

        report = {
            'report_id': f"orchestration_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            'timestamp': datetime.now().isoformat(),
            'system_status': {
                'total_agents': len(self.agents),
                'total_archetypes': len(self.archetypes),
                'total_tasks_processed': len(self.active_tasks),
                'outputs_generated': len(list(self.outputs_dir.glob("*.json")))
            },
            'agent_summary': self.get_agent_status(),
            'recent_tasks': self.active_tasks[-5:] if self.active_tasks else []
        }

        report_path = self.outputs_dir / f"orchestration_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2)

        return str(report_path)


def main():
    """Run the orchestration system"""

    print("=" * 60)
    print("SINCOR AGENT ORCHESTRATION SYSTEM")
    print("=" * 60)

    orchestrator = AgentOrchestrator()

    # Show status
    status = orchestrator.get_agent_status()
    print(f"\n[STATUS] System Status:")
    print(f"  - Total Agents: {status['total_agents']}")
    print(f"  - Total Archetypes: {status['total_archetypes']}")
    print(f"  - Archetypes: {', '.join(status['archetypes_list'])}")

    # Run demo workflow
    outputs = orchestrator.run_demo_workflow()

    print(f"\n[SUCCESS] Generated {len(outputs)} outputs:")
    for output in outputs:
        print(f"  - {output}")

    # Generate report
    report_path = orchestrator.generate_report()
    print(f"\n[REPORT] Orchestration report: {report_path}")

    print("\n" + "=" * 60)
    print("ORCHESTRATION COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
