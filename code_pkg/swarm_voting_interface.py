#!/usr/bin/env python3
"""
SINCOR Swarm Voting Interface
Web interface for swarm democracy and decision visualization
"""

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
import asyncio
import threading
import time
import json
from swarm_intelligence_lifecycle import (
    swarm_manager, SwarmDecisionType, VoteType, 
    god_mode_scaling_request, create_lead_generation_goal
)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sincor-swarm-intelligence-2025'
socketio = SocketIO(app, cors_allowed_origins="*")

class SwarmVotingDashboard:
    """Dashboard for swarm voting and decision making"""
    
    def __init__(self):
        self.running = False
        
    async def start_voting_monitor(self):
        """Monitor swarm decisions and broadcast updates"""
        self.running = True
        
        while self.running:
            try:
                # Get current swarm status
                swarm_status = swarm_manager.get_swarm_intelligence_summary()
                
                # Get active decisions
                active_decisions = {}
                for decision_id, decision in swarm_manager.active_decisions.items():
                    active_decisions[decision_id] = {
                        'decision_id': decision.decision_id,
                        'decision_type': decision.decision_type.value,
                        'proposal': decision.proposal,
                        'vote_count': len(decision.votes),
                        'consensus_reached': decision.consensus_reached,
                        'execution_status': decision.execution_status,
                        'created_at': decision.created_at,
                        'votes': [
                            {
                                'agent_id': vote.agent_id,
                                'vote_type': vote.vote_type.value,
                                'confidence': vote.confidence,
                                'reasoning': vote.reasoning,
                                'performance_weight': vote.performance_weight
                            }
                            for vote in decision.votes
                        ]
                    }
                
                # Get active goals
                active_goals = {}
                for goal_id, goal in swarm_manager.goal_trackers.items():
                    progress_percentage = (goal.current_progress / goal.target_value) * 100
                    time_remaining = (goal.deadline - goal.deadline.__class__.now()).total_seconds()
                    
                    active_goals[goal_id] = {
                        'goal_id': goal.goal_id,
                        'description': goal.description,
                        'target_value': goal.target_value,
                        'current_progress': goal.current_progress,
                        'progress_percentage': min(100, progress_percentage),
                        'deadline': goal.deadline.isoformat(),
                        'time_remaining_hours': max(0, time_remaining / 3600),
                        'priority': goal.priority,
                        'assigned_agents': goal.assigned_agents,
                        'auto_scaling_enabled': goal.auto_scaling_enabled
                    }
                
                # Emit updates to all clients
                socketio.emit('swarm_update', {
                    'swarm_status': swarm_status,
                    'active_decisions': active_decisions,
                    'active_goals': active_goals,
                    'timestamp': time.time()
                })
                
                await asyncio.sleep(3)  # Update every 3 seconds
                
            except Exception as e:
                print(f"Voting monitor error: {e}")
                await asyncio.sleep(5)

# Global dashboard instance
voting_dashboard = SwarmVotingDashboard()

@app.route('/')
def swarm_dashboard():
    """Main swarm intelligence dashboard"""
    return render_template('swarm_voting_dashboard.html')

@app.route('/api/swarm/status')
def get_swarm_status():
    """API endpoint for swarm intelligence status"""
    return jsonify(swarm_manager.get_swarm_intelligence_summary())

@app.route('/api/decisions/active')
def get_active_decisions():
    """API endpoint for active swarm decisions"""
    decisions = {}
    for decision_id, decision in swarm_manager.active_decisions.items():
        decisions[decision_id] = {
            'decision_id': decision.decision_id,
            'decision_type': decision.decision_type.value,
            'proposal': decision.proposal,
            'vote_count': len(decision.votes),
            'consensus_reached': decision.consensus_reached,
            'execution_status': decision.execution_status
        }
    return jsonify(decisions)

@app.route('/api/goals/active')
def get_active_goals():
    """API endpoint for active goals"""
    goals = {}
    for goal_id, goal in swarm_manager.goal_trackers.items():
        progress_percentage = (goal.current_progress / goal.target_value) * 100
        goals[goal_id] = {
            'goal_id': goal.goal_id,
            'description': goal.description,
            'target_value': goal.target_value,
            'current_progress': goal.current_progress,
            'progress_percentage': min(100, progress_percentage),
            'deadline': goal.deadline.isoformat(),
            'priority': goal.priority
        }
    return jsonify(goals)

