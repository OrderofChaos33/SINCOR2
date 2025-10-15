"""
SINCOR Monitoring Dashboard
Real-time monitoring endpoint for system health and metrics
"""

import os
import psutil
import time
from datetime import datetime
from flask import jsonify


class MonitoringDashboard:
    """System monitoring and health metrics"""

    def __init__(self, app=None):
        self.app = app
        self.start_time = time.time()
        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize monitoring endpoints"""

        @app.route('/api/monitoring/status', methods=['GET'])
        def monitoring_status():
            """Monitoring status endpoint"""
            return jsonify({
                'status': 'healthy',
                'timestamp': datetime.now().isoformat(),
                'uptime_seconds': int(time.time() - self.start_time)
            })

        @app.route('/api/monitoring/metrics', methods=['GET'])
        def get_metrics():
            """Comprehensive system metrics"""
            return jsonify(self.get_system_metrics())

        @app.route('/api/monitoring/security', methods=['GET'])
        def security_status():
            """Security features status"""
            from app import (
                AUTH_AVAILABLE,
                RATE_LIMIT_AVAILABLE,
                SECURITY_HEADERS_AVAILABLE,
                LOGGING_AVAILABLE
            )

            return jsonify({
                'security_score': self.calculate_security_score(),
                'features': {
                    'authentication': AUTH_AVAILABLE,
                    'rate_limiting': RATE_LIMIT_AVAILABLE,
                    'security_headers': SECURITY_HEADERS_AVAILABLE,
                    'logging': LOGGING_AVAILABLE,
                    'input_validation': self.check_validation_available()
                },
                'timestamp': datetime.now().isoformat()
            })

        @app.route('/api/monitoring/logs', methods=['GET'])
        def log_summary():
            """Log file summary"""
            try:
                from production_logger import get_logger_stats
                stats = get_logger_stats()
                return jsonify({
                    'logs': stats,
                    'timestamp': datetime.now().isoformat()
                })
            except:
                return jsonify({'error': 'Logging not available'}), 503

    def get_system_metrics(self):
        """Get comprehensive system metrics"""

        # CPU metrics
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_count = psutil.cpu_count()

        # Memory metrics
        memory = psutil.virtual_memory()
        memory_used_mb = memory.used / (1024 * 1024)
        memory_total_mb = memory.total / (1024 * 1024)
        memory_percent = memory.percent

        # Disk metrics (use current drive)
        try:
            import platform
            if platform.system() == 'Windows':
                disk = psutil.disk_usage('C:')
            else:
                disk = psutil.disk_usage('/')
            disk_used_gb = disk.used / (1024 * 1024 * 1024)
            disk_total_gb = disk.total / (1024 * 1024 * 1024)
            disk_percent = disk.percent
        except:
            disk_used_gb = 0
            disk_total_gb = 0
            disk_percent = 0

        # Process metrics
        process = psutil.Process()
        process_memory_mb = process.memory_info().rss / (1024 * 1024)
        process_cpu_percent = process.cpu_percent(interval=0.1)

        # Uptime
        uptime_seconds = int(time.time() - self.start_time)
        uptime_minutes = uptime_seconds // 60
        uptime_hours = uptime_minutes // 60
        uptime_days = uptime_hours // 24

        return {
            'timestamp': datetime.now().isoformat(),
            'uptime': {
                'seconds': uptime_seconds,
                'formatted': f'{uptime_days}d {uptime_hours % 24}h {uptime_minutes % 60}m'
            },
            'cpu': {
                'percent': cpu_percent,
                'count': cpu_count,
                'process_percent': process_cpu_percent
            },
            'memory': {
                'used_mb': round(memory_used_mb, 2),
                'total_mb': round(memory_total_mb, 2),
                'percent': memory_percent,
                'process_mb': round(process_memory_mb, 2)
            },
            'disk': {
                'used_gb': round(disk_used_gb, 2),
                'total_gb': round(disk_total_gb, 2),
                'percent': disk_percent
            },
            'system': {
                'platform': os.name,
                'python_version': os.sys.version.split()[0]
            }
        }

    def calculate_security_score(self):
        """Calculate current security score"""
        from app import (
            AUTH_AVAILABLE,
            RATE_LIMIT_AVAILABLE,
            SECURITY_HEADERS_AVAILABLE,
            LOGGING_AVAILABLE
        )

        score = 50  # Base score

        if AUTH_AVAILABLE:
            score += 20
        if RATE_LIMIT_AVAILABLE:
            score += 15
        if SECURITY_HEADERS_AVAILABLE:
            score += 10
        if LOGGING_AVAILABLE:
            score += 5
        if self.check_validation_available():
            score += 10

        return score

    def check_validation_available(self):
        """Check if validation is available"""
        try:
            from validation_models import WaitlistSignup
            return True
        except:
            return False


def get_health_summary():
    """Get quick health summary"""
    try:
        cpu = psutil.cpu_percent(interval=0.5)
        memory = psutil.virtual_memory().percent

        # Try to get disk usage, skip if fails
        try:
            import platform
            if platform.system() == 'Windows':
                disk = psutil.disk_usage('C:').percent
            else:
                disk = psutil.disk_usage('/').percent
        except:
            disk = 0

        status = 'healthy'
        if cpu > 90 or memory > 90 or (disk > 0 and disk > 90):
            status = 'critical'
        elif cpu > 70 or memory > 70 or (disk > 0 and disk > 80):
            status = 'warning'

        return {
            'status': status,
            'cpu_percent': cpu,
            'memory_percent': memory,
            'disk_percent': disk
        }
    except Exception as e:
        return {'status': 'unknown', 'error': str(e)}


# Test function
def test_monitoring():
    """Test monitoring dashboard"""
    print("Testing SINCOR Monitoring Dashboard...")

    dashboard = MonitoringDashboard()

    print("\nSystem Metrics:")
    metrics = dashboard.get_system_metrics()

    print(f"  Uptime: {metrics['uptime']['formatted']}")
    print(f"  CPU Usage: {metrics['cpu']['percent']}%")
    print(f"  Memory: {metrics['memory']['used_mb']:.0f}MB / {metrics['memory']['total_mb']:.0f}MB ({metrics['memory']['percent']}%)")
    print(f"  Disk: {metrics['disk']['used_gb']:.1f}GB / {metrics['disk']['total_gb']:.1f}GB ({metrics['disk']['percent']}%)")
    print(f"  Process Memory: {metrics['memory']['process_mb']:.0f}MB")

    print("\nHealth Summary:")
    health = get_health_summary()
    print(f"  Status: {health.get('status', 'UNKNOWN').upper()}")
    if 'cpu_percent' in health:
        print(f"  CPU: {health['cpu_percent']}%")
        print(f"  Memory: {health['memory_percent']}%")
        print(f"  Disk: {health['disk_percent']}%")
    else:
        print(f"  Error: {health.get('error', 'Unknown error')}")

    print("\nSecurity Score:")
    score = dashboard.calculate_security_score()
    print(f"  Score: {score}/100")

    print("\nMonitoring endpoints ready:")
    print("  - GET /api/monitoring/status - Monitoring status")
    print("  - GET /api/monitoring/metrics - Detailed metrics")
    print("  - GET /api/monitoring/security - Security status")
    print("  - GET /api/monitoring/logs - Log file summary")


if __name__ == "__main__":
    test_monitoring()
