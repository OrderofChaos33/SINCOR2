from __future__ import annotations

from abc import ABC, abstractmethod
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class VerticalAgent(ABC):
    """
    Production-grade base class for vertical-specific agents.
    All vertical agents should inherit from this class.
    """

    name: str = "base_vertical_agent"
    version: str = "0.1.0"
    capabilities: list[str] = []
    description: str = ""

    def __init__(self):
        self.logger = logging.getLogger(f"vertical.{self.name}")

    @abstractmethod
    def execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a task. Must be implemented by subclasses.
        Should return a structured result dict.
        """
        pass

    def get_agent_card(self) -> Dict[str, Any]:
        """Return machine-readable Agent Card for A2A discovery."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "capabilities": self.capabilities,
            "input_schema": "See schemas.py",
            "output_schema": "See schemas.py",
        }

    def validate_task(self, task: Dict[str, Any]) -> bool:
        """Basic validation hook. Override for stricter checks."""
        return isinstance(task, dict)

    def handle_error(self, error: Exception, task: Dict[str, Any]) -> Dict[str, Any]:
        self.logger.error(f"Error executing task: {error}", exc_info=True)
        return {"status": "error", "message": str(error)}
