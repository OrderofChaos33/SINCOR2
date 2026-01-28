#!/usr/bin/env python3
"""
Quick MVP Test - Check what's working in SINCOR2
"""

import os
import sys

print("=" * 60)
print("SINCOR2 MVP DIAGNOSTIC")
print("=" * 60)

# Test 1: Environment Variables
print("\n[1] Environment Check")
env_file = ".env"
if os.path.exists(env_file):
    print(f"✓ {env_file} exists")
    with open(env_file) as f:
        content = f.read()
        has_anthropic = "ANTHROPIC_API_KEY" in content and "your_" not in content.split("ANTHROPIC_API_KEY")[1].split("\n")[0]
        has_paypal_id = "PAYPAL_REST_API_ID" in content
        has_paypal_secret = "PAYPAL_REST_API_SECRET" in content

        print(f"  ANTHROPIC_API_KEY: {'✓ Set' if has_anthropic else '✗ Not set (using placeholder)'}")
        print(f"  PAYPAL_REST_API_ID: {'✓ Set' if has_paypal_id else '✗ Not set'}")
        print(f"  PAYPAL_REST_API_SECRET: {'✓ Set' if has_paypal_secret else '✗ Not set'}")
else:
    print(f"✗ {env_file} missing")

# Test 2: Core Modules
print("\n[2] Core Module Imports")
modules_to_test = [
    "waitlist_system",
    "monetization_engine",
    "paypal_integration",
    "cortecs_core",
    "agency_kernel",
    "swarm_coordination"
]

working_modules = []
for module in modules_to_test:
    try:
        __import__(module)
        print(f"✓ {module}")
        working_modules.append(module)
    except Exception as e:
        print(f"✗ {module}: {str(e)[:50]}")

# Test 3: Database
print("\n[3] Database Check")
data_dir = "data"
if os.path.exists(data_dir):
    print(f"✓ {data_dir}/ exists")
    files = os.listdir(data_dir)
    print(f"  Files: {len(files)} found")
    for f in files[:5]:
        print(f"    - {f}")
else:
    print(f"✗ {data_dir}/ missing")
    os.makedirs(data_dir, exist_ok=True)
    print(f"✓ Created {data_dir}/")

# Test 4: Templates
print("\n[4] Templates Check")
templates_dir = "templates"
if os.path.exists(templates_dir):
    templates = [f for f in os.listdir(templates_dir) if f.endswith('.html')]
    print(f"✓ {len(templates)} HTML templates found")
    for t in templates[:5]:
        print(f"  - {t}")
else:
    print(f"✗ {templates_dir}/ missing")

# Test 5: Agents
print("\n[5] Agents Check")
agents_dir = "agents"
if os.path.exists(agents_dir):
    agents = [f for f in os.listdir(agents_dir) if f.endswith('.yaml')]
    print(f"✓ {len(agents)} agent YAML files found")
else:
    print(f"✗ {agents_dir}/ missing")

# Test 6: Flask App Quick Import
print("\n[6] Flask App Import Test")
try:
    # Don't actually run it, just test if it can be imported
    import app
    print("✓ app.py imports successfully")
except Exception as e:
    print(f"✗ app.py import failed: {str(e)[:80]}")

# MVP Assessment
print("\n" + "=" * 60)
print("MVP READINESS ASSESSMENT")
print("=" * 60)

mvp_score = 0
total_checks = 6

if os.path.exists(".env"):
    mvp_score += 1
if len(working_modules) >= 4:
    mvp_score += 1
if os.path.exists(data_dir):
    mvp_score += 1
if os.path.exists(templates_dir):
    mvp_score += 1
if os.path.exists(agents_dir):
    mvp_score += 1

print(f"\nReadiness Score: {mvp_score}/{total_checks} ({int(mvp_score/total_checks*100)}%)")

if mvp_score >= 5:
    print("Status: ✓ MVP READY - Can launch with basic functionality")
    print("\nNext steps:")
    print("1. Set ANTHROPIC_API_KEY in .env (if needed for agents)")
    print("2. Run: python app.py")
    print("3. Visit: http://localhost:5000")
elif mvp_score >= 3:
    print("Status: ⚠ NEEDS FIXES - Close to MVP but missing critical pieces")
else:
    print("Status: ✗ NOT READY - Major components missing")

print("=" * 60)
