#!/usr/bin/env python3
"""
SINCOR Crash Diagnostics and End-to-End Testing Suite

This script performs comprehensive health checks and crash diagnostics
to identify issues causing the SINCOR application to crash or fail.
"""

import os
import sys
import json
import time
import sqlite3
import requests
import subprocess
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any

class CrashDiagnostics:
    """Comprehensive crash diagnostics for SINCOR application."""
    
    def __init__(self):
        self.root_path = Path(__file__).parent
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "tests": [],
            "critical_issues": [],
            "warnings": [],
            "recommendations": []
        }
    
    def log_result(self, test_name: str, status: str, details: str = "", critical: bool = False):
        """Log a test result."""
        result = {
            "test": test_name,
            "status": status,
            "details": details,
            "timestamp": datetime.now().isoformat()
        }
        self.results["tests"].append(result)
        
        if status == "FAIL" and critical:
            self.results["critical_issues"].append(f"{test_name}: {details}")
        elif status == "WARN":
            self.results["warnings"].append(f"{test_name}: {details}")
            
        print(f"[{status}] {test_name}: {details}")
    
    def test_python_environment(self):
        """Test Python environment and dependencies."""
        print("\n=== Python Environment Tests ===")
        
        # Check Python version
        python_version = sys.version_info
        if python_version >= (3, 8):
            self.log_result("Python Version", "PASS", f"Python {python_version.major}.{python_version.minor}.{python_version.micro}")
        else:
            self.log_result("Python Version", "FAIL", f"Python {python_version.major}.{python_version.minor} (requires 3.8+)", critical=True)
        
        # Check required modules
        required_modules = [
            "flask", "requests", "sqlite3", "datetime", "pathlib", 
            "json", "threading", "time", "os", "sys", "re", "csv"
        ]
        
        for module in required_modules:
            try:
                if module == "sqlite3":
                    import sqlite3
                elif module == "flask":
                    import flask
                elif module == "requests":
                    import requests
                else:
                    __import__(module)
                self.log_result(f"Module: {module}", "PASS", "Available")
            except ImportError as e:
                self.log_result(f"Module: {module}", "FAIL", f"Not available: {e}", critical=True)
    
    def test_file_structure(self):
        """Test critical file and directory structure."""
        print("\n=== File Structure Tests ===")
        
        critical_files = [
            "sincor_app.py",
            "requirements.txt",
            "config/environment.sample.env",
            "agents/daetime/scheduler_harness.py",
            "agents/base_agent.py"
        ]
        
        critical_dirs = [
            "logs",
            "data",
            "outputs",
            "agents",
            "config",
            "templates"
        ]
        
        # Check critical files
        for file_path in critical_files:
            full_path = self.root_path / file_path
            if full_path.exists():
                self.log_result(f"File: {file_path}", "PASS", "Exists")
            else:
                self.log_result(f"File: {file_path}", "FAIL", "Missing", critical=True)
        
        # Check critical directories
        for dir_path in critical_dirs:
            full_path = self.root_path / dir_path
            if full_path.exists():
                self.log_result(f"Directory: {dir_path}", "PASS", "Exists")
            else:
                full_path.mkdir(exist_ok=True)
                self.log_result(f"Directory: {dir_path}", "WARN", "Created")
    
    def test_configuration(self):
        """Test configuration and environment variables."""
        print("\n=== Configuration Tests ===")
        
        # Check environment variables
        env_vars = [
            "FLASK_SECRET_KEY", "SMTP_HOST", "SMTP_USER", "SMTP_PASS",
            "EMAIL_FROM", "EMAIL_TO", "NOTIFY_PHONE"
        ]
        
        for var in env_vars:
            value = os.getenv(var, "")
            if value:
                self.log_result(f"ENV: {var}", "PASS", "Set")
            else:
                self.log_result(f"ENV: {var}", "WARN", "Not set")
        
        # Check config files
        config_files = [
            "config/environment.sample.env",
            "config/agent_roles.yaml"
        ]
        
        for config_file in config_files:
            full_path = self.root_path / config_file
            if full_path.exists():
                self.log_result(f"Config: {config_file}", "PASS", "Exists")
            else:
                self.log_result(f"Config: {config_file}", "WARN", "Missing")
    
    def test_database_connectivity(self):
        """Test database connections and integrity."""
        print("\n=== Database Tests ===")
        
        db_files = [
            "data/sincor_main.db",
            "data/business_intel.db",
            "data/business_intelligence.db",
            "data/compliance.db",
            "data/generated_content.db"
        ]
        
        for db_file in db_files:
            full_path = self.root_path / db_file
            try:
                if full_path.exists():
                    conn = sqlite3.connect(str(full_path))
                    cursor = conn.cursor()
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                    tables = cursor.fetchall()
                    conn.close()
                    self.log_result(f"DB: {db_file}", "PASS", f"{len(tables)} tables found")
                else:
                    self.log_result(f"DB: {db_file}", "WARN", "File doesn't exist")
            except Exception as e:
                self.log_result(f"DB: {db_file}", "FAIL", f"Error: {e}")
    
    def test_agent_modules(self):
        """Test agent module imports and initialization."""
        print("\n=== Agent Module Tests ===")
        
        agent_modules = [
            ("agents.daetime.scheduler_harness", "scheduler_harness"),
            ("agents.base_agent", "BaseAgent"),
            ("agents.intelligence.business_intel_agent", "BusinessIntelAgent"),
            ("agents.taskpool.taskpool_dispatcher", "TaskDispatcher")
        ]
        
        for module_path, class_name in agent_modules:
            try:
                sys.path.insert(0, str(self.root_path))
                module = __import__(module_path, fromlist=[class_name])
                if hasattr(module, class_name):
                    self.log_result(f"Agent: {module_path}", "PASS", f"{class_name} available")
                else:
                    self.log_result(f"Agent: {module_path}", "WARN", f"{class_name} not found")
            except ImportError as e:
                self.log_result(f"Agent: {module_path}", "FAIL", f"Import error: {e}")
            except Exception as e:
                self.log_result(f"Agent: {module_path}", "FAIL", f"Error: {e}")
    
    def test_route_modules(self):
        """Test Flask route module imports."""
        print("\n=== Route Module Tests ===")
        
        route_modules = [
            "checkout", "dashboard_routes", "media_pack_routes",
            "auto_detailing_routes", "authority_expansion",
            "business_discovery", "email_automation",
            "analytics_dashboard", "enterprise_domination",
            "security_compliance", "value_maximization",
            "conversion_optimization", "seo_domination",
            "admin_control"
        ]
        
        sys.path.insert(0, str(self.root_path))
        
        for module_name in route_modules:
            try:
                module = __import__(module_name)
                self.log_result(f"Route: {module_name}", "PASS", "Import successful")
            except ImportError as e:
                self.log_result(f"Route: {module_name}", "FAIL", f"Import failed: {e}")
            except Exception as e:
                self.log_result(f"Route: {module_name}", "FAIL", f"Error: {e}")
    
    def test_app_startup(self):
        """Test application startup without running the server."""
        print("\n=== Application Startup Test ===")
        
        try:
            sys.path.insert(0, str(self.root_path))
            
            # Try importing the main app
            spec = __import__("sincor_app")
            self.log_result("App Import", "PASS", "Main app imported successfully")
            
            # Check if Flask app is created
            if hasattr(spec, 'app'):
                self.log_result("Flask App", "PASS", "Flask app instance created")
            else:
                self.log_result("Flask App", "FAIL", "Flask app instance not found")
                
        except Exception as e:
            self.log_result("App Import", "FAIL", f"Failed to import app: {e}", critical=True)
    
    def test_network_connectivity(self):
        """Test network connectivity for external services."""
        print("\n=== Network Connectivity Tests ===")
        
        test_urls = [
            ("Google API", "https://maps.googleapis.com/"),
            ("Stripe API", "https://api.stripe.com/"),
            ("General Internet", "https://httpbin.org/get")
        ]
        
        for service_name, url in test_urls:
            try:
                response = requests.get(url, timeout=5)
                if response.status_code < 400:
                    self.log_result(f"Network: {service_name}", "PASS", f"Reachable ({response.status_code})")
                else:
                    self.log_result(f"Network: {service_name}", "WARN", f"HTTP {response.status_code}")
            except requests.RequestException as e:
                self.log_result(f"Network: {service_name}", "FAIL", f"Connection failed: {e}")
    
    def test_log_analysis(self):
        """Analyze recent logs for error patterns."""
        print("\n=== Log Analysis ===")
        
        log_files = [
            "logs/run.log",
            "logs/sincor_engine.log",
            "logs/oversight.log"
        ]
        
        error_patterns = [
            r"ERROR",
            r"CRITICAL",
            r"Exception",
            r"Traceback",
            r"not configured",
            r"Import.*Error",
            r"Connection.*failed"
        ]
        
        for log_file in log_files:
            full_path = self.root_path / log_file
            if full_path.exists():
                try:
                    with open(full_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        
                    error_count = 0
                    for pattern in error_patterns:
                        import re
                        matches = re.findall(pattern, content, re.IGNORECASE)
                        error_count += len(matches)
                    
                    if error_count == 0:
                        self.log_result(f"Log: {log_file}", "PASS", "No errors detected")
                    elif error_count < 5:
                        self.log_result(f"Log: {log_file}", "WARN", f"{error_count} errors found")
                    else:
                        self.log_result(f"Log: {log_file}", "FAIL", f"{error_count} errors found")
                        
                except Exception as e:
                    self.log_result(f"Log: {log_file}", "FAIL", f"Cannot read: {e}")
            else:
                self.log_result(f"Log: {log_file}", "WARN", "Log file missing")
    
    def generate_recommendations(self):
        """Generate recommendations based on test results."""
        print("\n=== Generating Recommendations ===")
        
        # Count issues
        critical_count = len(self.results["critical_issues"])
        warning_count = len(self.results["warnings"])
        
        recommendations = []
        
        if critical_count > 0:
            recommendations.append("CRITICAL: Fix critical issues before running the application")
            
        if "Google API key not configured" in str(self.results):
            recommendations.append("Configure Google API key for business intelligence features")
            
        if warning_count > 5:
            recommendations.append("Multiple warnings detected - review configuration")
            
        if any("Import failed" in str(test) for test in self.results["tests"]):
            recommendations.append("Install missing Python packages: pip install -r requirements.txt")
            
        recommendations.extend([
            "Run this diagnostic regularly to catch issues early",
            "Monitor logs/run.log for real-time issues", 
            "Implement proper error handling in agent modules",
            "Set up automated testing pipeline"
        ])
        
        self.results["recommendations"] = recommendations
        
        for rec in recommendations:
            print(f"  {rec}")
    
    def save_report(self):
        """Save detailed diagnostic report."""
        report_file = self.root_path / "logs" / f"crash_diagnostics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, indent=2)
        
        print(f"\nDetailed report saved to: {report_file}")
        return report_file
    
    def run_all_tests(self):
        """Run all diagnostic tests."""
        print("SINCOR Crash Diagnostics Starting...")
        print("=" * 50)
        
        # Run all test suites
        self.test_python_environment()
        self.test_file_structure()
        self.test_configuration()
        self.test_database_connectivity()
        self.test_agent_modules()
        self.test_route_modules()
        self.test_app_startup()
        self.test_network_connectivity()
        self.test_log_analysis()
        
        # Generate summary
        print("\n" + "=" * 50)
        print("DIAGNOSTIC SUMMARY")
        
        passed = len([t for t in self.results["tests"] if t["status"] == "PASS"])
        warned = len([t for t in self.results["tests"] if t["status"] == "WARN"])
        failed = len([t for t in self.results["tests"] if t["status"] == "FAIL"])
        
        print(f"PASSED: {passed}")
        print(f"WARNINGS: {warned}")
        print(f"FAILED: {failed}")
        print(f"CRITICAL: {len(self.results['critical_issues'])}")
        
        self.generate_recommendations()
        return self.save_report()


if __name__ == "__main__":
    diagnostics = CrashDiagnostics()
    report_file = diagnostics.run_all_tests()
    
    # Return appropriate exit code
    critical_issues = len(diagnostics.results["critical_issues"])
    sys.exit(critical_issues)