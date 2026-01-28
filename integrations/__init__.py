"""Integrations package - lightweight stubs for tests and local development.
This package provides minimal, demo-safe implementations so integration tests
can be imported and run without external credentials. These are NOT production
implementations.
"""

__all__ = [
    'square_integration', 'crm_integration', 'email_workflow_system',
    'accounting_integration', 'square_workflow_optimizer'
]

from . import square_integration, crm_integration, email_workflow_system, accounting_integration, square_workflow_optimizer
