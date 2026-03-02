"""
Pytest configuration and fixtures for SINCOR2 test suite.
"""
import os
import pytest

# These test files use module-level code and sys.exit; exclude from pytest collection
collect_ignore = [
    "tests/test_units.py",
    "tests/test_value.py",
]


def pytest_configure(config):
    """Set environment variables required for tests."""
    os.environ.setdefault('LOGIN_EMAIL', 'user@example.com')
    os.environ.setdefault('LOGIN_PASSWORD', 'demo')
    os.environ.setdefault('JWT_SECRET_KEY', 'test-secret-key')
