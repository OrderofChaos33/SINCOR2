"""
SINCOR Rate Limiting System
Protects API endpoints from abuse, DDoS attacks, and brute force attempts
"""

import os
from flask import request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address


class SINCORRateLimiter:
    """SINCOR Rate Limiter Manager"""

    def __init__(self, app=None):
        self.app = app
        self.limiter = None

        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize rate limiter with Flask app"""

        # Rate limiting configuration
        storage_uri = os.environ.get('RATE_LIMIT_STORAGE_URI', 'memory://')

        # For production, use Redis:
        # storage_uri = "redis://localhost:6379"

        # Initialize Flask-Limiter
        self.limiter = Limiter(
            app=app,
            key_func=get_remote_address,
            default_limits=["1000 per day", "200 per hour"],
            storage_uri=storage_uri,
            storage_options={},
            strategy="fixed-window",
            headers_enabled=True,
            swallow_errors=True  # Don't crash if Redis is down
        )

        # Custom error handler for rate limit exceeded
        @app.errorhandler(429)
        def ratelimit_handler(e):
            return jsonify({
                'success': False,
                'error': 'Rate limit exceeded',
                'error_code': 'rate_limit_exceeded',
                'message': 'Too many requests. Please try again later.',
                'retry_after': getattr(e, 'description', 'unknown')
            }), 429

    def get_limiter(self):
        """Get the limiter instance for use in decorators"""
        return self.limiter


# Rate limit configurations by endpoint type

# Authentication endpoints - strict limits to prevent brute force
# Max 5 login attempts per minute, 20 per hour, 50 per day
AUTH_LIMITS = "5 per minute;20 per hour;50 per day"

# Payment endpoints - moderate limits for legitimate use
# Max 10 payment attempts per minute, 50 per hour, 200 per day
PAYMENT_LIMITS = "10 per minute;50 per hour;200 per day"

# Waitlist/public endpoints - generous limits
# Max 20 signups per minute, 100 per hour, 500 per day
PUBLIC_LIMITS = "20 per minute;100 per hour;500 per day"

# Admin endpoints - moderate limits
# Max 30 admin actions per minute, 200 per hour, 1000 per day
ADMIN_LIMITS = "30 per minute;200 per hour;1000 per day"

# API test/health endpoints - very generous
# Max 60 health checks per minute, 1000 per hour
MONITORING_LIMITS = "60 per minute;1000 per hour"

# Analytics endpoints - moderate
# Max 10 analytics requests per minute, 100 per hour
ANALYTICS_LIMITS = "10 per minute;100 per hour"


def get_rate_limit_config():
    """Get rate limit configuration summary"""
    return {
        'authentication': AUTH_LIMITS.split(';'),
        'payment': PAYMENT_LIMITS.split(';'),
        'public': PUBLIC_LIMITS.split(';'),
        'admin': ADMIN_LIMITS.split(';'),
        'monitoring': MONITORING_LIMITS.split(';'),
        'analytics': ANALYTICS_LIMITS.split(';')
    }


def get_client_identifier():
    """
    Get unique identifier for rate limiting
    Can be customized to use JWT identity, API key, etc.
    """
    # Try to get authenticated user first
    try:
        from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request
        verify_jwt_in_request(optional=True)
        user = get_jwt_identity()
        if user:
            return f"user:{user}"
    except:
        pass

    # Fall back to IP address
    return f"ip:{get_remote_address()}"


# Custom key function for user-based rate limiting
def user_or_ip_based_limiter():
    """Rate limit by authenticated user or IP address"""
    return get_client_identifier()


# Test function
def test_rate_limiter():
    """Test rate limiter configuration"""
    print("Testing SINCOR Rate Limiter...")

    # Check storage configuration
    storage_uri = os.environ.get('RATE_LIMIT_STORAGE_URI', 'memory://')
    print(f"Storage: {storage_uri}")

    if storage_uri == 'memory://':
        print("  Note: Using in-memory storage (not suitable for production with multiple workers)")
        print("  For production, use Redis: RATE_LIMIT_STORAGE_URI=redis://localhost:6379")

    # Display rate limit configs
    print("\nRate Limit Configurations:")
    config = get_rate_limit_config()

    for endpoint_type, limits in config.items():
        print(f"\n  {endpoint_type.upper()}:")
        for limit in limits:
            print(f"    - {limit}")

    print("\nRate limiting ready!")
    print("\nRecommendations:")
    print("  1. Use Redis for production (multi-worker support)")
    print("  2. Monitor rate limit hits in logs")
    print("  3. Adjust limits based on actual usage patterns")
    print("  4. Consider whitelist for trusted IPs")


if __name__ == "__main__":
    test_rate_limiter()
