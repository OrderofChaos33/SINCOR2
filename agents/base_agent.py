"""
Base Agent Class for SINCOR

Provides common functionality and interface for all SINCOR agents.
All agents should inherit from this class to ensure consistency.
"""

import datetime
import json
from pathlib import Path
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class BaseAgent(ABC):
    """Base class for all SINCOR agents."""
    
    def __init__(self, name: str, log_path: str = "logs/agent.log", config: Optional[Dict] = None):
        """
        Initialize the base agent.
        
        Args:
            name: Agent name for logging and identification
            log_path: Path to log file (relative to project root)
            config: Optional configuration dictionary
        """
        self.name = name
        self.log_path = Path(log_path)
        self.config = config or {}
        self.heartbeat_count = 0
        self.status = "initialized"
        self.last_error = None
        
        # Ensure log directory exists
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._log(f"Agent {self.name} initialized")
    
    def heartbeat(self) -> bool:
        """
        Standard heartbeat function for all agents.
        
        Returns:
            bool: True if heartbeat successful, False otherwise
        """
        try:
            self.heartbeat_count += 1
            self.status = "active"
            self._log(f"Heartbeat #{self.heartbeat_count}")
            return True
        except Exception as e:
            self.last_error = str(e)
            self.status = "error"
            self._log(f"ERROR in heartbeat: {e}")
            return False
    
    def run_diagnostics(self) -> Dict[str, Any]:
        """
        Run diagnostic checks for the agent.
        
        Returns:
            dict: Diagnostic results
        """
        try:
            diagnostics = {
                "agent_name": self.name,
                "status": self.status,
                "heartbeat_count": self.heartbeat_count,
                "last_error": self.last_error,
                "timestamp": datetime.datetime.now().isoformat(),
                "log_writable": self._check_log_writable(),
                "config_loaded": bool(self.config),
            }
            
            # Add agent-specific diagnostics
            custom_diagnostics = self._run_custom_diagnostics()
            if custom_diagnostics:
                diagnostics.update(custom_diagnostics)
            
            self._log(f"Diagnostics completed: {json.dumps(diagnostics, indent=2)}")
            return diagnostics
            
        except Exception as e:
            error_msg = f"Diagnostics failed: {e}"
            self.last_error = str(e)
            self.status = "error"
            self._log(f"ERROR: {error_msg}")
            return {"error": str(e), "agent_name": self.name}
    
    def _log(self, message: str) -> None:
        """
        Write a message to the agent's log file.
        
        Args:
            message: Message to log
        """
        try:
            timestamp = datetime.datetime.now().isoformat(timespec="seconds")
            log_entry = f"[{timestamp}] {self.name}: {message}\n"
            
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(log_entry)
                
        except Exception as e:
            # If we can't log, at least print to console
            print(f"[{self.name}] LOG ERROR: {e}")
            print(f"[{self.name}] Original message: {message}")
    
    def _check_log_writable(self) -> bool:
        """
        Check if the log file is writable.
        
        Returns:
            bool: True if log is writable, False otherwise
        """
        try:
            test_msg = f"Log write test - {datetime.datetime.now().isoformat()}"
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"# {test_msg}\n")
            return True
        except Exception:
            return False
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current agent status.
        
        Returns:
            dict: Current status information
        """
        return {
            "name": self.name,
            "status": self.status,
            "heartbeat_count": self.heartbeat_count,
            "last_error": self.last_error,
            "log_path": str(self.log_path)
        }
    
    def shutdown(self) -> bool:
        """
        Graceful shutdown of the agent.
        
        Returns:
            bool: True if shutdown successful
        """
        try:
            self.status = "shutting_down"
            self._log("Agent shutdown initiated")
            
            # Allow agents to perform custom shutdown
            self._custom_shutdown()
            
            self.status = "stopped"
            self._log("Agent shutdown completed")
            return True
            
        except Exception as e:
            self.last_error = str(e)
            self._log(f"ERROR during shutdown: {e}")
            return False
    
    @abstractmethod
    def _run_custom_diagnostics(self) -> Optional[Dict[str, Any]]:
        """
        Override this method to add agent-specific diagnostic checks.
        
        Returns:
            dict or None: Custom diagnostic results
        """
        pass
    
    def _custom_shutdown(self) -> None:
        """
        Override this method to add agent-specific shutdown procedures.
        """
        pass