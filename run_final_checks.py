#!/usr/bin/env python3
"""
SINCOR Final Validation Checks

Comprehensive validation script that runs all checks before deployment
or after fixing issues to ensure system stability.
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime

class FinalValidation:
    """Final validation checks for SINCOR deployment readiness."""
    
    def __init__(self):
        self.root_path = Path(__file__).parent
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "validation_status": "RUNNING",
            "checks": [],
            "deployment_ready": False,
            "critical_blockers": [],
            "recommendations": []
        }
    
    def log_check(self, check_name: str, status: str, details: str = "", blocker: bool = False):
        """Log a validation check result."""
        check = {
            "name": check_name,
            "status": status,
            "details": details,
            "is_blocker": blocker,
            "timestamp": datetime.now().isoformat()
        }
        self.results["checks"].append(check)
        
        if status == "FAIL" and blocker:
            self.results["critical_blockers"].append(f"{check_name}: {details}")
        
        status_emoji = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "INFO": "ℹ️"}
        blocker_flag = " [BLOCKER]" if blocker else ""
        print(f"{status_emoji.get(status, '❓')} {check_name}{blocker_flag}: {details}")
    
    def run_crash_diagnostics(self):
        """Run the crash diagnostics script."""
        print("\n🔍 Running Crash Diagnostics...")
        print("=" * 40)
        
        try:
            result = subprocess.run(
                [sys.executable, "test_crash_diagnostics.py"],
                cwd=str(self.root_path),
                capture_output=True,
                text=True,
                timeout=120  # 2 minute timeout
            )
            
            if result.returncode == 0:
                self.log_check("Crash Diagnostics", "PASS", "All critical checks passed")
            elif result.returncode < 5:  # Some warnings but not critical
                self.log_check("Crash Diagnostics", "WARN", f"Some warnings detected (code {result.returncode})")
            else:
                self.log_check("Crash Diagnostics", "FAIL", f"Critical issues found (code {result.returncode})", blocker=True)
            
            # Print diagnostics output
            if result.stdout:
                print("Diagnostics Output:")
                print(result.stdout)
            
            if result.stderr and result.returncode != 0:
                print("Diagnostics Errors:")
                print(result.stderr)
                
        except subprocess.TimeoutExpired:
            self.log_check("Crash Diagnostics", "FAIL", "Diagnostics timed out", blocker=True)
        except Exception as e:
            self.log_check("Crash Diagnostics", "FAIL", f"Failed to run diagnostics: {e}", blocker=True)
    
    def run_e2e_tests(self):
        """Run the end-to-end test suite."""
        print("\n🧪 Running End-to-End Tests...")
        print("=" * 40)
        
        try:
            result = subprocess.run(
                [sys.executable, "test_e2e_validation.py"],
                cwd=str(self.root_path),
                capture_output=True,
                text=True,
                timeout=180  # 3 minute timeout
            )
            
            if result.returncode == 0:
                self.log_check("E2E Tests", "PASS", "All tests passed with good success rate")
            else:
                self.log_check("E2E Tests", "FAIL", f"Tests failed or low success rate (code {result.returncode})", blocker=True)
            
            # Print test output
            if result.stdout:
                print("E2E Test Output:")
                print(result.stdout)
            
            if result.stderr and result.returncode != 0:
                print("E2E Test Errors:")
                print(result.stderr)
                
        except subprocess.TimeoutExpired:
            self.log_check("E2E Tests", "FAIL", "E2E tests timed out", blocker=True)
        except Exception as e:
            self.log_check("E2E Tests", "FAIL", f"Failed to run E2E tests: {e}", blocker=True)
    
    def check_environment_config(self):
        """Check critical environment configuration."""
        print("\n⚙️ Checking Environment Configuration...")
        
        # Critical environment variables for production
        critical_env_vars = [
            ("FLASK_SECRET_KEY", True),
            ("EMAIL_FROM", False),
            ("SMTP_HOST", False)
        ]
        
        missing_critical = []
        
        for var_name, is_critical in critical_env_vars:
            value = os.getenv(var_name, "")
            if value:
                self.log_check(f"ENV: {var_name}", "PASS", "Configured")
            else:
                if is_critical:
                    missing_critical.append(var_name)
                    self.log_check(f"ENV: {var_name}", "FAIL", "Not configured", blocker=True)
                else:
                    self.log_check(f"ENV: {var_name}", "WARN", "Not configured")
        
        # Check for production.env file
        prod_env_file = self.root_path / "config" / "production.env"
        if prod_env_file.exists():
            self.log_check("Production Config", "PASS", "production.env found")
        else:
            self.log_check("Production Config", "WARN", "production.env missing")
    
    def check_security_settings(self):
        """Check security configuration."""
        print("\n🔒 Checking Security Settings...")
        
        # Check Flask secret key strength
        secret_key = os.getenv("FLASK_SECRET_KEY", "")
        if not secret_key:
            self.log_check("Flask Secret Key", "FAIL", "Not set", blocker=True)
        elif secret_key == "sincor-admin-secret-key-2025":  # Default key
            self.log_check("Flask Secret Key", "FAIL", "Using default key - security risk", blocker=True)
        elif len(secret_key) < 32:
            self.log_check("Flask Secret Key", "WARN", "Short key - consider longer")
        else:
            self.log_check("Flask Secret Key", "PASS", "Strong secret key set")
        
        # Check for sensitive files in logs
        log_dir = self.root_path / "logs"
        if log_dir.exists():
            sensitive_patterns = ["password", "secret", "key", "token"]
            for log_file in log_dir.glob("*.log"):
                try:
                    with open(log_file, 'r', encoding='utf-8') as f:
                        content = f.read().lower()
                        found_sensitive = [p for p in sensitive_patterns if p in content]
                        if found_sensitive:
                            self.log_check(f"Security: {log_file.name}", "WARN", f"May contain sensitive data: {found_sensitive}")
                except:
                    continue
    
    def check_production_readiness(self):
        """Check production readiness indicators."""
        print("\n🚀 Checking Production Readiness...")
        
        # Check for debug mode indicators
        app_file = self.root_path / "sincor_app.py"
        if app_file.exists():
            try:
                with open(app_file, 'r') as f:
                    content = f.read()
                    if "debug=True" in content:
                        self.log_check("Debug Mode", "FAIL", "Debug mode enabled - not production ready", blocker=True)
                    else:
                        self.log_check("Debug Mode", "PASS", "Debug mode not explicitly enabled")
            except:
                self.log_check("Debug Mode", "WARN", "Cannot check debug mode setting")
        
        # Check for test data in production
        leads_csv = self.root_path / "outputs" / "leads.csv"
        if leads_csv.exists():
            try:
                with open(leads_csv, 'r') as f:
                    content = f.read()
                    if "test@example.com" in content or "Test Customer" in content:
                        self.log_check("Test Data", "WARN", "Test data found in leads.csv")
                    else:
                        self.log_check("Test Data", "PASS", "No obvious test data found")
            except:
                pass
        
        # Check database file sizes
        data_dir = self.root_path / "data"
        if data_dir.exists():
            large_dbs = []
            for db_file in data_dir.glob("*.db"):
                size_mb = db_file.stat().st_size / (1024 * 1024)
                if size_mb > 100:  # More than 100MB
                    large_dbs.append(f"{db_file.name} ({size_mb:.1f}MB)")
            
            if large_dbs:
                self.log_check("Database Size", "WARN", f"Large databases: {', '.join(large_dbs)}")
            else:
                self.log_check("Database Size", "PASS", "Database sizes reasonable")
    
    def generate_deployment_checklist(self):
        """Generate deployment checklist."""
        checklist = [
            "✅ Run final validation script (this script)",
            "✅ Backup current production database",
            "🔧 Set production environment variables",
            "🔒 Ensure strong Flask secret key is set",
            "📧 Configure SMTP settings for email",
            "🗄️ Verify database migrations are current",
            "🌐 Test all critical endpoints manually",
            "📊 Monitor logs after deployment",
            "🚨 Have rollback plan ready",
            "📞 Prepare incident response contacts"
        ]
        
        print("\n📋 Deployment Checklist:")
        for item in checklist:
            print(f"  {item}")
        
        self.results["deployment_checklist"] = checklist
    
    def determine_deployment_readiness(self):
        """Determine if system is ready for deployment."""
        critical_blockers = len(self.results["critical_blockers"])
        
        if critical_blockers == 0:
            self.results["deployment_ready"] = True
            self.results["validation_status"] = "PASS"
            print("\n🎉 DEPLOYMENT READY!")
            print("All critical checks passed. System is ready for deployment.")
        else:
            self.results["deployment_ready"] = False
            self.results["validation_status"] = "BLOCKED"
            print(f"\n🚫 DEPLOYMENT BLOCKED!")
            print(f"Found {critical_blockers} critical blockers that must be resolved:")
            for blocker in self.results["critical_blockers"]:
                print(f"  ❌ {blocker}")
        
        # Add recommendations
        if not self.results["deployment_ready"]:
            self.results["recommendations"].extend([
                "Fix all critical blockers before attempting deployment",
                "Run this validation script again after fixes",
                "Consider staging environment testing first",
                "Review logs for additional issues"
            ])
        else:
            self.results["recommendations"].extend([
                "Deploy during low-traffic hours",
                "Monitor application logs closely after deployment",
                "Have rollback plan ready",
                "Test critical functionality after deployment"
            ])
    
    def save_validation_report(self):
        """Save validation report."""
        report_file = self.root_path / "logs" / f"final_validation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        with open(report_file, 'w') as f:
            json.dump(self.results, f, indent=2)
        
        print(f"\n📄 Full validation report saved to: {report_file}")
        return report_file
    
    def run_final_validation(self):
        """Run complete final validation."""
        print("🔍 SINCOR Final Validation Starting...")
        print("=" * 50)
        
        # Run all validation checks
        self.run_crash_diagnostics()
        self.run_e2e_tests()
        self.check_environment_config()
        self.check_security_settings()
        self.check_production_readiness()
        
        # Generate results
        print("\n" + "=" * 50)
        self.determine_deployment_readiness()
        self.generate_deployment_checklist()
        
        # Save report
        report_file = self.save_validation_report()
        
        return self.results["deployment_ready"]


if __name__ == "__main__":
    validator = FinalValidation()
    deployment_ready = validator.run_final_validation()
    
    # Exit with appropriate code
    sys.exit(0 if deployment_ready else 1)