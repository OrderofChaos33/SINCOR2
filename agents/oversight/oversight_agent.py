
import sys
from pathlib import Path

# Add parent directory to path to import base_agent
sys.path.append(str(Path(__file__).parent.parent))
from base_agent import BaseAgent

class OversightAgent(BaseAgent):
    """Agent responsible for system oversight and monitoring."""
    
    def __init__(self, name="Oversight", log_path="logs/oversight.log", config=None):
        super().__init__(name, log_path, config)
        self.monitored_systems = config.get("monitored_systems", []) if config else []
        
    def _run_custom_diagnostics(self):
        """Run oversight-specific diagnostics."""
        try:
            custom_checks = {
                "monitored_systems_count": len(self.monitored_systems),
                "system_health": self._check_system_health(),
                "disk_space": self._check_disk_space(),
                "log_files": self._check_log_files()
            }
            return custom_checks
        except Exception as e:
            return {"custom_diagnostics_error": str(e)}
    
    def _check_system_health(self):
        """Check basic system health metrics."""
        try:
            # Check if key directories exist
            required_dirs = ["logs", "outputs", "config"]
            base_path = Path(__file__).parent.parent.parent
            
            health = {}
            for dir_name in required_dirs:
                dir_path = base_path / dir_name
                health[f"{dir_name}_exists"] = dir_path.exists()
                if dir_path.exists():
                    health[f"{dir_name}_writable"] = os.access(dir_path, os.W_OK)
            
            return health
        except Exception as e:
            return {"error": str(e)}
    
    def _check_disk_space(self):
        """Check available disk space."""
        try:
            import shutil
            base_path = Path(__file__).parent.parent.parent
            total, used, free = shutil.disk_usage(base_path)
            
            return {
                "total_gb": round(total / (1024**3), 2),
                "used_gb": round(used / (1024**3), 2), 
                "free_gb": round(free / (1024**3), 2),
                "usage_percent": round((used / total) * 100, 1)
            }
        except Exception as e:
            return {"error": str(e)}
    
    def _check_log_files(self):
        """Check log files status."""
        try:
            base_path = Path(__file__).parent.parent.parent
            logs_dir = base_path / "logs"
            
            if not logs_dir.exists():
                return {"error": "logs directory not found"}
            
            log_files = list(logs_dir.glob("*.log"))
            log_info = {}
            
            for log_file in log_files:
                try:
                    stat = log_file.stat()
                    log_info[log_file.name] = {
                        "size_mb": round(stat.st_size / (1024**2), 2),
                        "modified": stat.st_mtime
                    }
                except Exception as e:
                    log_info[log_file.name] = {"error": str(e)}
            
            return {
                "total_log_files": len(log_files),
                "files": log_info
            }
        except Exception as e:
            return {"error": str(e)}
    
    def _custom_shutdown(self):
        """Custom shutdown procedures for oversight agent."""
        self._log("Oversight agent performing final system check before shutdown")
        final_check = self._check_system_health()
        self._log(f"Final system health: {final_check}")

import os
