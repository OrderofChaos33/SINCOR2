#!/usr/bin/env python3
"""
Pre-deployment validation script for DigitalOcean migration
Checks if all dependencies and configurations are ready
"""

import os
import sys
import importlib.util

def check_file_exists(filepath, description):
    """Check if required file exists"""
    if os.path.exists(filepath):
        print(f"✅ {description}: {filepath}")
        return True
    else:
        print(f"❌ MISSING {description}: {filepath}")
        return False

def check_import(module_name):
    """Check if module can be imported"""
    try:
        __import__(module_name)
        print(f"✅ Module: {module_name}")
        return True
    except ImportError as e:
        print(f"⚠️  Module {module_name}: {e}")
        return False

def check_environment_vars():
    """Check critical environment variables"""
    required_vars = [
        'FLASK_ENV',
        'PAYPAL_CLIENT_ID', 
        'PAYPAL_CLIENT_SECRET',
        'FLASK_SECRET_KEY'
    ]
    
    missing = []
    for var in required_vars:
        if os.getenv(var):
            print(f"✅ Environment: {var}")
        else:
            print(f"⚠️  Environment: {var} (will need to be set in DigitalOcean)")
            missing.append(var)
    
    return missing

def main():
    print("🚀 SINCOR DigitalOcean Migration Pre-Check")
    print("=" * 50)
    
    issues = []
    
    # Check required files
    required_files = [
        ("app.py", "Main Flask application"),
        ("requirements.txt", "Python dependencies"),
        ("Procfile", "Process configuration"),
        ("DIGITALOCEAN_MIGRATION.md", "Migration guide"),
        ("config.yaml", "BI Scout configuration")
    ]
    
    for filepath, desc in required_files:
        if not check_file_exists(filepath, desc):
            issues.append(f"Missing file: {filepath}")
    
    print("\n📦 Checking Python Dependencies...")
    critical_modules = ["flask", "gunicorn", "requests"]
    for module in critical_modules:
        if not check_import(module):
            issues.append(f"Missing module: {module}")
    
    print("\n🔧 Checking Environment Configuration...")
    missing_env = check_environment_vars()
    
    print("\n📊 Pre-Check Summary:")
    if not issues and not missing_env:
        print("🎉 ALL SYSTEMS GO! Ready for DigitalOcean deployment!")
        print("\n📋 Next Steps:")
        print("1. Push code to GitHub")
        print("2. Create DigitalOcean App")
        print("3. Add environment variables") 
        print("4. Deploy and test!")
    else:
        print(f"⚠️  Found {len(issues)} issues that need fixing:")
        for issue in issues:
            print(f"   - {issue}")
        
        if missing_env:
            print(f"\n📝 Environment variables to set in DigitalOcean:")
            for var in missing_env:
                print(f"   - {var}")
    
    print(f"\n🌐 Current working directory: {os.getcwd()}")
    print("📁 Make sure you're in the sincor-clean folder!")

if __name__ == "__main__":
    main()