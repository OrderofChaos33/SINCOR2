import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

# Import the base agent
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))
from base_agent import BaseAgent

# Create a concrete test implementation
class TestAgent(BaseAgent):
    """Test implementation of BaseAgent."""
    
    def _run_custom_diagnostics(self):
        return {"test_check": "passed"}

class TestBaseAgent:
    """Test the BaseAgent class."""
    
    @pytest.fixture
    def temp_log_path(self):
        """Create a temporary log file path."""
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            yield Path(tmp.name)
            # Clean up
            if Path(tmp.name).exists():
                Path(tmp.name).unlink()
    
    def test_agent_initialization(self, temp_log_path):
        """Test agent initializes correctly."""
        config = {"test_key": "test_value"}
        agent = TestAgent(name="TestAgent", log_path=str(temp_log_path), config=config)
        
        assert agent.name == "TestAgent"
        assert agent.log_path == temp_log_path
        assert agent.config == config
        assert agent.heartbeat_count == 0
        assert agent.status == "initialized"
        assert agent.last_error is None
        assert temp_log_path.parent.exists()
    
    def test_heartbeat(self, temp_log_path):
        """Test heartbeat functionality."""
        agent = TestAgent(name="HeartbeatTest", log_path=str(temp_log_path))
        
        # First heartbeat
        result = agent.heartbeat()
        assert result is True
        assert agent.heartbeat_count == 1
        assert agent.status == "active"
        
        # Second heartbeat
        result = agent.heartbeat()
        assert result is True
        assert agent.heartbeat_count == 2
        
        # Check log was written
        log_content = temp_log_path.read_text(encoding="utf-8")
        assert "Heartbeat #1" in log_content
        assert "Heartbeat #2" in log_content
    
    def test_run_diagnostics(self, temp_log_path):
        """Test diagnostics functionality."""
        config = {"test_setting": True}
        agent = TestAgent(name="DiagnosticsTest", log_path=str(temp_log_path), config=config)
        
        # Run some heartbeats first
        agent.heartbeat()
        agent.heartbeat()
        
        result = agent.run_diagnostics()
        
        assert isinstance(result, dict)
        assert result["agent_name"] == "DiagnosticsTest"
        assert result["status"] == "active"
        assert result["heartbeat_count"] == 2
        assert result["last_error"] is None
        assert "timestamp" in result
        assert result["log_writable"] is True
        assert result["config_loaded"] is True
        assert result["test_check"] == "passed"  # From custom diagnostics
    
    def test_get_status(self, temp_log_path):
        """Test status retrieval."""
        agent = TestAgent(name="StatusTest", log_path=str(temp_log_path))
        agent.heartbeat()
        
        status = agent.get_status()
        
        assert status["name"] == "StatusTest"
        assert status["status"] == "active"
        assert status["heartbeat_count"] == 1
        assert status["last_error"] is None
        assert status["log_path"] == str(temp_log_path)
    
    def test_shutdown(self, temp_log_path):
        """Test graceful shutdown."""
        agent = TestAgent(name="ShutdownTest", log_path=str(temp_log_path))
        
        result = agent.shutdown()
        
        assert result is True
        assert agent.status == "stopped"
        
        # Check shutdown was logged
        log_content = temp_log_path.read_text(encoding="utf-8")
        assert "shutdown initiated" in log_content
        assert "shutdown completed" in log_content
    
    def test_error_handling_heartbeat(self, temp_log_path):
        """Test error handling in heartbeat."""
        agent = TestAgent(name="ErrorTest", log_path=str(temp_log_path))
        
        with patch.object(agent, '_log', side_effect=Exception("Log error")):
            result = agent.heartbeat()
            
            assert result is False
            assert agent.status == "error"
            assert "Log error" in agent.last_error
    
    def test_error_handling_diagnostics(self, temp_log_path):
        """Test error handling in diagnostics."""
        agent = TestAgent(name="ErrorTest", log_path=str(temp_log_path))
        
        with patch.object(agent, '_check_log_writable', side_effect=Exception("Check error")):
            result = agent.run_diagnostics()
            
            assert "error" in result
            assert "Check error" in result["error"]
            assert result["agent_name"] == "ErrorTest"
    
    def test_log_writing(self, temp_log_path):
        """Test log writing functionality."""
        agent = TestAgent(name="LogTest", log_path=str(temp_log_path))
        
        agent._log("Test message 1")
        agent._log("Test message 2")
        
        log_content = temp_log_path.read_text(encoding="utf-8")
        assert "LogTest: Test message 1" in log_content
        assert "LogTest: Test message 2" in log_content
        # Check timestamps are included
        assert "[" in log_content and "]" in log_content
    
    def test_log_error_handling(self):
        """Test log error handling when file can't be written."""
        # Use invalid path
        invalid_path = "/nonexistent/path/test.log"
        agent = TestAgent(name="LogErrorTest", log_path=invalid_path)
        
        with patch('builtins.print') as mock_print:
            agent._log("Test message")
            
            # Should print error to console
            mock_print.assert_called()
    
    def test_check_log_writable_success(self, temp_log_path):
        """Test log writable check when successful."""
        agent = TestAgent(name="WritableTest", log_path=str(temp_log_path))
        
        result = agent._check_log_writable()
        assert result is True
        
        # Check test message was written
        log_content = temp_log_path.read_text(encoding="utf-8")
        assert "Log write test" in log_content
    
    def test_check_log_writable_failure(self):
        """Test log writable check when it fails."""
        # Use a path that should fail
        invalid_path = "/nonexistent/path/test.log"
        agent = TestAgent(name="WriteFailTest", log_path=invalid_path)
        
        result = agent._check_log_writable()
        assert result is False