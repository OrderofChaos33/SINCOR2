from __future__ import annotations

"""Bridge utilities for integrating SINCOR2 agents with n8n workflows."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4


@dataclass
class WorkflowExecution:
    """Represents the state of an n8n workflow execution."""

    workflow_id: str
    execution_id: str = field(default_factory=lambda: f"n8n-{uuid4().hex[:12]}")
    status: str = 'queued'
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class N8NBridgeAgent:
    """Provides a simple interface for workflow triggers and webhook normalization."""

    def __init__(self) -> None:
        self.executions: Dict[str, WorkflowExecution] = {}

    def trigger_workflow(self, workflow_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create a mock workflow execution request."""
        execution = WorkflowExecution(workflow_id=workflow_id, status='running')
        self.executions[execution.execution_id] = execution
        return {
            'workflow_id': workflow_id,
            'execution_id': execution.execution_id,
            'status': execution.status,
            'accepted_payload_keys': sorted(payload.keys()),
        }

    def get_workflow_status(self, execution_id: str) -> Dict[str, str]:
        """Return the state of a previously triggered execution."""
        execution = self.executions.get(execution_id)
        if execution is None:
            return {'execution_id': execution_id, 'status': 'unknown'}
        return {
            'execution_id': execution.execution_id,
            'workflow_id': execution.workflow_id,
            'status': execution.status,
            'created_at': execution.created_at,
        }

    def parse_webhook_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize incoming webhook payloads into a compact internal envelope."""
        return {
            'event': payload.get('event') or payload.get('type') or 'unknown',
            'status': payload.get('status', 'received'),
            'execution_id': payload.get('executionId') or payload.get('execution_id'),
            'data': payload.get('data', payload),
            'received_at': datetime.now(timezone.utc).isoformat(),
        }
