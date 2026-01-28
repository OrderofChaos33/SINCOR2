#!/usr/bin/env python3
"""
SINCOR Real-Time Health Monitoring Dashboard
Web interface for the advanced agent health monitoring system
"""

from flask import Flask, render_template, jsonify, request, send_from_directory
from flask_socketio import SocketIO, emit
import asyncio
import threading
import time
import json
from agent_health_monitor import HealthMonitoringEngine, SystemHealthSummary
from typing import Dict, Any

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sincor-health-monitoring-2025'
socketio = SocketIO(app, cors_allowed_origins="*")

# Global monitoring instance
monitor = None
monitoring_thread = None

class MonitoringDashboard:
    """Dashboard controller for real-time monitoring"""
    
    def __init__(self):
        self.monitor = HealthMonitoringEngine()
        self.running = False
        
    async def start_background_monitoring(self):
        """Start background monitoring with WebSocket updates"""
        self.running = True
        
        while self.running:
            try:
                # Monitor all agents
                await self.monitor.monitor_all_agents()
                
                # Generate system summary
                summary = self.monitor.generate_system_summary()
                
                # Emit updates to all connected clients
                socketio.emit('system_update', {
                    'summary': self.serialize_summary(summary),
                    'agent_metrics': self.serialize_agent_metrics(),
                    'timestamp': time.time()
                })
                
                await asyncio.sleep(2)  # Update every 2 seconds
                
            except Exception as e:
                print(f"Monitoring error: {e}")
                await asyncio.sleep(5)
                
    def serialize_summary(self, summary: SystemHealthSummary) -> Dict:
        """Serialize system summary for JSON"""
        return {
            'timestamp': summary.timestamp,
            'total_agents': summary.total_agents,
            'healthy_agents': summary.healthy_agents,
            'warning_agents': summary.warning_agents,
            'critical_agents': summary.critical_agents,
            'offline_agents': summary.offline_agents,
            'avg_response_time': summary.avg_response_time,
            'total_tasks_completed': summary.total_tasks_completed,
            'total_revenue': summary.total_revenue,
            'system_load': summary.system_load,
            'swarm_efficiency': summary.swarm_efficiency,
            'coordination_success_rate': summary.coordination_success_rate,
            'alerts': summary.alerts
        }
        
    def serialize_agent_metrics(self) -> Dict:
        """Serialize agent metrics for JSON"""
        result = {}
        for agent_id, metrics in self.monitor.current_metrics.items():
            result[agent_id] = {
                'agent_id': metrics.agent_id,
                'status': metrics.status.value,
                'task_completion_rate': metrics.task_completion_rate,
                'response_time_ms': metrics.response_time_ms,
                'error_rate': metrics.error_rate,
                'accuracy_score': metrics.accuracy_score,
                'cpu_usage': metrics.cpu_usage,
                'memory_usage': metrics.memory_usage,
                'api_success_rate': metrics.api_success_rate,
                'collaboration_success_rate': metrics.collaboration_success_rate,
                'workflow_efficiency': metrics.workflow_efficiency,
                'revenue_generated': metrics.revenue_generated,
                'tasks_processed': metrics.tasks_processed,
                'alerts': metrics.alerts
            }
        return result

# Global dashboard instance
dashboard = MonitoringDashboard()

@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('monitoring_dashboard.html')

@app.route('/api/system/status')
def get_system_status():
    """API endpoint for system status"""
    summary = dashboard.monitor.generate_system_summary()
    return jsonify(dashboard.serialize_summary(summary))

@app.route('/api/agents/all')
def get_all_agents():
    """API endpoint for all agent metrics"""
    return jsonify(dashboard.serialize_agent_metrics())

@app.route('/api/agent/<agent_id>')
def get_agent_details(agent_id):
    """API endpoint for specific agent details"""
    if agent_id not in dashboard.monitor.current_metrics:
        return jsonify({'error': 'Agent not found'}), 404
        
    metrics = dashboard.monitor.current_metrics[agent_id]
    return jsonify(dashboard.serialize_agent_metrics()[agent_id])

@app.route('/api/agent/<agent_id>/report')
def get_agent_report(agent_id):
    """API endpoint for agent health report"""
    hours = request.args.get('hours', 24, type=int)
    report = dashboard.monitor.get_agent_health_report(agent_id, hours)
    return jsonify(report)

@app.route('/api/alerts')
def get_system_alerts():
    """API endpoint for system alerts"""
    summary = dashboard.monitor.generate_system_summary()
    return jsonify({'alerts': summary.alerts})

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    print('Client connected to monitoring dashboard')
    
    # Send initial data
    summary = dashboard.monitor.generate_system_summary()
    emit('system_update', {
        'summary': dashboard.serialize_summary(summary),
        'agent_metrics': dashboard.serialize_agent_metrics(),
        'timestamp': time.time()
    })

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    print('Client disconnected from monitoring dashboard')

def start_monitoring_loop():
    """Start monitoring in background thread"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(dashboard.start_background_monitoring())

if __name__ == '__main__':
    print(">> Starting SINCOR Health Monitoring Dashboard")
    print(">> Access dashboard at: http://localhost:5001")
    
    # Start background monitoring
    monitoring_thread = threading.Thread(target=start_monitoring_loop, daemon=True)
    monitoring_thread.start()
    
    # Start Flask app with SocketIO
    socketio.run(app, host='0.0.0.0', port=5001, debug=False)