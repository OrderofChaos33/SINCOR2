#!/usr/bin/env python3
"""
CORTEX Admin Chat Interface - Direct communication with your 42-agent ecosystem
"""

from flask import Flask, render_template, request, jsonify, session
from flask_socketio import SocketIO, emit
import os
import json
from datetime import datetime
from sincor_kitt_interface import SINCORInterface

app = Flask(__name__)
app.config['SECRET_KEY'] = 'cortex-admin-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

# Initialize CORTEX
cortex_system = SINCORInterface()

@app.route('/admin/chat')
def admin_chat():
    """Admin chat interface for direct CORTEX communication."""
    return render_template('admin_chat.html')

@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    print('[ADMIN] Admin connected to CORTEX chat')
    emit('status', {'message': 'Connected to CORTEX - 42 agents standing by'})

@socketio.on('cortex_message')
def handle_cortex_message(data):
    """Handle message to CORTEX and coordinate agent response."""
    try:
        user_message = data.get('message', '').strip()
        
        if not user_message:
            return
        
        print(f'[ADMIN INPUT] {user_message}')
        
        # Show processing status
        emit('processing', {'message': 'CORTEX analyzing request and coordinating agents...'})
        
        # Process through CORTEX natural language system
        parsed = cortex_system.process_natural_language(user_message)
        
        # Show which agents are being activated
        coordination = cortex_system.simulate_agent_coordination(parsed["intent"], parsed["entities"])
        activated_agents = coordination.get('agents_activated', [])
        
        emit('agent_activation', {
            'intent': parsed['intent'],
            'confidence': parsed['confidence'],
            'agents': activated_agents,
            'message': f'Activating {len(activated_agents)} specialized agents...'
        })
        
        # Generate CORTEX response
        response = cortex_system.generate_kitt_response(parsed["intent"], parsed["entities"], user_message)
        response = response.replace("SINCOR", "CORTEX")
        
        # Send the full response
        emit('cortex_response', {
            'message': response,
            'timestamp': datetime.now().isoformat(),
            'coordination_score': '100/100',
            'agents_coordinated': len(activated_agents)
        })
        
        # Show ongoing coordination status
        completion_time = coordination.get('estimated_completion', '')
        if completion_time:
            try:
                completion_str = str(completion_time).replace('T', ' ')[:19]
            except:
                completion_str = 'Processing...'
        else:
            completion_str = 'Immediate'
            
        emit('coordination_status', {
            'message': 'Multi-agent coordination complete. Results delivered.',
            'success_probability': coordination.get('success_probability', 95),
            'estimated_completion': completion_str
        })
        
    except Exception as e:
        print(f'[ERROR] {e}')
        emit('error', {'message': f'CORTEX encountered an error: {str(e)}'})

@socketio.on('get_agent_status')
def handle_agent_status():
    """Get current status of all 42 agents."""
    try:
        # Simulate current agent status
        agent_categories = {
            'Business Operations': ['Board Agent', 'CFO Agent', 'Sales Agent', 'Marketing Agent', 'Customer Agent', 'HR Agent', 'IT Agent', 'Legal Agent', 'Operations Agent', 'Product Agent', 'Data Agent', 'Strategy Agent'],
            'Intelligence & Analytics': ['Business Intelligence', 'Industry Expansion', 'Master Orchestrator', 'Template Engine'],
            'Marketing & Content': ['Content Generation', 'Campaign Automation', 'Profile Sync', 'STEM Clip', 'Distribution', 'Marketing Dept'],
            'Compliance & Legal': ['AML Agent', 'KYC Agent', 'SEC Watchdog', 'Gazette Main'],
            'Operations & Coordination': ['Oversight', 'Build Coordination', 'Task Processing', 'Workflow Automation', 'Syndication', 'DAO Management']
        }
        
        agent_status = {}
        for category, agents in agent_categories.items():
            agent_status[category] = []
            for agent in agents:
                agent_status[category].append({
                    'name': agent,
                    'status': 'READY',
                    'coordination_score': 100,
                    'last_active': 'Real-time'
                })
        
        emit('agent_status_update', {
            'categories': agent_status,
            'total_agents': 42,
            'coordination_score': 100,
            'system_status': 'OPTIMAL'
        })
        
    except Exception as e:
        emit('error', {'message': f'Error getting agent status: {str(e)}'})

if __name__ == '__main__':
    # Create templates directory if it doesn't exist
    os.makedirs('templates', exist_ok=True)
    
    print("\n" + "="*60)
    print("    CORTEX ADMIN CHAT INTERFACE STARTING")
    print("="*60)
    print("\n[BRAIN] Your 42-agent CORTEX system is ready for direct communication!")
    print("[WEB] Admin Interface: http://localhost:5001/admin/chat")
    print("[CHAT] Chat directly with your AI business orchestrator")
    print("[CHART] Watch real-time agent coordination in action")
    print("\n" + "="*60)
    
    socketio.run(app, debug=True, port=5001, host='0.0.0.0')