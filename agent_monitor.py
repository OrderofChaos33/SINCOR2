#!/usr/bin/env python3
"""
SINCOR Agent Monitoring Dashboard

Real-time monitoring and visualization of agent activities, coordination,
and system health across all SINCOR agents.
"""

import os
import json
import time
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from collections import defaultdict

from flask import Flask, render_template_string, jsonify, request

class AgentMonitor:
    """Real-time agent monitoring and coordination tracker."""
    
    def __init__(self):
        self.root_path = Path(__file__).parent
        self.log_dir = self.root_path / "logs"
        self.data_dir = self.root_path / "data"
        
        # Agent state tracking
        self.agent_status = {}
        self.agent_metrics = defaultdict(dict)
        self.coordination_events = []
        self.system_health = {}
        
        # Monitoring configuration
        self.update_interval = 5  # seconds
        self.max_events = 1000
        self.is_monitoring = False
        
        # Initialize monitoring database
        self._init_monitoring_db()
        
    def _init_monitoring_db(self):
        """Initialize agent monitoring database."""
        self.monitor_db = self.data_dir / "agent_monitor.db"
        
        conn = sqlite3.connect(str(self.monitor_db))
        cursor = conn.cursor()
        
        # Agent status table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS agent_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_name TEXT NOT NULL,
                status TEXT NOT NULL,
                last_activity TEXT,
                message TEXT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Agent metrics table  
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS agent_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_name TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                metric_value TEXT NOT NULL,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Coordination events table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS coordination_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                source_agent TEXT NOT NULL,
                target_agent TEXT,
                description TEXT,
                data TEXT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def scan_agent_logs(self):
        """Scan agent logs for activity and status updates."""
        agent_data = {}
        
        # Scan daetime agent logs
        daetime_logs = self.log_dir / "daetime"
        if daetime_logs.exists():
            for log_file in daetime_logs.glob("run_*.log"):
                try:
                    with open(log_file, 'r') as f:
                        content = f.read()
                        if content.strip():
                            data = json.loads(content)
                            agent_data['daetime'] = {
                                'status': 'active',
                                'last_activity': data.get('result', {}).get('timestamp', ''),
                                'last_task': data.get('task', {}),
                                'result': data.get('result', {}),
                                'log_file': log_file.name
                            }
                except Exception as e:
                    agent_data['daetime'] = {
                        'status': 'error',
                        'error': str(e),
                        'log_file': log_file.name
                    }
        
        # Scan main application logs
        main_log = self.log_dir / "run.log"
        if main_log.exists():
            try:
                with open(main_log, 'r') as f:
                    lines = f.readlines()[-50:]  # Last 50 lines
                
                # Extract agent-related activities
                agent_activities = []
                for line in lines:
                    if 'AGENT' in line.upper() or 'agent' in line.lower():
                        agent_activities.append(line.strip())
                
                agent_data['system'] = {
                    'status': 'active',
                    'recent_activities': agent_activities,
                    'last_updated': datetime.now().isoformat()
                }
                
            except Exception as e:
                agent_data['system'] = {'status': 'error', 'error': str(e)}
        
        # Scan business intelligence logs
        intel_log = self.log_dir / "template_engine.log"
        if intel_log.exists():
            try:
                with open(intel_log, 'r') as f:
                    lines = f.readlines()[-10:]
                
                if lines:
                    last_line = lines[-1].strip()
                    agent_data['intelligence'] = {
                        'status': 'active',
                        'last_activity': last_line,
                        'log_entries': len(lines)
                    }
            except Exception as e:
                agent_data['intelligence'] = {'status': 'error', 'error': str(e)}
        
        return agent_data
    
    def analyze_agent_coordination(self, agent_data):
        """Analyze how agents are coordinating with each other."""
        coordination_analysis = {
            'coordination_score': 0,
            'active_agents': 0,
            'communication_events': 0,
            'system_coherence': 'unknown',
            'recommendations': []
        }
        
        active_agents = [name for name, data in agent_data.items() 
                        if data.get('status') == 'active']
        
        coordination_analysis['active_agents'] = len(active_agents)
        
        # Analyze coordination patterns
        if len(active_agents) >= 2:
            coordination_analysis['coordination_score'] = min(85, len(active_agents) * 25)
            coordination_analysis['system_coherence'] = 'good'
        elif len(active_agents) == 1:
            coordination_analysis['coordination_score'] = 50
            coordination_analysis['system_coherence'] = 'limited'
            coordination_analysis['recommendations'].append('Start additional agents for better coordination')
        else:
            coordination_analysis['coordination_score'] = 10
            coordination_analysis['system_coherence'] = 'poor'
            coordination_analysis['recommendations'].append('No active agents detected - check system health')
        
        # Check for communication patterns in logs
        system_data = agent_data.get('system', {})
        activities = system_data.get('recent_activities', [])
        
        communication_keywords = ['started', 'completed', 'synchronized', 'updated', 'executed']
        comm_events = sum(1 for activity in activities 
                         for keyword in communication_keywords 
                         if keyword.lower() in activity.lower())
        
        coordination_analysis['communication_events'] = comm_events
        
        if comm_events > 5:
            coordination_analysis['recommendations'].append('High agent activity - system working well')
        elif comm_events > 0:
            coordination_analysis['recommendations'].append('Moderate agent activity detected')
        else:
            coordination_analysis['recommendations'].append('Low agent activity - may need attention')
        
        return coordination_analysis
    
    def get_system_health_metrics(self):
        """Calculate overall system health metrics."""
        health_metrics = {
            'overall_health': 'unknown',
            'health_score': 0,
            'uptime': 'unknown',
            'error_rate': 0,
            'performance_score': 0,
            'issues': [],
            'strengths': []
        }
        
        try:
            # Check database connectivity
            databases = ['sincor_main.db', 'business_intel.db', 'compliance.db']
            db_health = 0
            
            for db_name in databases:
                db_path = self.data_dir / db_name
                if db_path.exists():
                    try:
                        conn = sqlite3.connect(str(db_path))
                        conn.execute('SELECT 1').fetchone()
                        conn.close()
                        db_health += 1
                    except:
                        health_metrics['issues'].append(f'Database {db_name} has connectivity issues')
            
            health_metrics['database_health'] = (db_health / len(databases)) * 100
            
            # Check log file health
            log_files = list(self.log_dir.glob('*.log'))
            if log_files:
                health_metrics['strengths'].append(f'{len(log_files)} log files found')
                
                # Check for recent activity (last hour)
                recent_activity = False
                for log_file in log_files:
                    if log_file.stat().st_mtime > (time.time() - 3600):  # 1 hour
                        recent_activity = True
                        break
                
                if recent_activity:
                    health_metrics['strengths'].append('Recent system activity detected')
                else:
                    health_metrics['issues'].append('No recent activity in logs')
            
            # Calculate overall health score
            base_score = 70  # Base score for running system
            
            if health_metrics['database_health'] > 80:
                base_score += 15
            elif health_metrics['database_health'] > 50:
                base_score += 5
            else:
                base_score -= 10
            
            if len(health_metrics['issues']) == 0:
                base_score += 10
            else:
                base_score -= len(health_metrics['issues']) * 5
            
            health_metrics['health_score'] = max(0, min(100, base_score))
            
            # Determine overall health status
            if health_metrics['health_score'] >= 80:
                health_metrics['overall_health'] = 'excellent'
            elif health_metrics['health_score'] >= 60:
                health_metrics['overall_health'] = 'good'
            elif health_metrics['health_score'] >= 40:
                health_metrics['overall_health'] = 'fair'
            else:
                health_metrics['overall_health'] = 'poor'
                
        except Exception as e:
            health_metrics['issues'].append(f'Health check error: {e}')
            health_metrics['health_score'] = 30
            health_metrics['overall_health'] = 'degraded'
        
        return health_metrics
    
    def update_monitoring_data(self):
        """Update all monitoring data."""
        try:
            # Scan agent logs
            self.agent_status = self.scan_agent_logs()
            
            # Analyze coordination
            coordination = self.analyze_agent_coordination(self.agent_status)
            
            # Get system health
            health = self.get_system_health_metrics()
            
            # Store data
            conn = sqlite3.connect(str(self.monitor_db))
            cursor = conn.cursor()
            
            # Update agent status
            for agent_name, status_data in self.agent_status.items():
                cursor.execute('''
                    INSERT INTO agent_status (agent_name, status, last_activity, message)
                    VALUES (?, ?, ?, ?)
                ''', (
                    agent_name,
                    status_data.get('status', 'unknown'),
                    status_data.get('last_activity', ''),
                    json.dumps(status_data)
                ))
            
            # Store coordination event
            cursor.execute('''
                INSERT INTO coordination_events (event_type, source_agent, description, data)
                VALUES (?, ?, ?, ?)
            ''', (
                'coordination_analysis',
                'system',
                f'Coordination score: {coordination["coordination_score"]}',
                json.dumps(coordination)
            ))
            
            conn.commit()
            conn.close()
            
            # Update internal state
            self.system_health = health
            
            return {
                'agents': self.agent_status,
                'coordination': coordination,
                'health': health,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            return {'error': str(e), 'timestamp': datetime.now().isoformat()}
    
    def get_monitoring_data(self):
        """Get current monitoring data."""
        return self.update_monitoring_data()
    
    def start_monitoring(self, interval=5):
        """Start background monitoring."""
        self.is_monitoring = True
        self.update_interval = interval
        
        def monitoring_loop():
            while self.is_monitoring:
                self.update_monitoring_data()
                time.sleep(self.update_interval)
        
        monitor_thread = threading.Thread(target=monitoring_loop, daemon=True)
        monitor_thread.start()
        return monitor_thread


# Flask web interface for agent monitoring
def create_monitor_app():
    """Create Flask app for agent monitoring dashboard."""
    
    app = Flask(__name__)
    monitor = AgentMonitor()
    
    # Start background monitoring
    monitor.start_monitoring()
    
    @app.route('/')
    def dashboard():
        """Main monitoring dashboard."""
        return render_template_string(DASHBOARD_HTML)
    
    @app.route('/api/status')
    def api_status():
        """API endpoint for current status."""
        data = monitor.get_monitoring_data()
        return jsonify(data)
    
    @app.route('/api/agents')
    def api_agents():
        """API endpoint for agent details."""
        return jsonify(monitor.agent_status)
    
    @app.route('/api/health')
    def api_health():
        """API endpoint for system health."""
        return jsonify(monitor.system_health)
    
    return app


# HTML template for the monitoring dashboard
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>SINCOR Agent Monitor</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 0;
            padding: 20px;
            background: #f5f7fa;
            color: #333;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
            text-align: center;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        .stat-card {
            background: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            border-left: 4px solid #667eea;
        }
        .stat-value {
            font-size: 2em;
            font-weight: bold;
            margin-bottom: 5px;
        }
        .stat-label {
            color: #666;
            font-size: 0.9em;
        }
        .agent-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        .agent-card {
            background: white;
            border-radius: 10px;
            padding: 20px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        .agent-status {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.8em;
            font-weight: bold;
            text-transform: uppercase;
        }
        .status-active { background: #d4edda; color: #155724; }
        .status-error { background: #f8d7da; color: #721c24; }
        .status-unknown { background: #ffeaa7; color: #856404; }
        .activity-log {
            background: #f8f9fa;
            padding: 15px;
            border-radius: 5px;
            margin-top: 15px;
            max-height: 200px;
            overflow-y: auto;
            font-family: monospace;
            font-size: 0.9em;
        }
        .coordination-panel {
            background: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }
        .progress-bar {
            width: 100%;
            height: 20px;
            background: #e9ecef;
            border-radius: 10px;
            overflow: hidden;
            margin: 10px 0;
        }
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #667eea, #764ba2);
            transition: width 0.3s ease;
        }
        .refresh-btn {
            background: #667eea;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 14px;
        }
        .refresh-btn:hover {
            background: #5a67d8;
        }
        .timestamp {
            color: #666;
            font-size: 0.8em;
            text-align: right;
            margin-top: 10px;
        }
        .recommendations {
            background: #e3f2fd;
            border-left: 4px solid #2196f3;
            padding: 15px;
            margin-top: 15px;
            border-radius: 5px;
        }
        .error-text {
            color: #dc3545;
            font-weight: bold;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>SINCOR Agent Monitoring Dashboard</h1>
        <p>Real-time monitoring of agent activities and system coordination</p>
        <button class="refresh-btn" onclick="refreshData()">Refresh Data</button>
    </div>

    <div class="stats-grid">
        <div class="stat-card">
            <div class="stat-value" id="active-agents">--</div>
            <div class="stat-label">Active Agents</div>
        </div>
        <div class="stat-card">
            <div class="stat-value" id="coordination-score">--</div>
            <div class="stat-label">Coordination Score</div>
        </div>
        <div class="stat-card">
            <div class="stat-value" id="health-score">--</div>
            <div class="stat-label">System Health</div>
        </div>
        <div class="stat-card">
            <div class="stat-value" id="communication-events">--</div>
            <div class="stat-label">Communication Events</div>
        </div>
    </div>

    <div class="coordination-panel">
        <h3>Agent Coordination Analysis</h3>
        <div>
            <strong>Coordination Score:</strong>
            <div class="progress-bar">
                <div class="progress-fill" id="coord-progress" style="width: 0%"></div>
            </div>
            <span id="coord-status">Unknown</span>
        </div>
        <div class="recommendations" id="recommendations">
            <strong>Recommendations:</strong>
            <ul id="recommendation-list"></ul>
        </div>
    </div>

    <div class="agent-grid" id="agent-grid">
        <!-- Agent cards will be populated here -->
    </div>

    <div class="timestamp" id="last-updated">
        Last updated: --
    </div>

    <script>
        let monitorData = {};

        function refreshData() {
            fetch('/api/status')
                .then(response => response.json())
                .then(data => {
                    monitorData = data;
                    updateDashboard();
                })
                .catch(error => {
                    console.error('Error fetching data:', error);
                    showError('Failed to fetch monitoring data');
                });
        }

        function updateDashboard() {
            if (monitorData.error) {
                showError(monitorData.error);
                return;
            }

            // Update stats
            const coord = monitorData.coordination || {};
            const health = monitorData.health || {};
            const agents = monitorData.agents || {};

            document.getElementById('active-agents').textContent = coord.active_agents || 0;
            document.getElementById('coordination-score').textContent = coord.coordination_score || 0;
            document.getElementById('health-score').textContent = health.health_score || 0;
            document.getElementById('communication-events').textContent = coord.communication_events || 0;

            // Update coordination progress
            const coordScore = coord.coordination_score || 0;
            document.getElementById('coord-progress').style.width = coordScore + '%';
            document.getElementById('coord-status').textContent = coord.system_coherence || 'Unknown';

            // Update recommendations
            const recList = document.getElementById('recommendation-list');
            recList.innerHTML = '';
            (coord.recommendations || []).forEach(rec => {
                const li = document.createElement('li');
                li.textContent = rec;
                recList.appendChild(li);
            });

            // Update agent cards
            updateAgentCards(agents);

            // Update timestamp
            document.getElementById('last-updated').textContent = 
                'Last updated: ' + (monitorData.timestamp || 'Unknown');
        }

        function updateAgentCards(agents) {
            const grid = document.getElementById('agent-grid');
            grid.innerHTML = '';

            Object.keys(agents).forEach(agentName => {
                const agent = agents[agentName];
                const card = document.createElement('div');
                card.className = 'agent-card';
                
                const status = agent.status || 'unknown';
                const statusClass = 'status-' + status;
                
                let activityContent = '';
                if (agent.recent_activities) {
                    activityContent = agent.recent_activities.slice(-5).join('\\n');
                } else if (agent.last_activity) {
                    activityContent = agent.last_activity;
                } else if (agent.error) {
                    activityContent = 'ERROR: ' + agent.error;
                }

                card.innerHTML = `
                    <h4>${agentName.charAt(0).toUpperCase() + agentName.slice(1)} Agent</h4>
                    <span class="agent-status ${statusClass}">${status}</span>
                    <div class="activity-log">${activityContent || 'No recent activity'}</div>
                    ${agent.last_task ? '<p><strong>Last Task:</strong> ' + JSON.stringify(agent.last_task) + '</p>' : ''}
                `;
                
                grid.appendChild(card);
            });

            if (Object.keys(agents).length === 0) {
                grid.innerHTML = '<div class="agent-card"><p class="error-text">No agents detected</p></div>';
            }
        }

        function showError(message) {
            const grid = document.getElementById('agent-grid');
            grid.innerHTML = `<div class="agent-card"><p class="error-text">Error: ${message}</p></div>`;
        }

        // Auto-refresh every 10 seconds
        setInterval(refreshData, 10000);

        // Initial load
        refreshData();
    </script>
</body>
</html>
'''


if __name__ == '__main__':
    app = create_monitor_app()
    print("Starting SINCOR Agent Monitor on http://localhost:5001")
    print("This will monitor agent activities and coordination in real-time.")
    app.run(host='0.0.0.0', port=5001, debug=False)