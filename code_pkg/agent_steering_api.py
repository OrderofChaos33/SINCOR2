"""
SINCOR Agent Steering API
Interface for guiding autonomous agent decisions
"""

from flask import Blueprint, jsonify, request
from datetime import datetime
import json
import os

agent_steering_bp = Blueprint('agent_steering', __name__)

# In-memory storage for directives (use database in production)
agent_directives = {}
agent_autonomy_levels = {}

@agent_steering_bp.route('/api/agents/list', methods=['GET'])
def list_agents():
    """Get list of all agents"""
    try:
        agents = []
        agents_dir = 'agents'

        if os.path.exists(agents_dir):
            for filename in os.listdir(agents_dir):
                if filename.endswith('.yaml') and filename.startswith('E-'):
                    # Extract basic info from filename
                    agent_id = filename.replace('.yaml', '')
                    agent_name = agent_id.split('-')[1].capitalize()

                    # Try to load full agent data
                    try:
                        import yaml
                        with open(os.path.join(agents_dir, filename), 'r') as f:
                            agent_data = yaml.safe_load(f)

                        agents.append({
                            'id': agent_data.get('id', agent_id),
                            'name': agent_data.get('name', agent_name),
                            'archetype': agent_data.get('archetype', 'Unknown'),
                            'status': agent_data.get('status', 'Hatch'),
                            'specializations': agent_data.get('specializations', []),
                            'persona': agent_data.get('persona', {})
                        })
                    except:
                        # Fallback if YAML parsing fails
                        agents.append({
                            'id': agent_id,
                            'name': agent_name,
                            'archetype': 'Unknown',
                            'status': 'Active'
                        })

        return jsonify({
            'success': True,
            'agents': agents,
            'count': len(agents)
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@agent_steering_bp.route('/api/agents/directive', methods=['POST'])
def send_directive():
    """Send strategic directive to an agent"""
    try:
        data = request.get_json()
        agent_id = data.get('agent_id')
        directive = data.get('directive')
        priority = data.get('priority', 'medium')

        if not agent_id or not directive:
            return jsonify({
                'success': False,
                'error': 'agent_id and directive are required'
            }), 400

        # Store directive
        if agent_id not in agent_directives:
            agent_directives[agent_id] = []

        agent_directives[agent_id].append({
            'directive': directive,
            'priority': priority,
            'timestamp': datetime.utcnow().isoformat(),
            'status': 'pending'
        })

        # In a real system, this would:
        # 1. Send directive to agent's context/memory
        # 2. Update agent's decision-making parameters
        # 3. Log to database for tracking

        return jsonify({
            'success': True,
            'message': f'Directive sent to agent {agent_id}',
            'directive_id': len(agent_directives[agent_id]) - 1
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@agent_steering_bp.route('/api/agents/chat', methods=['POST'])
def agent_chat():
    """Chat with an agent"""
    try:
        data = request.get_json()
        agent_id = data.get('agent_id')
        message = data.get('message')
        conversation_history = data.get('conversation_history', [])

        if not agent_id or not message:
            return jsonify({
                'success': False,
                'error': 'agent_id and message are required'
            }), 400

        # Try to use cortecs_core for real agent response
        try:
            from cortecs_core import CortexCore
            cortex = CortexCore()

            # Get agent info
            agent_info = get_agent_info(agent_id)

            # Build prompt with agent personality
            system_prompt = f"""You are {agent_info['name']}, a {agent_info['archetype']} agent in the SINCOR autonomous AI system.

Your role: {get_archetype_description(agent_info['archetype'])}

Respond as this agent would, considering:
- Your archetype's perspective and expertise
- Your autonomous decision-making capabilities
- Your current directives and goals

Keep responses concise and in-character."""

            # Call Claude API
            response_text = cortex.reason(message, system_prompt=system_prompt)

            return jsonify({
                'success': True,
                'response': response_text,
                'agent_id': agent_id
            })

        except Exception as e:
            # Fallback to simulated response
            print(f"Claude API error: {e}, using fallback")
            agent_info = get_agent_info(agent_id)
            fallback_response = generate_fallback_response(agent_info, message)

            return jsonify({
                'success': True,
                'response': fallback_response,
                'agent_id': agent_id,
                'mode': 'simulated'
            })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@agent_steering_bp.route('/api/agents/<agent_id>/directives', methods=['GET'])
def get_agent_directives(agent_id):
    """Get all directives for an agent"""
    try:
        directives = agent_directives.get(agent_id, [])

        return jsonify({
            'success': True,
            'agent_id': agent_id,
            'directives': directives,
            'count': len(directives)
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@agent_steering_bp.route('/api/agents/<agent_id>/autonomy', methods=['POST'])
def set_autonomy_level(agent_id):
    """Set autonomy level for an agent"""
    try:
        data = request.get_json()
        level = data.get('level', 80)  # 0-100

        if not 0 <= level <= 100:
            return jsonify({
                'success': False,
                'error': 'Autonomy level must be between 0 and 100'
            }), 400

        agent_autonomy_levels[agent_id] = level

        return jsonify({
            'success': True,
            'agent_id': agent_id,
            'autonomy_level': level
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


def get_agent_info(agent_id):
    """Get agent information"""
    try:
        import yaml
        agent_file = f'agents/{agent_id}.yaml'

        if os.path.exists(agent_file):
            with open(agent_file, 'r') as f:
                agent_data = yaml.safe_load(f)
            return {
                'name': agent_data.get('name', agent_id),
                'archetype': agent_data.get('archetype', 'Unknown'),
                'specializations': agent_data.get('specializations', [])
            }
    except:
        pass

    # Fallback
    return {
        'name': agent_id.split('-')[1].capitalize() if '-' in agent_id else agent_id,
        'archetype': 'Unknown',
        'specializations': []
    }


def get_archetype_description(archetype):
    """Get description of agent archetype"""
    descriptions = {
        'Director': 'Strategic leadership and high-level decision-making. You guide the overall direction and coordinate between agents.',
        'Scout': 'Research and intelligence gathering. You explore new territories and collect valuable information.',
        'Synthesizer': 'Analysis and synthesis of information. You process data and create actionable insights.',
        'Builder': 'Development and implementation. You create solutions and build systems.',
        'Negotiator': 'Communication and collaboration. You facilitate agreements and partnerships.',
        'Caretaker': 'Maintenance and organization. You ensure systems run smoothly and data is well-organized.',
        'Auditor': 'Quality assurance and validation. You ensure high standards and catch errors.'
    }
    return descriptions.get(archetype, 'AI assistant supporting the SINCOR system.')


def generate_fallback_response(agent_info, message):
    """Generate a fallback response when Claude API is unavailable"""
    archetype = agent_info['archetype']
    name = agent_info['name']

    # Simple pattern matching for common queries
    message_lower = message.lower()

    if 'status' in message_lower or 'how are you' in message_lower:
        return f"I'm {name}, operating at full capacity. As a {archetype} agent, I'm currently monitoring my assigned tasks and ready to assist."

    elif 'what' in message_lower and ('do' in message_lower or 'can you' in message_lower):
        return f"As a {archetype} agent, I {get_archetype_description(archetype).lower()} I can help with {archetype.lower()}-related tasks and autonomous decision-making."

    elif 'help' in message_lower or 'assist' in message_lower:
        return f"I'm here to help! As {name}, a {archetype} agent, I can assist with strategic decisions in my domain. What would you like me to focus on?"

    elif 'task' in message_lower or 'project' in message_lower:
        return f"I'm ready to take on new tasks. Based on my {archetype} capabilities, I can handle autonomous execution with human oversight. Please provide your directive."

    else:
        return f"Understood. As {name} ({archetype} agent), I'm processing your request. In a fully connected system, I would analyze this with my specialized capabilities and provide a detailed autonomous response."
