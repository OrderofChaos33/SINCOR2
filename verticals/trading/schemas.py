from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class TaskInput(BaseModel):
    task_type: str
    payload: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    correlation_id: Optional[str] = None


class TaskOutput(BaseModel):
    status: str = Field(..., pattern="^(success|error|partial)$")
    result: Dict[str, Any]
    error: Optional[str] = None
    correlation_id: Optional[str] = None
