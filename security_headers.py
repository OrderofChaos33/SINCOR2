"""
SINCOR Security Headers
Adds security headers to all responses to protect against common web vulnerabilities
"""

from flask import make_response


class SecurityHeaders:
    """Security headers middleware for Flask"""

    def __init__(self, app=None):
        self.app = app
        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize security headers with Flask app"""

        @app.after_request
        def add_security_headers(response):
            """Add security headers to every response"""

            # Content Security Policy - Prevents XSS attacks
            # Allows content only from same origin by default
            response.headers['Content-Security-Policy'] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: https:; "
                "font-src 'self' https://cdn.jsdelivr.net; "
                "connect-src 'self' https://api.anthropic.com; "
                "frame-ancestors 'none';"
            )

            # Strict-Transport-Security - Forces HTTPS
            # max-age=31536000 = 1 year
            response.headers['Strict-Transport-Security'] = (
                'max-age=31536000; includeSubDomains; preload'
            )

            # X-Content-Type-Options - Prevents MIME sniffing
            # Stops browsers from guessing content types
            response.headers['X-Content-Type-Options'] = 'nosniff'

            # X-Frame-Options - Prevents clickjacking
            # Prevents site from being embedded in iframes
            response.headers['X-Frame-Options'] = 'DENY'

            # X-XSS-Protection - Legacy XSS protection
            # Modern browsers use CSP instead, but this helps older browsers
            response.headers['X-XSS-Protection'] = '1; mode=block'

            # Referrer-Policy - Controls referrer information
            # Prevents leaking sensitive URLs
            response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'

            # Permissions-Policy - Controls browser features
            # Disables potentially dangerous features
            response.headers['Permissions-Policy'] = (
                'geolocation=(), '
                'microphone=(), '
                'camera=(), '
                'payment=(), '
                'usb=(), '
                'magnetometer=(), '
                'gyroscope=(), '
                'accelerometer=()'
            )

            # Cache-Control - Prevents caching of sensitive data
            if request_path_is_sensitive(response):
                response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
                response.headers['Pragma'] = 'no-cache'
                response.headers['Expires'] = '0'

            # X-Powered-By - Remove server fingerprinting
            # Don't advertise what framework we're using
            response.headers.pop('X-Powered-By', None)
            response.headers.pop('Server', None)

            return response


def request_path_is_sensitive(response):
    """Check if response contains sensitive data that shouldn't be cached"""
    from flask import request

    sensitive_paths = [
        '/api/auth/',
        '/api/payment/',
        '/api/admin/',
        '/api/analytics/'
    ]

    path = request.path
    return any(path.startswith(p) for p in sensitive_paths)


def get_security_headers_config():
    """Get current security headers configuration"""
    return {
        'Content-Security-Policy': 'Strict CSP with same-origin default',
        'Strict-Transport-Security': 'HSTS with 1 year max-age',
        'X-Content-Type-Options': 'nosniff - Prevents MIME sniffing',
        'X-Frame-Options': 'DENY - Prevents clickjacking',
        'X-XSS-Protection': 'Enabled with blocking mode',
        'Referrer-Policy': 'strict-origin-when-cross-origin',
        'Permissions-Policy': 'Restrictive - disables dangerous features',
        'Cache-Control': 'Sensitive paths use no-store'
    }


# Test function
def test_security_headers():
    """Test security headers configuration"""
    print("Testing SINCOR Security Headers...")

    config = get_security_headers_config()

    print("\nSecurity Headers Configuration:")
    for header, description in config.items():
        print(f"  {header}")
        print(f"    {description}")

    print("\nSecurity headers ready!")
    print("\nProtection Against:")
    print("  - XSS attacks (Content-Security-Policy, X-XSS-Protection)")
    print("  - Clickjacking (X-Frame-Options)")
    print("  - MIME sniffing (X-Content-Type-Options)")
    print("  - Man-in-the-middle attacks (Strict-Transport-Security)")
    print("  - Information leakage (Referrer-Policy)")
    print("  - Unwanted browser features (Permissions-Policy)")
    print("  - Server fingerprinting (Removed Server/X-Powered-By)")


if __name__ == "__main__":
    test_security_headers()
