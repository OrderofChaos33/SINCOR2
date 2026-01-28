"""
SINCOR Agent Analytics API
Real-time agent observability and interaction tracking
"""

from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta
from typing import Dict, List, Any
import json

# This will be imported from database.py
from database import db

agent_analytics_bp = Blueprint('agent_analytics', __name__)

# ============================================================
# AGENT METRICS ENDPOINTS
# ============================================================

@agent_analytics_bp.route('/api/agent-analytics/metrics', methods=['GET'])
def get_agent_metrics():
    """Get agent performance metrics"""
    agent_id = request.args.get('agent_id')
    limit = int(request.args.get('limit', 100))

    metrics = db.get_agent_metrics(agent_id=agent_id, limit=limit)

    return jsonify({
        'success': True,
        'count': len(metrics),
        'metrics': metrics
    })


@agent_analytics_bp.route('/api/agent-analytics/metrics', methods=['POST'])
def record_agent_metric():
    """Record a new agent metric"""
    data = request.get_json()

    success = db.record_agent_metric(data)

    return jsonify({
        'success': success,
        'message': 'Metric recorded' if success else 'Failed to record metric'
    })


# ============================================================
# AGENT INTERACTIONS ENDPOINTS
# ============================================================

@agent_analytics_bp.route('/api/agent-analytics/interactions', methods=['GET'])
def get_interactions():
    """Get agent interactions"""
    agent_id = request.args.get('agent_id')
    limit = int(request.args.get('limit', 100))

    interactions = db.get_interactions(agent_id=agent_id, limit=limit)

    return jsonify({
        'success': True,
        'count': len(interactions),
        'interactions': interactions
    })


@agent_analytics_bp.route('/api/agent-analytics/interactions', methods=['POST'])
def record_interaction():
    """Record a new agent interaction"""
    data = request.get_json()

    success = db.record_interaction(data)

    return jsonify({
        'success': success,
        'message': 'Interaction recorded' if success else 'Failed to record interaction'
    })


@agent_analytics_bp.route('/api/agent-analytics/interaction-graph', methods=['GET'])
def get_interaction_graph():
    """Get interaction graph data for visualization"""
    limit = int(request.args.get('limit', 500))

    interactions = db.get_interactions(limit=limit)

    # Build nodes and edges for graph visualization
    nodes = {}
    edges = []

    for interaction in interactions:
        # Add source node
        if interaction['source_agent_id'] not in nodes:
            nodes[interaction['source_agent_id']] = {
                'id': interaction['source_agent_id'],
                'name': interaction['source_agent_name'],
                'interactions': 0
            }
        nodes[interaction['source_agent_id']]['interactions'] += 1

        # Add target node
        if interaction['target_agent_id'] not in nodes:
            nodes[interaction['target_agent_id']] = {
                'id': interaction['target_agent_id'],
                'name': interaction['target_agent_name'],
                'interactions': 0
            }
        nodes[interaction['target_agent_id']]['interactions'] += 1

        # Add edge
        edges.append({
            'source': interaction['source_agent_id'],
            'target': interaction['target_agent_id'],
            'type': interaction['interaction_type'],
            'success': interaction['success'],
            'timestamp': interaction['timestamp']
        })

    return jsonify({
        'success': True,
        'nodes': list(nodes.values()),
        'edges': edges
    })


# ============================================================
# AGENT TASKS ENDPOINTS
# ============================================================

@agent_analytics_bp.route('/api/agent-analytics/tasks', methods=['GET'])
def get_tasks():
    """Get agent tasks"""
    agent_id = request.args.get('agent_id')
    status = request.args.get('status')
    limit = int(request.args.get('limit', 100))

    tasks = db.get_tasks(agent_id=agent_id, status=status, limit=limit)

    return jsonify({
        'success': True,
        'count': len(tasks),
        'tasks': tasks
    })


@agent_analytics_bp.route('/api/agent-analytics/tasks', methods=['POST'])
def create_task():
    """Create a new agent task"""
    data = request.get_json()

    task_id = db.create_task(data)

    return jsonify({
        'success': task_id is not None,
        'task_id': task_id
    })


@agent_analytics_bp.route('/api/agent-analytics/tasks/<task_id>', methods=['PUT'])
def update_task(task_id):
    """Update task status and results"""
    data = request.get_json()

    success = db.update_task(task_id, data)

    return jsonify({
        'success': success,
        'message': 'Task updated' if success else 'Task not found'
    })


# ============================================================
# SYSTEM METRICS ENDPOINTS
# ============================================================

@agent_analytics_bp.route('/api/agent-analytics/system-metrics', methods=['GET'])
def get_system_metrics():
    """Get system-wide metrics"""
    limit = int(request.args.get('limit', 100))

    metrics = db.get_system_metrics(limit=limit)

    return jsonify({
        'success': True,
        'count': len(metrics),
        'metrics': metrics
    })


@agent_analytics_bp.route('/api/agent-analytics/system-metrics', methods=['POST'])
def record_system_metric():
    """Record system metric"""
    data = request.get_json()

    success = db.record_system_metric(data)

    return jsonify({
        'success': success,
        'message': 'System metric recorded' if success else 'Failed to record metric'
    })


