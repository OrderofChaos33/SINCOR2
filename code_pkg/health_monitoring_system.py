"""
Health Monitoring System - Day 3 Hardening
Provides health/heartbeat endpoints with SLA monitoring and alerts
"""

import os
import json
import sqlite3
import logging
import time
import psutil
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from enum import Enum
from flask import Blueprint, jsonify, render_template_string
import threading
import schedule

logger = logging.getLogger(__name__)

class HealthStatus(Enum):
    GREEN = "green"    # All systems operational
    YELLOW = "yellow"  # Degraded performance
    RED = "red"        # Critical issues

@dataclass
class ServiceHealth:
    name: str
    status: HealthStatus
    response_time_ms: float
    error_rate: float
    last_check: datetime
    details: Dict[str, Any] = None

@dataclass
class SystemMetrics:
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    queue_depth: int
    active_connections: int
    timestamp: datetime

class HealthMonitoringSystem:
    def __init__(self):
        self.db_path = "clinton_auto_detailing_health.db"
        self.alert_thresholds = {
            'webhook_failure_rate': 0.02,  # 2%
            'queue_depth_critical': 1000,
            'response_time_critical': 5000,  # 5 seconds
            'cpu_critical': 80,
            'memory_critical': 85,
            'disk_critical': 90
        }
        
        self.service_endpoints = {
            'square_integration': 'http://localhost:5000/api/square/health',
            'crm_system': 'http://localhost:5000/api/crm/health',
            'email_system': 'http://localhost:5000/api/email/health',
            'accounting_system': 'http://localhost:5000/api/accounting/health',
            'event_processor': 'http://localhost:5000/api/events/metrics',
            'paypal_integration': None,  # External service
            'facebook_api': None,        # External service
            'google_ads_api': None       # External service
        }
        
        self.init_database()
        self.start_monitoring()
    
    def init_database(self):
        """Initialize health monitoring database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Service health history
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS service_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_name TEXT,
                status TEXT,
                response_time_ms REAL,
                error_rate REAL,
                cpu_percent REAL,
                memory_percent REAL,
                details TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX(service_name, timestamp),
                INDEX(status)
            )
        ''')
        
        # System metrics history
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cpu_percent REAL,
                memory_percent REAL,
                disk_percent REAL,
                queue_depth INTEGER,
                active_connections INTEGER,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX(timestamp)
            )
        ''')
        
        # SLA violations and alerts
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sla_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_type TEXT,
                service_name TEXT,
                severity TEXT,
                threshold_value REAL,
                actual_value REAL,
                message TEXT,
                resolved BOOLEAN DEFAULT 0,
                resolved_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX(alert_type, created_at),
                INDEX(resolved)
            )
        ''')
        
        # Uptime tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS uptime_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_name TEXT,
                uptime_start TIMESTAMP,
                uptime_end TIMESTAMP,
                duration_seconds INTEGER,
                downtime_reason TEXT,
                INDEX(service_name, uptime_start)
            )
        ''')
        
        conn.commit()
        conn.close()
        
        logger.info("Health monitoring database initialized")
    
    def check_service_health(self, service_name: str, endpoint: str = None) -> ServiceHealth:
        """Check health of a specific service"""
        start_time = time.time()
        
        try:
            if endpoint:
                # HTTP health check
                response = requests.get(endpoint, timeout=10)
                response_time = (time.time() - start_time) * 1000
                
                if response.status_code == 200:
                    data = response.json()
                    status = HealthStatus.GREEN
                    error_rate = data.get('error_rate', 0)
                    details = data
                else:
                    status = HealthStatus.RED
                    error_rate = 1.0
                    details = {'error': f'HTTP {response.status_code}'}
            else:
                # External service check (simplified)
                response_time = 0
                status = HealthStatus.GREEN  # Assume healthy if no endpoint
                error_rate = 0
                details = {'type': 'external_service'}
                
        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            status = HealthStatus.RED
            error_rate = 1.0
            details = {'error': str(e)}
            
            logger.error(f"Health check failed for {service_name}: {e}")
        
        # Determine status based on thresholds
        if error_rate > self.alert_thresholds['webhook_failure_rate']:
            status = HealthStatus.RED
        elif response_time > self.alert_thresholds['response_time_critical']:
            status = HealthStatus.YELLOW if status == HealthStatus.GREEN else status
        
        return ServiceHealth(
            name=service_name,
            status=status,
            response_time_ms=response_time,
            error_rate=error_rate,
            last_check=datetime.now(),
            details=details
        )
    
    def collect_system_metrics(self) -> SystemMetrics:
        """Collect system-level metrics"""
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory_percent = psutil.virtual_memory().percent
            disk_percent = psutil.disk_usage('/').percent
            
            # Get queue depth from event system
            queue_depth = self._get_queue_depth()
            
            # Get active connections (simplified)
            active_connections = len(psutil.net_connections())
            
            return SystemMetrics(
                cpu_percent=cpu_percent,
                memory_percent=memory_percent,
                disk_percent=disk_percent,
                queue_depth=queue_depth,
                active_connections=active_connections,
                timestamp=datetime.now()
            )
            
        except Exception as e:
            logger.error(f"Failed to collect system metrics: {e}")
            return SystemMetrics(0, 0, 0, 0, 0, datetime.now())
    
    def _get_queue_depth(self) -> int:
        """Get current event queue depth"""
        try:
            # Connect to event system database
            conn = sqlite3.connect("clinton_auto_detailing_events.db")
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT COUNT(*) FROM event_queue 
                WHERE status IN ('pending', 'failed')
            ''')
            
            depth = cursor.fetchone()[0]
            conn.close()
            
            return depth
            
        except Exception as e:
            logger.error(f"Failed to get queue depth: {e}")
            return 0
    
    def check_all_services(self) -> Dict[str, ServiceHealth]:
        """Check health of all monitored services"""
        service_health = {}
        
        for service_name, endpoint in self.service_endpoints.items():
            health = self.check_service_health(service_name, endpoint)
            service_health[service_name] = health
            
            # Store in database
            self._store_service_health(health)
        
        return service_health
    
    def _store_service_health(self, health: ServiceHealth):
        """Store service health in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO service_health 
            (service_name, status, response_time_ms, error_rate, details)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            health.name,
            health.status.value,
            health.response_time_ms,
            health.error_rate,
            json.dumps(health.details or {})
        ))
        
        conn.commit()
        conn.close()
    
    def _store_system_metrics(self, metrics: SystemMetrics):
        """Store system metrics in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO system_metrics 
            (cpu_percent, memory_percent, disk_percent, queue_depth, active_connections)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            metrics.cpu_percent,
            metrics.memory_percent,
            metrics.disk_percent,
            metrics.queue_depth,
            metrics.active_connections
        ))
        
        conn.commit()
        conn.close()
    
    def check_sla_violations(self, service_health: Dict[str, ServiceHealth], 
                           system_metrics: SystemMetrics):
        """Check for SLA violations and create alerts"""
        
        # Check webhook failure rates
        for service_name, health in service_health.items():
            if health.error_rate > self.alert_thresholds['webhook_failure_rate']:
                self._create_alert(
                    alert_type='webhook_failure_rate',
                    service_name=service_name,
                    severity='critical',
                    threshold_value=self.alert_thresholds['webhook_failure_rate'],
                    actual_value=health.error_rate,
                    message=f"{service_name} webhook failure rate {health.error_rate:.2%} exceeds threshold"
                )
        
        # Check queue depth
        if system_metrics.queue_depth > self.alert_thresholds['queue_depth_critical']:
            self._create_alert(
                alert_type='queue_depth',
                service_name='event_processor',
                severity='critical',
                threshold_value=self.alert_thresholds['queue_depth_critical'],
                actual_value=system_metrics.queue_depth,
                message=f"Event queue depth {system_metrics.queue_depth} exceeds critical threshold"
            )
        
        # Check system resources
        if system_metrics.cpu_percent > self.alert_thresholds['cpu_critical']:
            self._create_alert(
                alert_type='high_cpu',
                service_name='system',
                severity='warning',
                threshold_value=self.alert_thresholds['cpu_critical'],
                actual_value=system_metrics.cpu_percent,
                message=f"CPU usage {system_metrics.cpu_percent:.1f}% exceeds critical threshold"
            )
        
        if system_metrics.memory_percent > self.alert_thresholds['memory_critical']:
            self._create_alert(
                alert_type='high_memory',
                service_name='system',
                severity='critical',
                threshold_value=self.alert_thresholds['memory_critical'],
                actual_value=system_metrics.memory_percent,
                message=f"Memory usage {system_metrics.memory_percent:.1f}% exceeds critical threshold"
            )
    
    def _create_alert(self, alert_type: str, service_name: str, severity: str,
                     threshold_value: float, actual_value: float, message: str):
        """Create SLA violation alert"""
        
        # Check if similar alert exists and is unresolved
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT COUNT(*) FROM sla_alerts 
            WHERE alert_type = ? AND service_name = ? AND resolved = 0
            AND created_at > datetime('now', '-1 hour')
        ''', (alert_type, service_name))
        
        existing_count = cursor.fetchone()[0]
        
        if existing_count == 0:  # Don't spam alerts
            cursor.execute('''
                INSERT INTO sla_alerts 
                (alert_type, service_name, severity, threshold_value, actual_value, message)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (alert_type, service_name, severity, threshold_value, actual_value, message))
            
            conn.commit()
            
            # Send notification (implement based on your notification system)
            self._send_alert_notification(severity, message)
            
            logger.warning(f"SLA Alert: {message}", extra={
                'alert_type': alert_type,
                'service': service_name,
                'severity': severity
            })
        
        conn.close()
    
    def _send_alert_notification(self, severity: str, message: str):
        """Send alert notification (implement based on your preferences)"""
        # TODO: Implement email/SMS/Slack notifications
        # For now, just log
        if severity == 'critical':
            logger.critical(f"CRITICAL ALERT: {message}")
        else:
            logger.warning(f"WARNING ALERT: {message}")
    
    def get_overall_status(self) -> HealthStatus:
        """Get overall system health status"""
        service_health = self.check_all_services()
        
        # Determine overall status
        red_count = sum(1 for h in service_health.values() if h.status == HealthStatus.RED)
        yellow_count = sum(1 for h in service_health.values() if h.status == HealthStatus.YELLOW)
        
        if red_count > 0:
            return HealthStatus.RED
        elif yellow_count > 0:
            return HealthStatus.YELLOW
        else:
            return HealthStatus.GREEN
    
    def get_health_dashboard_data(self) -> Dict[str, Any]:
        """Get comprehensive health dashboard data"""
        service_health = self.check_all_services()
        system_metrics = self.collect_system_metrics()
        overall_status = self.get_overall_status()
        
        # Get recent alerts
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT alert_type, service_name, severity, message, created_at
            FROM sla_alerts 
            WHERE resolved = 0 
            ORDER BY created_at DESC 
            LIMIT 10
        ''')
        active_alerts = cursor.fetchall()
        
        # Get uptime statistics
        cursor.execute('''
            SELECT service_name, 
                   AVG(CASE WHEN status = 'green' THEN 1 ELSE 0 END) * 100 as uptime_percent
            FROM service_health 
            WHERE timestamp >= datetime('now', '-24 hours')
            GROUP BY service_name
        ''')
        uptime_stats = cursor.fetchall()
        
        conn.close()
        
        # Store current metrics
        self._store_system_metrics(system_metrics)
        
        # Check for violations
        self.check_sla_violations(service_health, system_metrics)
        
        return {
            'overall_status': overall_status.value,
            'service_health': {name: asdict(health) for name, health in service_health.items()},
            'system_metrics': asdict(system_metrics),
            'active_alerts': active_alerts,
            'uptime_stats': uptime_stats,
            'last_updated': datetime.now().isoformat()
        }
    
    def start_monitoring(self):
        """Start background monitoring tasks"""
        def run_monitoring():
            # Schedule health checks every minute
            schedule.every(1).minutes.do(self.check_all_services)
            
            # Schedule system metrics collection every 30 seconds
            schedule.every(30).seconds.do(self.collect_system_metrics)
            
            # Schedule alert cleanup every hour
            schedule.every().hour.do(self._cleanup_old_alerts)
            
            while True:
                schedule.run_pending()
                time.sleep(10)
        
        # Start monitoring thread
        monitor_thread = threading.Thread(target=run_monitoring, daemon=True)
        monitor_thread.start()
        
        logger.info("Health monitoring started")
    
    def _cleanup_old_alerts(self):
        """Clean up resolved alerts older than 7 days"""
        cutoff_date = datetime.now() - timedelta(days=7)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            DELETE FROM sla_alerts 
            WHERE resolved = 1 AND resolved_at < ?
        ''', (cutoff_date,))
        
        conn.commit()
        conn.close()

# Flask Blueprint for health endpoints
health_bp = Blueprint('health', __name__)
health_monitor = HealthMonitoringSystem()

@health_bp.route('/integrations/status')
def integration_status():
    """Integration status endpoint"""
    try:
        dashboard_data = health_monitor.get_health_dashboard_data()
        return jsonify({
            'status': 'success',
            'data': dashboard_data
        })
    except Exception as e:
        logger.error(f"Health status error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@health_bp.route('/events/heartbeat')
def events_heartbeat():
    """Events system heartbeat"""
    try:
        queue_depth = health_monitor._get_queue_depth()
        
        status = "healthy"
        if queue_depth > health_monitor.alert_thresholds['queue_depth_critical']:
            status = "critical"
        elif queue_depth > health_monitor.alert_thresholds['queue_depth_critical'] * 0.5:
            status = "warning"
        
        return jsonify({
            'status': status,
            'queue_depth': queue_depth,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@health_bp.route('/queue/depth')
def queue_depth():
    """Queue depth endpoint"""
    try:
        depth = health_monitor._get_queue_depth()
        
        color = "green"
        if depth > health_monitor.alert_thresholds['queue_depth_critical']:
            color = "red"
        elif depth > health_monitor.alert_thresholds['queue_depth_critical'] * 0.5:
            color = "yellow"
        
        return jsonify({
            'depth': depth,
            'color': color,
            'threshold': health_monitor.alert_thresholds['queue_depth_critical'],
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@health_bp.route('/dashboard')
def health_dashboard():
    """Health monitoring dashboard"""
    dashboard_html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Clinton Auto Detailing - Health Dashboard</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: Arial, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }
            .container { max-width: 1400px; margin: 0 auto; }
            .header { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            .status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 20px; }
            .status-card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            .status-indicator { display: inline-block; width: 12px; height: 12px; border-radius: 50%; margin-right: 8px; }
            .green { background: #10b981; }
            .yellow { background: #f59e0b; }
            .red { background: #ef4444; }
            .metric-value { font-size: 2em; font-weight: bold; margin: 10px 0; }
            .alerts-section { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            .alert-item { padding: 10px; margin: 10px 0; border-radius: 4px; }
            .alert-critical { background: #fef2f2; border-left: 4px solid #ef4444; }
            .alert-warning { background: #fffbeb; border-left: 4px solid #f59e0b; }
            .uptime-section { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Clinton Auto Detailing - System Health</h1>
                <p>Operational Monitoring Dashboard</p>
            </div>
            
            <div class="status-grid" id="servicesGrid">
                Loading service health...
            </div>
            
            <div class="alerts-section">
                <h2>Active Alerts</h2>
                <div id="activeAlerts">Loading alerts...</div>
            </div>
            
            <div class="uptime-section">
                <h2>24-Hour Uptime Statistics</h2>
                <div id="uptimeStats">Loading uptime stats...</div>
            </div>
        </div>
        
        <script>
            async function loadHealthData() {
                try {
                    const response = await fetch('/health/integrations/status');
                    const result = await response.json();
                    const data = result.data;
                    
                    // Update services grid
                    const servicesHtml = Object.entries(data.service_health).map(([name, health]) => `
                        <div class="status-card">
                            <h3><span class="status-indicator ${health.status}"></span>${name.replace('_', ' ').toUpperCase()}</h3>
                            <div class="metric-value">${health.response_time_ms.toFixed(0)}ms</div>
                            <p>Response Time</p>
                            <p>Error Rate: ${(health.error_rate * 100).toFixed(2)}%</p>
                            <p>Last Check: ${new Date(health.last_check).toLocaleTimeString()}</p>
                        </div>
                    `).join('');
                    document.getElementById('servicesGrid').innerHTML = servicesHtml;
                    
                    // Update alerts
                    const alertsHtml = data.active_alerts.length > 0 ? 
                        data.active_alerts.map(alert => `
                            <div class="alert-item alert-${alert[2]}">
                                <strong>${alert[1]}</strong> - ${alert[3]}
                                <br><small>${new Date(alert[4]).toLocaleString()}</small>
                            </div>
                        `).join('') : '<p>No active alerts</p>';
                    document.getElementById('activeAlerts').innerHTML = alertsHtml;
                    
                    // Update uptime stats
                    const uptimeHtml = data.uptime_stats.map(stat => `
                        <p><strong>${stat[0]}:</strong> ${stat[1].toFixed(2)}% uptime</p>
                    `).join('');
                    document.getElementById('uptimeStats').innerHTML = uptimeHtml;
                    
                } catch (error) {
                    console.error('Failed to load health data:', error);
                }
            }
            
            loadHealthData();
            setInterval(loadHealthData, 30000); // Refresh every 30 seconds
        </script>
    </body>
    </html>
    '''
    
    return dashboard_html

if __name__ == "__main__":
    # Test health monitoring
    monitor = HealthMonitoringSystem()
    
    print("Checking system health...")
    dashboard_data = monitor.get_health_dashboard_data()
    
    print(f"Overall Status: {dashboard_data['overall_status']}")
    print(f"Active Alerts: {len(dashboard_data['active_alerts'])}")
    print(f"Queue Depth: {dashboard_data['system_metrics']['queue_depth']}")
    
    print("\\nHealth monitoring system ready!")