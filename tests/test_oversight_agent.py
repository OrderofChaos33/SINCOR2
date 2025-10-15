import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

# Import the agent
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "agents" / "oversight"))
from oversight_agent import OversightAgent

class TestOversightAgent:
    """Test the OversightAgent class."""
    
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
        agent = OversightAgent(name="TestAgent", log_path=str(temp_log_path))
        
        assert agent.name == "TestAgent"
        assert agent.log_path == temp_log_path
        assert agent.heartbeat_count == 0
        assert temp_log_path.parent.exists()
    
    def test_heartbeat(self, temp_log_path):
        """Test heartbeat functionality."""
        agent = OversightAgent(log_path=str(temp_log_path))
        
        with patch('builtins.print') as mock_print:
            agent.heartbeat()
            agent.heartbeat()
            
            assert agent.heartbeat_count == 2
            assert mock_print.call_count == 2
            
            # Check log was written
            log_content = temp_log_path.read_text(encoding="utf-8")
            assert "Heartbeat #1" in log_content
            assert "Heartbeat #2" in log_content
    
    def test_log_writing(self, temp_log_path):
        """Test log writing functionality."""
        agent = OversightAgent(log_path=str(temp_log_path))
        
        agent._log("Test message")
        agent._log("Another message")
        
        log_content = temp_log_path.read_text(encoding="utf-8")
        assert "Oversight: Test message" in log_content
        assert "Oversight: Another message" in log_content
        # Check timestamps are included
        assert "[" in log_content and "]" in log_content
    
    def test_run_diagnostics(self, temp_log_path):
        """Test diagnostics functionality."""
        agent = OversightAgent(log_path=str(temp_log_path))
        
        with patch('builtins.print') as mock_print:
            result = agent.run_diagnostics()
            
            assert isinstance(result, dict)
            assert "log_writable" in result
            assert "timestamp" in result
            assert result["log_writable"] is True
            
            mock_print.assert_called_with("[Oversight] Running diagnostics...")
            
            # Check diagnostic was logged
            log_content = temp_log_path.read_text(encoding="utf-8")
            assert "Diagnostics check OK" in log_content
    
    def test_error_handling_heartbeat(self, temp_log_path):
        """Test error handling in heartbeat."""
        agent = OversightAgent(log_path=str(temp_log_path))
        
        with patch.object(agent, '_log', side_effect=Exception("Log error")), \
             patch('builtins.print') as mock_print:
            
            agent.heartbeat()
            
            # Should still increment counter
            assert agent.heartbeat_count == 1
            # Should print error
            mock_print.assert_any_call("[Oversight] ERROR in heartbeat: Log error")
    
    def test_error_handling_diagnostics(self, temp_log_path):
        """Test error handling in diagnostics."""
        agent = OversightAgent(log_path=str(temp_log_path))
        
        with patch.object(agent, '_check_log_writable', side_effect=Exception("Check error")), \
             patch('builtins.print') as mock_print:
            
            result = agent.run_diagnostics()
            
            assert "error" in result
            assert "Check error" in result["error"]
            mock_print.assert_any_call("[Oversight] ERROR: Diagnostics failed: Check error")
    
    def test_check_log_writable_success(self, temp_log_path):
        """Test log writable check when successful."""
        agent = OversightAgent(log_path=str(temp_log_path))
        
        result = agent._check_log_writable()
        assert result is True
        
        # Check test message was written
        log_content = temp_log_path.read_text(encoding="utf-8")
        assert "Log write test" in log_content
    
    def test_check_log_writable_failure(self):
        """Test log writable check when it fails."""
        # Use a path that should fail (directory that doesn't exist and can't be created)
        invalid_path = "/nonexistent/path/that/should/fail.log"
        agent = OversightAgent(log_path=invalid_path)
        
        result = agent._check_log_writable()
        assert result is False