# ============================================================
# ANALYTICS & AGGREGATIONS
# ============================================================

@agent_analytics_bp.route('/api/agent-analytics/analytics', methods=['GET'])
def get_analytics():
    """Get aggregated agent analytics"""
    agent_id = request.args.get('agent_id')

    analytics = db.get_agent_analytics(agent_id=agent_id)

    return jsonify({
        'success': True,
        'analytics': analytics
    })


@agent_analytics_bp.route('/api/agent-analytics/agent-summary', methods=['GET'])
def get_agent_summary():
    """Get summary of all agents"""
    try:
        # Get recent metrics for all agents
        all_metrics = db.get_agent_metrics(limit=500)

        # Group by agent_id and get latest metric
        agent_summary = {}
        for metric in all_metrics:
            agent_id = metric['agent_id']
            if agent_id not in agent_summary:
                agent_summary[agent_id] = metric

        # Get task analytics for each agent
        for agent_id, agent_data in agent_summary.items():
            task_analytics = db.get_agent_analytics(agent_id=agent_id)
            agent_data['task_analytics'] = task_analytics

        return jsonify({
            'success': True,
            'agent_count': len(agent_summary),
            'agents': list(agent_summary.values())
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@agent_analytics_bp.route('/api/agent-analytics/archetype-summary', methods=['GET'])
def get_archetype_summary():
    """Get performance summary by archetype"""
    try:
        all_metrics = db.get_agent_metrics(limit=500)

        archetype_data = {}

        for metric in all_metrics:
            archetype = metric['archetype']
            if archetype not in archetype_data:
                archetype_data[archetype] = {
                    'archetype': archetype,
                    'agent_count': 0,
                    'total_tasks': 0,
                    'avg_success_rate': 0,
                    'avg_quality_score': 0,
                    'avg_health_score': 0,
                    'agents': set()
                }

            data = archetype_data[archetype]
            data['agents'].add(metric['agent_id'])
            data['total_tasks'] += metric['tasks_completed'] + metric['tasks_failed']
            data['avg_success_rate'] += metric['success_rate']
            data['avg_quality_score'] += metric['quality_score']
            data['avg_health_score'] += metric['health_score']

        # Calculate averages
        for archetype, data in archetype_data.items():
            agent_count = len(data['agents'])
            data['agent_count'] = agent_count
            if agent_count > 0:
                data['avg_success_rate'] /= agent_count
                data['avg_quality_score'] /= agent_count
                data['avg_health_score'] /= agent_count
            data['agents'] = list(data['agents'])  # Convert set to list for JSON

        return jsonify({
            'success': True,
            'archetype_count': len(archetype_data),
            'archetypes': list(archetype_data.values())
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@agent_analytics_bp.route('/api/agent-analytics/activity-timeline', methods=['GET'])
def get_activity_timeline():
    """Get agent activity over time"""
    hours = int(request.args.get('hours', 24))
    agent_id = request.args.get('agent_id')

    try:
        # Get tasks from the last N hours
        tasks = db.get_tasks(agent_id=agent_id, limit=1000)

        # Group by hour
        timeline = {}
        for task in tasks:
            timestamp = datetime.fromisoformat(task['created_at'])
            hour_key = timestamp.strftime('%Y-%m-%d %H:00')

            if hour_key not in timeline:
                timeline[hour_key] = {
                    'timestamp': hour_key,
                    'total_tasks': 0,
                    'completed_tasks': 0,
                    'failed_tasks': 0,
                    'avg_duration_ms': 0,
                    'durations': []
                }

            timeline[hour_key]['total_tasks'] += 1
            if task['status'] == 'completed':
                timeline[hour_key]['completed_tasks'] += 1
            elif task['status'] == 'failed':
                timeline[hour_key]['failed_tasks'] += 1

            if task['duration_ms']:
                timeline[hour_key]['durations'].append(task['duration_ms'])

        # Calculate averages
        for hour, data in timeline.items():
            if data['durations']:
                data['avg_duration_ms'] = sum(data['durations']) / len(data['durations'])
            del data['durations']  # Remove raw durations from response

        # Sort by timestamp
        sorted_timeline = sorted(timeline.values(), key=lambda x: x['timestamp'])

        return jsonify({
            'success': True,
            'hours': hours,
            'data_points': len(sorted_timeline),
            'timeline': sorted_timeline
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@agent_analytics_bp.route('/api/agent-analytics/health-check', methods=['GET'])
def analytics_health_check():
    """Check analytics system health"""
    try:
        # Test database connection
        metrics_count = len(db.get_agent_metrics(limit=1))
        interactions_count = len(db.get_interactions(limit=1))
        tasks_count = len(db.get_tasks(limit=1))

        return jsonify({
            'success': True,
            'database_type': db.db_type,
            'database_connected': True,
            'metrics_available': metrics_count > 0,
            'interactions_available': interactions_count > 0,
            'tasks_available': tasks_count > 0
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'database_connected': False,
            'error': str(e)
        }), 500