@app.route('/api/agent/vote', methods=['POST'])
def submit_agent_vote():
    """API endpoint for agent voting"""
    data = request.get_json()
    
    # Simulate agent vote submission
    asyncio.create_task(swarm_manager.submit_vote(
        agent_id=data['agent_id'],
        decision_id=data['decision_id'],
        vote_type=VoteType(data['vote_type']),
        confidence=data['confidence'],
        reasoning=data['reasoning']
    ))
    
    return jsonify({'status': 'vote_submitted'})

@app.route('/api/god/scale', methods=['POST'])
def god_mode_scale():
    """God mode instant scaling"""
    data = request.get_json()
    
    asyncio.create_task(god_mode_scaling_request(
        agent_count=data['agent_count'],
        task_type=data.get('task_type', 'general'),
        reasoning=data.get('reasoning', 'God mode scaling request')
    ))
    
    return jsonify({'status': 'god_mode_scaling_initiated'})

@app.route('/api/goal/create', methods=['POST'])
def create_goal():
    """Create new goal with auto-scaling"""
    data = request.get_json()
    
    goal_id = asyncio.create_task(create_lead_generation_goal(
        target_leads=data['target_leads'],
        deadline_days=data['deadline_days']
    ))
    
    return jsonify({'status': 'goal_created', 'goal_id': 'pending'})

@app.route('/api/goal/<goal_id>/progress', methods=['POST'])
def update_goal_progress(goal_id):
    """Update progress on a goal"""
    data = request.get_json()
    
    asyncio.create_task(swarm_manager.update_goal_progress(
        goal_id=goal_id,
        current_progress=data['current_progress']
    ))
    
    return jsonify({'status': 'progress_updated'})

@app.route('/api/decision/create', methods=['POST'])
def create_swarm_decision():
    """Create new swarm decision for voting"""
    data = request.get_json()
    
    decision_type = SwarmDecisionType(data['decision_type'])
    proposal = data['proposal']
    user_id = data.get('user_id', 'SYSTEM')
    
    decision_id = asyncio.create_task(swarm_manager.create_swarm_decision(
        decision_type=decision_type,
        proposal=proposal,
        user_id=user_id
    ))
    
    return jsonify({'status': 'decision_created', 'decision_id': 'pending'})

@socketio.on('connect')
def handle_voting_connect():
    """Handle client connection to voting dashboard"""
    print('Client connected to swarm voting dashboard')
    
    # Send initial swarm status
    swarm_status = swarm_manager.get_swarm_intelligence_summary()
    emit('swarm_update', {
        'swarm_status': swarm_status,
        'active_decisions': {},
        'active_goals': {},
        'timestamp': time.time()
    })

@socketio.on('disconnect')
def handle_voting_disconnect():
    """Handle client disconnection"""
    print('Client disconnected from swarm voting dashboard')

@socketio.on('simulate_agent_vote')
def handle_simulate_vote(data):
    """Handle simulated agent vote from dashboard"""
    print(f"Simulating vote from agent {data['agent_id']}")
    
    # Submit vote asynchronously
    asyncio.create_task(swarm_manager.submit_vote(
        agent_id=data['agent_id'],
        decision_id=data['decision_id'],
        vote_type=VoteType(data['vote_type']),
        confidence=data['confidence'],
        reasoning=data['reasoning']
    ))
    
    emit('vote_submitted', {'status': 'success'})

def start_voting_monitor():
    """Start voting monitoring in background thread"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(voting_dashboard.start_voting_monitor())

if __name__ == '__main__':
    print(">> Starting SINCOR Swarm Intelligence Dashboard")
    print(">> God Mode: ENABLED")
    print(">> Swarm Democracy: ACTIVE")
    print(">> Access dashboard at: http://localhost:5002")
    
    # Start background voting monitor
    voting_thread = threading.Thread(target=start_voting_monitor, daemon=True)
    voting_thread.start()
    
    # Start Flask app with SocketIO
    socketio.run(app, host='0.0.0.0', port=5002, debug=False)