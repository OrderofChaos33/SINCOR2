"""
SINCOR2 - A comprehensive platform for intelligent commerce, monetization, and content management.

This package provides:
- Flask-based web application framework
- AI-powered content and monetization engines
- Real-time intelligence and analytics
- User authentication and authorization
- Rate limiting and security features
- PayPal and Square payment integration
"""

__version__ = "1.0.0"
__author__ = "OrderofChaos33"
__description__ = "SINCOR Master Platform - Production Ready"

# Public API exports (optional, for convenience imports)
try:
    from sincor2.app import create_app
except ImportError:
    pass  # app.py may not be importable at package level

__all__ = [
    "__version__",
    "__author__",
    "__description__",
]
