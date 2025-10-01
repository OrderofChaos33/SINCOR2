"""
SINCOR Production Logging System
Comprehensive logging for monitoring, debugging, and security auditing
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from datetime import datetime
import json

# Check if running on Windows
IS_WINDOWS = sys.platform == 'win32'


class SINCORLogger:
    """Production-grade logging system for SINCOR"""

    def __init__(self, app=None):
        self.app = app
        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize logging with Flask app"""

        # Create logs directory
        log_dir = os.path.join(os.path.dirname(__file__), 'logs')
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        # Configure logging level from environment
        log_level = os.environ.get('LOG_LEVEL', 'INFO')
        numeric_level = getattr(logging, log_level.upper(), logging.INFO)

        # Remove default handlers
        app.logger.handlers = []

        # 1. General application log (rotating by size)
        app_handler = RotatingFileHandler(
            os.path.join(log_dir, 'app.log'),
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=10
        )
        app_handler.setLevel(numeric_level)
        app_handler.setFormatter(logging.Formatter(
            '[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
        ))
        app.logger.addHandler(app_handler)

        # 2. Error log (rotating by size on Windows, daily on Linux)
        if IS_WINDOWS:
            # Use size-based rotation on Windows to avoid file locking issues
            error_handler = RotatingFileHandler(
                os.path.join(log_dir, 'error.log'),
                maxBytes=5 * 1024 * 1024,  # 5MB
                backupCount=30
            )
        else:
            error_handler = TimedRotatingFileHandler(
                os.path.join(log_dir, 'error.log'),
                when='midnight',
                interval=1,
                backupCount=30
            )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(logging.Formatter(
            '[%(asctime)s] %(levelname)s in %(module)s [%(pathname)s:%(lineno)d]:\n%(message)s\n'
        ))
        app.logger.addHandler(error_handler)

        # 3. Security audit log (rotating by size on Windows, daily on Linux)
        if IS_WINDOWS:
            # Use size-based rotation on Windows to avoid file locking issues
            security_handler = RotatingFileHandler(
                os.path.join(log_dir, 'security.log'),
                maxBytes=5 * 1024 * 1024,  # 5MB
                backupCount=90  # Keep 90 files
            )
        else:
            security_handler = TimedRotatingFileHandler(
                os.path.join(log_dir, 'security.log'),
                when='midnight',
                interval=1,
                backupCount=90  # Keep for 90 days
            )
        security_handler.setLevel(logging.WARNING)
        security_handler.setFormatter(logging.Formatter(
            '[%(asctime)s] SECURITY: %(message)s'
        ))
        self.security_logger = logging.getLogger('sincor.security')
        self.security_logger.addHandler(security_handler)
        self.security_logger.setLevel(logging.WARNING)

        # 4. Access log (rotating by size on Windows, daily on Linux)
        if IS_WINDOWS:
            # Use size-based rotation on Windows to avoid file locking issues
            access_handler = RotatingFileHandler(
                os.path.join(log_dir, 'access.log'),
                maxBytes=5 * 1024 * 1024,  # 5MB
                backupCount=30
            )
        else:
            access_handler = TimedRotatingFileHandler(
                os.path.join(log_dir, 'access.log'),
                when='midnight',
                interval=1,
                backupCount=30
            )
        access_handler.setLevel(logging.INFO)
        access_handler.setFormatter(logging.Formatter(
            '[%(asctime)s] %(message)s'
        ))
        self.access_logger = logging.getLogger('sincor.access')
        self.access_logger.addHandler(access_handler)
        self.access_logger.setLevel(logging.INFO)

        # 5. Console output (development)
        if os.environ.get('FLASK_ENV') == 'development':
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.DEBUG)
            console_handler.setFormatter(logging.Formatter(
                '[%(levelname)s] %(message)s'
            ))
            app.logger.addHandler(console_handler)

        # Set app logger level
        app.logger.setLevel(numeric_level)

        # Log startup
        app.logger.info('='*60)
        app.logger.info('SINCOR Platform Starting')
        app.logger.info(f'Log Level: {log_level}')
        app.logger.info(f'Environment: {os.environ.get("FLASK_ENV", "production")}')
        app.logger.info('='*60)

        # Add request/response logging
        self._setup_request_logging(app)

    def _setup_request_logging(self, app):
        """Setup automatic request/response logging"""

        @app.before_request
        def log_request():
            """Log incoming requests"""
            from flask import request

            # Log access
            self.access_logger.info(
                f'{request.remote_addr} - {request.method} {request.path}'
            )

            # Log security-relevant requests
            if request.path.startswith('/api/auth/') or request.path.startswith('/api/admin/'):
                self.security_logger.info(
                    f'AUTH_REQUEST: {request.remote_addr} - {request.method} {request.path}'
                )

        @app.after_request
        def log_response(response):
            """Log outgoing responses"""
            from flask import request

            # Log errors
            if response.status_code >= 400:
                app.logger.warning(
                    f'{request.method} {request.path} - {response.status_code}'
                )

            # Log security events
            if response.status_code == 401:
                self.security_logger.warning(
                    f'UNAUTHORIZED: {request.remote_addr} - {request.method} {request.path}'
                )
            elif response.status_code == 429:
                self.security_logger.warning(
                    f'RATE_LIMIT: {request.remote_addr} - {request.method} {request.path}'
                )

            return response

        @app.errorhandler(Exception)
        def log_exception(e):
            """Log unhandled exceptions"""
            from flask import request

            app.logger.error(
                f'Unhandled exception on {request.method} {request.path}:\n{str(e)}',
                exc_info=True
            )

            self.security_logger.error(
                f'EXCEPTION: {request.remote_addr} - {request.method} {request.path} - {str(e)}'
            )

            return {'error': 'Internal server error'}, 500

    def log_login_attempt(self, username, success, ip_address):
        """Log login attempts for security monitoring"""
        status = 'SUCCESS' if success else 'FAILED'
        self.security_logger.warning(
            f'LOGIN_{status}: user={username} ip={ip_address}'
        )

    def log_rate_limit_hit(self, endpoint, ip_address):
        """Log rate limit violations"""
        self.security_logger.warning(
            f'RATE_LIMIT_HIT: endpoint={endpoint} ip={ip_address}'
        )

    def log_validation_error(self, endpoint, error, ip_address):
        """Log validation errors (potential attack attempts)"""
        self.security_logger.warning(
            f'VALIDATION_ERROR: endpoint={endpoint} error={error} ip={ip_address}'
        )

    def log_payment_event(self, event_type, amount, status, user_id=None):
        """Log payment-related events"""
        self.security_logger.info(
            f'PAYMENT_{event_type}: amount=${amount} status={status} user={user_id}'
        )


