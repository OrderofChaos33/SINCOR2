#!/usr/bin/env python3
"""
SINCOR End-to-End Testing and Validation Suite

Comprehensive testing framework for validating SINCOR functionality
including web endpoints, agent operations, and system integration.
"""

import os
import sys
import json
import time
import requests
import threading
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any

class E2ETestSuite:
    """End-to-end testing suite for SINCOR application."""
    
    def __init__(self, base_url="http://localhost:5000"):
        self.base_url = base_url
        self.root_path = Path(__file__).parent
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "base_url": base_url,
            "tests": [],
            "summary": {},
            "failed_tests": [],
            "performance_metrics": {}
        }
        self.app_process = None
        self.app_started = False
    
    def log_test(self, test_name: str, status: str, details: str = "", duration: float = 0):
        """Log a test result."""
        result = {
            "test": test_name,
            "status": status,
            "details": details,
            "duration_ms": round(duration * 1000, 2),
            "timestamp": datetime.now().isoformat()
        }
        self.results["tests"].append(result)
        
        if status == "FAIL":
            self.results["failed_tests"].append(f"{test_name}: {details}")
        
        status_emoji = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "SKIP": "⏭️"}
        print(f"{status_emoji.get(status, '❓')} {test_name} ({duration*1000:.1f}ms): {details}")
    
    def start_app_server(self, timeout=30):
        """Start the SINCOR application server for testing."""
        print("\n🚀 Starting SINCOR application server...")
        
        try:
            # Change to app directory
            os.chdir(str(self.root_path))
            
            # Start the application
            self.app_process = subprocess.Popen(
                [sys.executable, "sincor_app.py"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Wait for app to start
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    response = requests.get(f"{self.base_url}/", timeout=2)
                    if response.status_code == 200:
                        self.app_started = True
                        print(f"✅ Application started successfully on {self.base_url}")
                        return True
                except requests.RequestException:
                    time.sleep(1)
                    continue
            
            print(f"❌ Application failed to start within {timeout} seconds")
            return False
            
        except Exception as e:
            print(f"❌ Failed to start application: {e}")
            return False
    
    def stop_app_server(self):
        """Stop the SINCOR application server."""
        if self.app_process:
            print("\n🛑 Stopping application server...")
            self.app_process.terminate()
            self.app_process.wait()
            self.app_started = False
    
    def test_basic_connectivity(self):
        """Test basic web connectivity."""
        print("\n=== Basic Connectivity Tests ===")
        
        if not self.app_started:
            self.log_test("Basic Connectivity", "SKIP", "App server not running")
            return
        
        start_time = time.time()
        try:
            response = requests.get(f"{self.base_url}/", timeout=5)
            duration = time.time() - start_time
            
            if response.status_code == 200:
                self.log_test("Homepage Load", "PASS", f"HTTP {response.status_code}", duration)
            else:
                self.log_test("Homepage Load", "FAIL", f"HTTP {response.status_code}", duration)
        except Exception as e:
            duration = time.time() - start_time
            self.log_test("Homepage Load", "FAIL", f"Connection error: {e}", duration)
    
    def test_api_endpoints(self):
        """Test critical API endpoints."""
        print("\n=== API Endpoint Tests ===")
        
        if not self.app_started:
            self.log_test("API Endpoints", "SKIP", "App server not running")
            return
        
        # Test endpoints that should exist
        endpoints = [
            ("/", "GET", "Homepage"),
            ("/health", "GET", "Health Check"),
            ("/api/status", "GET", "API Status"),
            ("/dashboard", "GET", "Dashboard"),
            ("/checkout", "GET", "Checkout"),
            ("/admin", "GET", "Admin Panel")
        ]
        
        for endpoint, method, description in endpoints:
            start_time = time.time()
            try:
                if method == "GET":
                    response = requests.get(f"{self.base_url}{endpoint}", timeout=5)
                else:
                    response = requests.request(method, f"{self.base_url}{endpoint}", timeout=5)
                
                duration = time.time() - start_time
                
                if response.status_code < 500:  # Accept 404s as valid responses
                    self.log_test(f"Endpoint: {description}", "PASS", f"HTTP {response.status_code}", duration)
                else:
                    self.log_test(f"Endpoint: {description}", "FAIL", f"HTTP {response.status_code}", duration)
                    
            except Exception as e:
                duration = time.time() - start_time
                self.log_test(f"Endpoint: {description}", "FAIL", f"Error: {e}", duration)
    
    def test_lead_capture_flow(self):
        """Test lead capture functionality."""
        print("\n=== Lead Capture Flow Tests ===")
        
        if not self.app_started:
            self.log_test("Lead Capture", "SKIP", "App server not running")
            return
        
        # Test lead submission
        test_lead_data = {
            "name": "Test Customer",
            "phone": "+1234567890",
            "service": "Full Detail",
            "email": "test@example.com"
        }
        
        start_time = time.time()
        try:
            response = requests.post(
                f"{self.base_url}/submit-lead",
                data=test_lead_data,
                timeout=10
            )
            duration = time.time() - start_time
            
            if response.status_code in [200, 201, 302]:  # Success or redirect
                self.log_test("Lead Submission", "PASS", f"HTTP {response.status_code}", duration)
            else:
                self.log_test("Lead Submission", "FAIL", f"HTTP {response.status_code}", duration)
                
        except Exception as e:
            duration = time.time() - start_time
            self.log_test("Lead Submission", "FAIL", f"Error: {e}", duration)
    
    def test_agent_functionality(self):
        """Test agent system functionality."""
        print("\n=== Agent System Tests ===")
        
        # Test agent module imports
        agent_tests = [
            ("scheduler_harness", "agents.daetime.scheduler_harness"),
            ("base_agent", "agents.base_agent"),
            ("taskpool_dispatcher", "agents.taskpool.taskpool_dispatcher")
        ]
        
        for agent_name, module_path in agent_tests:
            start_time = time.time()
            try:
                sys.path.insert(0, str(self.root_path))
                __import__(module_path)
                duration = time.time() - start_time
                self.log_test(f"Agent Import: {agent_name}", "PASS", "Module loaded", duration)
            except Exception as e:
                duration = time.time() - start_time
                self.log_test(f"Agent Import: {agent_name}", "FAIL", f"Import error: {e}", duration)
    
    def test_database_operations(self):
        """Test database connectivity and operations."""
        print("\n=== Database Operation Tests ===")
        
        import sqlite3
        
        # Test main database
        db_path = self.root_path / "data" / "sincor_main.db"
        start_time = time.time()
        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            
            # Test basic query
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = cursor.fetchall()
            
            conn.close()
            duration = time.time() - start_time
            self.log_test("Database Connection", "PASS", f"{len(tables)} tables found", duration)
            
        except Exception as e:
            duration = time.time() - start_time
            self.log_test("Database Connection", "FAIL", f"DB error: {e}", duration)
    
    def test_file_operations(self):
        """Test critical file operations."""
        print("\n=== File Operation Tests ===")
        
        # Test log writing
        start_time = time.time()
        try:
            test_log_path = self.root_path / "logs" / "e2e_test.log"
            with open(test_log_path, 'w') as f:
                f.write(f"E2E test log entry at {datetime.now().isoformat()}\n")
            
            # Verify file exists and is readable
            with open(test_log_path, 'r') as f:
                content = f.read()
            
            duration = time.time() - start_time
            self.log_test("Log File Writing", "PASS", "File created and readable", duration)
            
            # Clean up
            test_log_path.unlink()
            
        except Exception as e:
            duration = time.time() - start_time
            self.log_test("Log File Writing", "FAIL", f"File error: {e}", duration)
    
    def test_performance_metrics(self):
        """Collect performance metrics."""
        print("\n=== Performance Metrics ===")
        
        if not self.app_started:
            self.log_test("Performance Metrics", "SKIP", "App server not running")
            return
        
        # Test response times for key endpoints
        endpoints = ["/", "/dashboard", "/checkout"]
        
        for endpoint in endpoints:
            times = []
            for i in range(5):  # 5 requests per endpoint
                start_time = time.time()
                try:
                    response = requests.get(f"{self.base_url}{endpoint}", timeout=5)
                    duration = time.time() - start_time
                    if response.status_code < 500:
                        times.append(duration)
                except:
                    continue
            
            if times:
                avg_time = sum(times) / len(times)
                max_time = max(times)
                self.results["performance_metrics"][endpoint] = {
                    "avg_response_time": round(avg_time * 1000, 2),
                    "max_response_time": round(max_time * 1000, 2),
                    "requests_tested": len(times)
                }
                
                if avg_time < 1.0:  # Less than 1 second
                    self.log_test(f"Performance: {endpoint}", "PASS", f"Avg: {avg_time*1000:.1f}ms", avg_time)
                elif avg_time < 3.0:  # Less than 3 seconds
                    self.log_test(f"Performance: {endpoint}", "WARN", f"Avg: {avg_time*1000:.1f}ms", avg_time)
                else:
                    self.log_test(f"Performance: {endpoint}", "FAIL", f"Avg: {avg_time*1000:.1f}ms", avg_time)
    
    def generate_summary(self):
        """Generate test summary."""
        total_tests = len(self.results["tests"])
        passed = len([t for t in self.results["tests"] if t["status"] == "PASS"])
        failed = len([t for t in self.results["tests"] if t["status"] == "FAIL"])
        warned = len([t for t in self.results["tests"] if t["status"] == "WARN"])
        skipped = len([t for t in self.results["tests"] if t["status"] == "SKIP"])
        
        self.results["summary"] = {
            "total_tests": total_tests,
            "passed": passed,
            "failed": failed,
            "warned": warned,
            "skipped": skipped,
            "success_rate": round((passed / total_tests * 100) if total_tests > 0 else 0, 2)
        }
        
        print("\n" + "=" * 50)
        print("📊 E2E TEST SUMMARY")
        print("=" * 50)
        print(f"Total Tests: {total_tests}")
        print(f"✅ Passed: {passed}")
        print(f"❌ Failed: {failed}")
        print(f"⚠️  Warnings: {warned}")
        print(f"⏭️  Skipped: {skipped}")
        print(f"🎯 Success Rate: {self.results['summary']['success_rate']:.1f}%")
        
        if self.results["failed_tests"]:
            print("\n❌ Failed Tests:")
            for failure in self.results["failed_tests"]:
                print(f"  • {failure}")
        
        if self.results["performance_metrics"]:
            print("\n⚡ Performance Metrics:")
            for endpoint, metrics in self.results["performance_metrics"].items():
                print(f"  {endpoint}: {metrics['avg_response_time']}ms avg")
    
    def save_report(self):
        """Save test report."""
        report_file = self.root_path / "logs" / f"e2e_test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        with open(report_file, 'w') as f:
            json.dump(self.results, f, indent=2)
        
        print(f"\n📄 Full test report saved to: {report_file}")
        return report_file
    
    def run_full_suite(self, start_server=True):
        """Run the complete E2E test suite."""
        print("🧪 SINCOR End-to-End Test Suite Starting...")
        print("=" * 60)
        
        try:
            # Start server if requested
            if start_server:
                if not self.start_app_server():
                    print("❌ Cannot run tests - server failed to start")
                    return False
            
            # Run all test suites
            self.test_basic_connectivity()
            self.test_api_endpoints()
            self.test_lead_capture_flow()
            self.test_agent_functionality()
            self.test_database_operations()
            self.test_file_operations()
            self.test_performance_metrics()
            
            # Generate summary and save report
            self.generate_summary()
            report_file = self.save_report()
            
            return self.results["summary"]["success_rate"] > 70
            
        finally:
            if start_server:
                self.stop_app_server()


if __name__ == "__main__":
    # Allow custom base URL from command line
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5000"
    
    test_suite = E2ETestSuite(base_url)
    success = test_suite.run_full_suite(start_server=True)
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)