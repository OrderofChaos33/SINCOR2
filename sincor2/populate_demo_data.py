"""
Populate SINCOR database with demo agent data
Run this to test the agent observability dashboard
"""

from database import db
from datetime import datetime, timedelta
import random
import uuid

def populate_demo_data():
    """Populate database with demo agent metrics and interactions"""

    print("Populating demo data...")

    # Sample agent data
    agents = [
        {'agent_id': 'E-vega-01', 'agent_name': 'Vega Prime', 'archetype': 'Scout'},
        {'agent_id': 'E-altair-02', 'agent_name': 'Altair Navigator', 'archetype': 'Scout'},
        {'agent_id': 'E-deneb-03', 'agent_name': 'Deneb Synthesizer', 'archetype': 'Synthesizer'},
        {'agent_id': 'E-sirius-04', 'agent_name': 'Sirius Builder', 'archetype': 'Builder'},
        {'agent_id': 'E-rigel-05', 'agent_name': 'Rigel Constructor', 'archetype': 'Builder'},
        {'agent_id': 'E-betelgeuse-06', 'agent_name': 'Betelgeuse Negotiator', 'archetype': 'Negotiator'},
        {'agent_id': 'E-aldebaran-07', 'agent_name': 'Aldebaran Caretaker', 'archetype': 'Caretaker'},
        {'agent_id': 'E-antares-08', 'agent_name': 'Antares Auditor', 'archetype': 'Auditor'},
        {'agent_id': 'E-fomalhaut-09', 'agent_name': 'Fomalhaut Director', 'archetype': 'Director'},
    ]

    # Create metrics for last 24 hours
    current_time = datetime.utcnow()

    for i in range(50):  # 50 time points
        timestamp = current_time - timedelta(hours=24 - (i * 0.5))

        for agent in agents:
            # Random but realistic metrics
            tasks_completed = random.randint(5, 25)
            tasks_failed = random.randint(0, 3)
            total_tasks = tasks_completed + tasks_failed
            success_rate = (tasks_completed / total_tasks * 100) if total_tasks > 0 else 0

            metric_data = {
                'agent_id': agent['agent_id'],
                'agent_name': agent['agent_name'],
                'archetype': agent['archetype'],
                'tasks_completed': tasks_completed,
                'tasks_failed': tasks_failed,
                'success_rate': success_rate,
                'avg_response_time': random.uniform(100, 500),
                'quality_score': random.uniform(75, 98),
                'continuity_index': random.uniform(0.95, 1.0),
                'cpu_usage': random.uniform(5, 40),
                'memory_usage': random.uniform(20, 70),
                'status': random.choice(['active', 'active', 'active', 'idle']),  # Mostly active
                'health_score': random.uniform(85, 100),
                'metadata': {'timestamp_override': timestamp.isoformat()}
            }

            db.record_agent_metric(metric_data)

    print(f"Created {50 * len(agents)} agent metrics")

    # Create agent interactions
    interaction_types = ['task_handoff', 'collaboration', 'query', 'response']

    for i in range(100):  # 100 interactions
        source = random.choice(agents)
        target = random.choice([a for a in agents if a['agent_id'] != source['agent_id']])

        interaction_data = {
            'source_agent_id': source['agent_id'],
            'source_agent_name': source['agent_name'],
            'target_agent_id': target['agent_id'],
            'target_agent_name': target['agent_name'],
            'interaction_type': random.choice(interaction_types),
            'task_id': f'task-{uuid.uuid4().hex[:8]}',
            'task_description': f'Collaborative task between {source["agent_name"]} and {target["agent_name"]}',
            'success': random.choice([True, True, True, False]),  # Mostly successful
            'duration_ms': random.uniform(50, 500),
            'metadata': {}
        }

        db.record_interaction(interaction_data)

    print(f"Created 100 agent interactions")

    # Create agent tasks
    task_types = ['research', 'analysis', 'generation', 'optimization', 'validation']
    statuses = ['completed', 'in_progress', 'pending', 'failed']
    priorities = ['low', 'medium', 'high', 'critical']

    for i in range(150):  # 150 tasks
        agent = random.choice(agents)
        task_id = f'task-{uuid.uuid4().hex[:8]}'
        status = random.choice(statuses)

        task_data = {
            'task_id': task_id,
            'agent_id': agent['agent_id'],
            'agent_name': agent['agent_name'],
            'archetype': agent['archetype'],
            'task_type': random.choice(task_types),
            'task_description': f'{random.choice(task_types).title()} task for {agent["agent_name"]}',
            'priority': random.choice(priorities),
            'status': status,
            'success': status == 'completed',
            'quality_score': random.uniform(75, 98) if status == 'completed' else None,
            'duration_ms': random.uniform(100, 2000) if status in ['completed', 'failed'] else None,
            'metadata': {}
        }

        db.create_task(task_data)

        # Update task with completion data if completed
        if status in ['completed', 'failed']:
            db.update_task(task_id, {
                'completed_at': datetime.utcnow(),
                'started_at': datetime.utcnow() - timedelta(seconds=random.randint(10, 300))
            })

    print(f"Created 150 agent tasks")

    # Create system metrics
    for i in range(100):  # Last 100 data points
        timestamp = current_time - timedelta(minutes=100 - i)

        metric_data = {
            'cpu_percent': random.uniform(5, 45),
            'memory_percent': random.uniform(60, 90),
            'disk_percent': random.uniform(30, 60),
            'active_agents': random.randint(len(agents) - 2, len(agents)),
            'active_tasks': random.randint(10, 50),
            'requests_per_sec': random.uniform(5, 25),
            'errors_per_min': random.uniform(0, 2),
            'avg_response_time_ms': random.uniform(100, 400),
            'threats_blocked': random.randint(0, 5),
            'rate_limit_hits': random.randint(0, 10),
            'metadata': {}
        }

        db.record_system_metric(metric_data)

    print(f"Created 100 system metrics")

    print("\nDemo data populated successfully!")
    print("\nYou can now:")
    print("1. Start the app: python app.py")
    print("2. Visit: http://localhost:5000/agent-observability")
    print("3. Explore the agent analytics and interaction visualizations!")


if __name__ == '__main__':
    populate_demo_data()
