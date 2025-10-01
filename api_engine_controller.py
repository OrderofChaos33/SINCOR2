"""
SINCOR API Engine Controller
Exposes all AI engines and agents via REST API endpoints
"""

from flask import Blueprint, jsonify, request
from datetime import datetime
from typing import Dict, Any, List
import json
from pathlib import Path

# Import agent orchestrator
from agent_orchestrator import AgentOrchestrator

# Create blueprint
engine_api = Blueprint('engine_api', __name__, url_prefix='/api/engines')

# Initialize orchestrator
orchestrator = AgentOrchestrator()


# ==================== AGENT ORCHESTRATION ====================

@engine_api.route('/agents/status', methods=['GET'])
def get_agent_status():
    """Get status of all 43 agents"""
    try:
        status = orchestrator.get_agent_status()
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'data': status
        }), 200
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@engine_api.route('/agents/assign', methods=['POST'])
def assign_agent_task():
    """Assign a task to an agent"""
    try:
        data = request.get_json()

        if not data or 'task_type' not in data:
            return jsonify({
                'success': False,
                'error': 'task_type is required'
            }), 400

        task_type = data.get('task_type')
        task_data = data.get('task_data', {})

        # Assign task
        task_record = orchestrator.assign_task(task_type, task_data)

        # Generate output
        output_path = orchestrator.generate_output(task_record)

        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'task_record': task_record,
            'output_path': output_path
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@engine_api.route('/agents/archetypes', methods=['GET'])
def get_archetypes():
    """Get all agent archetypes"""
    try:
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'archetypes': list(orchestrator.archetypes.keys()),
            'details': orchestrator.archetypes
        }), 200
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@engine_api.route('/agents/list', methods=['GET'])
def list_agents():
    """List all 43 agents"""
    try:
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'total': len(orchestrator.agents),
            'agents': list(orchestrator.agents.keys()),
            'details': orchestrator.agents
        }), 200
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@engine_api.route('/agents/tasks', methods=['GET'])
def get_active_tasks():
    """Get all active tasks"""
    try:
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'total': len(orchestrator.active_tasks),
            'tasks': orchestrator.active_tasks
        }), 200
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ==================== OUTPUT MANAGEMENT ====================

@engine_api.route('/outputs/list', methods=['GET'])
def list_outputs():
    """List all generated outputs"""
    try:
        outputs_dir = Path(__file__).parent / 'outputs'

        if not outputs_dir.exists():
            return jsonify({
                'success': True,
                'total': 0,
                'outputs': []
            }), 200

        outputs = []
        for output_file in outputs_dir.glob('*.json'):
            with open(output_file, 'r') as f:
                data = json.load(f)
                outputs.append({
                    'filename': output_file.name,
                    'path': str(output_file),
                    'data': data
                })

        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'total': len(outputs),
            'outputs': outputs
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@engine_api.route('/outputs/<filename>', methods=['GET'])
def get_output(filename):
    """Get a specific output file"""
    try:
        outputs_dir = Path(__file__).parent / 'outputs'
        output_file = outputs_dir / filename

        if not output_file.exists():
            return jsonify({
                'success': False,
                'error': 'Output file not found'
            }), 404

        with open(output_file, 'r') as f:
            data = json.load(f)

        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'filename': filename,
            'data': data
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@engine_api.route('/outputs/report', methods=['GET'])
def generate_report():
    """Generate orchestration report"""
    try:
        report_path = orchestrator.generate_report()

        with open(report_path, 'r') as f:
            report_data = json.load(f)

        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'report_path': report_path,
            'report': report_data
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ==================== WORKFLOW MANAGEMENT ====================

@engine_api.route('/workflow/demo', methods=['POST'])
def run_demo_workflow():
    """Run demo workflow"""
    try:
        outputs = orchestrator.run_demo_workflow()

        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'message': 'Demo workflow completed',
            'outputs_generated': len(outputs),
            'output_paths': outputs
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@engine_api.route('/workflow/custom', methods=['POST'])
def run_custom_workflow():
    """Run custom workflow with specified tasks"""
    try:
        data = request.get_json()

        if not data or 'tasks' not in data:
            return jsonify({
                'success': False,
                'error': 'tasks array is required'
            }), 400

        tasks = data.get('tasks', [])
        generated_outputs = []

        for task in tasks:
            task_type = task.get('type')
            task_data = task.get('data', {})

            # Assign task
            task_record = orchestrator.assign_task(task_type, task_data)

            # Generate output
            output_path = orchestrator.generate_output(task_record)
            generated_outputs.append(output_path)

        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'message': f'Custom workflow completed with {len(tasks)} tasks',
            'outputs_generated': len(generated_outputs),
            'output_paths': generated_outputs
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ==================== ANALYTICS ====================

@engine_api.route('/analytics/summary', methods=['GET'])
def get_analytics_summary():
    """Get analytics summary"""
    try:
        outputs_dir = Path(__file__).parent / 'outputs'

        # Count outputs
        task_outputs = len(list(outputs_dir.glob('task_*.json')))
        reports = len(list(outputs_dir.glob('orchestration_report_*.json')))

        # Get agent utilization
        agent_usage = {}
        for task in orchestrator.active_tasks:
            agent = task.get('assigned_agent')
            if agent:
                agent_usage[agent] = agent_usage.get(agent, 0) + 1

        # Get task type distribution
        task_types = {}
        for task in orchestrator.active_tasks:
            task_type = task.get('task_type')
            if task_type:
                task_types[task_type] = task_types.get(task_type, 0) + 1

        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'analytics': {
                'total_agents': len(orchestrator.agents),
                'total_archetypes': len(orchestrator.archetypes),
                'total_tasks_processed': len(orchestrator.active_tasks),
                'task_outputs_generated': task_outputs,
                'reports_generated': reports,
                'agent_utilization': agent_usage,
                'task_type_distribution': task_types
            }
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ==================== HEALTH CHECK ====================

@engine_api.route('/health', methods=['GET'])
def engine_health():
    """Health check for engine API"""
    try:
        return jsonify({
            'success': True,
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'agents_loaded': len(orchestrator.agents),
            'archetypes_loaded': len(orchestrator.archetypes),
            'tasks_active': len(orchestrator.active_tasks)
        }), 200
    except Exception as e:
        return jsonify({
            'success': False,
            'status': 'unhealthy',
            'error': str(e)
        }), 500


# ==================== BLUEPRINT REGISTRATION ====================

def register_engine_api(app):
    """Register engine API blueprint with Flask app"""
    app.register_blueprint(engine_api)
    print(f"[ENGINE API] Registered {len(engine_api.deferred_functions)} endpoints")
    return engine_api
