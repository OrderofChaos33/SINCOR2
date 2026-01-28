"""
SINCOR Authentication System
JWT-based authentication for protecting admin and sensitive endpoints
"""

import os
from datetime import timedelta
from flask import jsonify, request
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    create_refresh_token,
    jwt_required,
    get_jwt_identity,
    get_jwt
)
from functools import wraps


class SINCORAuth:
    """SINCOR Authentication Manager"""

    def __init__(self, app=None):
        self.app = app
        self.jwt = None

        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize JWT authentication with Flask app"""

        # JWT Configuration
        app.config['JWT_SECRET_KEY'] = os.environ.get(
            'JWT_SECRET_KEY',
            'dev-secret-key-CHANGE-IN-PRODUCTION-min-32-chars'
        )
        app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=1)
        app.config['JWT_REFRESH_TOKEN_EXPIRES'] = timedelta(days=30)
        app.config['JWT_TOKEN_LOCATION'] = ['headers']
        app.config['JWT_HEADER_NAME'] = 'Authorization'
        app.config['JWT_HEADER_TYPE'] = 'Bearer'

        # Initialize JWT Manager
        self.jwt = JWTManager(app)

        # JWT Error Handlers
        @self.jwt.expired_token_loader
        def expired_token_callback(jwt_header, jwt_payload):
            return jsonify({
                'success': False,
                'error': 'Token has expired',
                'error_code': 'token_expired'
            }), 401

        @self.jwt.invalid_token_loader
        def invalid_token_callback(error):
            return jsonify({
                'success': False,
                'error': 'Invalid token',
                'error_code': 'invalid_token'
            }), 401

        @self.jwt.unauthorized_loader
        def missing_token_callback(error):
            return jsonify({
                'success': False,
                'error': 'Authorization token required',
                'error_code': 'missing_token'
            }), 401

        @self.jwt.revoked_token_loader
        def revoked_token_callback(jwt_header, jwt_payload):
            return jsonify({
                'success': False,
                'error': 'Token has been revoked',
                'error_code': 'token_revoked'
            }), 401

    def authenticate_user(self, username: str, password: str) -> dict:
        """
        Authenticate user credentials

        TODO: Replace with database lookup for production
        For now, uses environment variables for admin credentials
        """

        # Get admin credentials from environment
        valid_username = os.environ.get('ADMIN_USERNAME', 'admin')
        valid_password = os.environ.get('ADMIN_PASSWORD', 'changeme123')

        if username == valid_username and password == valid_password:
            # Create access and refresh tokens
            access_token = create_access_token(
                identity=username,
                additional_claims={'role': 'admin'}
            )
            refresh_token = create_refresh_token(identity=username)

            return {
                'success': True,
                'access_token': access_token,
                'refresh_token': refresh_token,
                'expires_in': 3600,  # 1 hour
                'token_type': 'Bearer',
                'user': {
                    'username': username,
                    'role': 'admin'
                }
            }
        else:
            return {
                'success': False,
                'error': 'Invalid username or password'
            }

    def create_tokens(self, username: str, role: str = 'user') -> dict:
        """Create access and refresh tokens for a user"""

        access_token = create_access_token(
            identity=username,
            additional_claims={'role': role}
        )
        refresh_token = create_refresh_token(identity=username)

        return {
            'access_token': access_token,
            'refresh_token': refresh_token,
            'expires_in': 3600,
            'token_type': 'Bearer'
        }

    def get_current_user(self) -> str:
        """Get current authenticated user from JWT"""
        return get_jwt_identity()

    def get_current_user_role(self) -> str:
        """Get current user's role from JWT claims"""
        claims = get_jwt()
        return claims.get('role', 'user')


def admin_required():
    """Decorator to require admin role for endpoint access"""
    def wrapper(fn):
        @wraps(fn)
        @jwt_required()
        def decorator(*args, **kwargs):
            claims = get_jwt()
            role = claims.get('role', 'user')

            if role != 'admin':
                return jsonify({
                    'success': False,
                    'error': 'Admin access required'
                }), 403

            return fn(*args, **kwargs)
        return decorator
    return wrapper


# Convenience decorator exports
def login_required():
    """Decorator to require authentication (any valid user)"""
    return jwt_required()


def optional_login():
    """Decorator for optional authentication"""
    return jwt_required(optional=True)


# Test function
def test_auth():
    """Test authentication system"""
    print("Testing SINCOR Authentication System...")

    # Check if JWT secret is set
    jwt_secret = os.environ.get('JWT_SECRET_KEY')
    if jwt_secret:
        print(f"[OK] JWT_SECRET_KEY configured (length: {len(jwt_secret)})")
    else:
        print("[WARNING] JWT_SECRET_KEY not set - using default (INSECURE)")

    # Check if admin credentials are set
    admin_user = os.environ.get('ADMIN_USERNAME', 'admin')
    admin_pass = os.environ.get('ADMIN_PASSWORD')

    if admin_pass and admin_pass != 'changeme123':
        print(f"[OK] Admin credentials configured: {admin_user}")
    else:
        print(f"[WARNING] Using default admin password (INSECURE)")

    print("\n[OK] Authentication system ready")
    print("\nDefault credentials:")
    print(f"  Username: {admin_user}")
    print(f"  Password: {admin_pass or 'changeme123'}")
    print("\n[WARNING] CHANGE THESE IN PRODUCTION!")


if __name__ == "__main__":
    test_auth()