def get_logger_stats():
    """Get logging statistics"""
    log_dir = os.path.join(os.path.dirname(__file__), 'logs')

    if not os.path.exists(log_dir):
        return {'error': 'Log directory not found'}

    stats = {}
    for log_file in ['app.log', 'error.log', 'security.log', 'access.log']:
        file_path = os.path.join(log_dir, log_file)
        if os.path.exists(file_path):
            size = os.path.getsize(file_path)
            with open(file_path, 'r') as f:
                lines = sum(1 for _ in f)
            stats[log_file] = {
                'size_mb': round(size / (1024 * 1024), 2),
                'lines': lines,
                'last_modified': datetime.fromtimestamp(
                    os.path.getmtime(file_path)
                ).strftime('%Y-%m-%d %H:%M:%S')
            }

    return stats


def tail_log(log_file='app.log', lines=50):
    """Get last N lines from a log file"""
    log_dir = os.path.join(os.path.dirname(__file__), 'logs')
    file_path = os.path.join(log_dir, log_file)

    if not os.path.exists(file_path):
        return f"Log file not found: {log_file}"

    with open(file_path, 'r') as f:
        all_lines = f.readlines()
        return ''.join(all_lines[-lines:])


# Test function
def test_logger():
    """Test logging configuration"""
    print("Testing SINCOR Logger...")

    # Create test app
    from flask import Flask
    app = Flask(__name__)

    # Initialize logger
    logger = SINCORLogger(app)

    # Test logging
    app.logger.info("Test INFO message")
    app.logger.warning("Test WARNING message")
    app.logger.error("Test ERROR message")

    logger.log_login_attempt("testuser", True, "127.0.0.1")
    logger.log_login_attempt("baduser", False, "192.168.1.1")
    logger.log_rate_limit_hit("/api/auth/login", "10.0.0.1")
    logger.log_payment_event("CREATE", 100.00, "pending", "user123")

    # Get stats
    stats = get_logger_stats()
    print("\nLog Statistics:")
    for log_file, info in stats.items():
        print(f"  {log_file}:")
        print(f"    Size: {info['size_mb']} MB")
        print(f"    Lines: {info['lines']}")
        print(f"    Last Modified: {info['last_modified']}")

    print("\nLogging ready!")
    print("\nLog Files Created:")
    print("  - logs/app.log (general application logs)")
    print("  - logs/error.log (errors and exceptions)")
    print("  - logs/security.log (security events)")
    print("  - logs/access.log (HTTP requests)")

    print("\nFeatures:")
    print("  - Automatic log rotation (size and time-based)")
    print("  - Security audit trail")
    print("  - Request/response logging")
    print("  - Error tracking with stack traces")
    print("  - Login attempt monitoring")
    print("  - Rate limit violation tracking")


if __name__ == "__main__":
    test_logger()
