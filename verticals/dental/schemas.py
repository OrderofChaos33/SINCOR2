from pydantic import BaseModel, Field
from typing import Optional


class TaskInput(BaseModel):
    task_type: str
    payload: dict
    metadata: Optional[dict] = Field(default_factory=dict)


class TaskOutput(BaseModel):
    status: str
    result: dict
    error: Optional[str] = None
