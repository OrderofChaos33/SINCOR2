from __future__ import annotations

"""Deployment configuration helpers for local and Railway environments."""

import os
from typing import Dict, List, Optional


class DeploymentConfig:
    """Provides environment-aware deployment settings and validation."""

    def __init__(self, environment: Optional[str] = None) -> None:
        self.environment = environment or os.getenv('FLASK_ENV', 'production')

    def get_config(self) -> Dict[str, object]:
        """Return a normalized runtime configuration payload."""
        return {
            'environment': self.environment,
            'port': int(os.getenv('PORT', '8080')),
            'host': os.getenv('HOST', '0.0.0.0'),
            'railway_environment': os.getenv('RAILWAY_ENVIRONMENT'),
            'health_check_path': '/health',
            'base_url': os.getenv('PLATFORM_URL', 'https://getsincor.com'),
        }

    def validate_environment(self, required_keys: Optional[List[str]] = None) -> Dict[str, object]:
        """Validate that required environment variables are present."""
        required_keys = required_keys or ['SECRET_KEY', 'JWT_SECRET_KEY']
        missing = [key for key in required_keys if not os.getenv(key)]
        return {'valid': not missing, 'missing': missing, 'environment': self.environment}

    def generate_railway_config(self) -> Dict[str, object]:
        """Generate Railway-oriented deployment hints."""
        return {
            'build': {'builder': 'NIXPACKS'},
            'deploy': {
                'startCommand': 'python run.py',
                'healthcheckPath': '/health',
                'restartPolicyType': 'ON_FAILURE',
            },
            'variables': self.get_config(),
        }
