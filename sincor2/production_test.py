"""
SINCOR Production Testing Suite
Comprehensive tests for production deployment
"""

import requests
import time
import json
from datetime import datetime

BASE_URL = "http://localhost:5000"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'

def print_test(name, passed, message=""):
    status = f"{Colors.GREEN}[PASS]{Colors.RESET}" if passed else f"{Colors.RED}[FAIL]{Colors.RESET}"
    print(f"{status} | {name}")
    if message:
        print(f"       {message}")

def print_section(title):
    print(f"\n{Colors.BLUE}{'='*60}{Colors.RESET}")
    print(f"{Colors.BLUE}{title}{Colors.RESET}")
    print(f"{Colors.BLUE}{'='*60}{Colors.RESET}\n")

# ============================================================
# DASHBOARD TESTS
# ============================================================

def test_dashboards():
    print_section("DASHBOARD TESTS")

    dashboards = [
        ("/", "Home Page"),
        ("/agent-steering", "Agent Steering"),
        ("/agent-chat", "Agent Chat"),
        ("/agent-observability", "Agent Observability"),
        ("/dashboard", "System Dashboard"),
        ("/test-analytics", "Test Analytics"),
        ("/health", "Health Check")
    ]

    for url, name in dashboards:
        try:
            response = requests.get(f"{BASE_URL}{url}", timeout=5)
            passed = response.status_code == 200
            size = len(response.content)
            print_test(f"{name} ({url})", passed, f"Status: {response.status_code}, Size: {size} bytes")
        except Exception as e:
            print_test(f"{name} ({url})", False, f"Error: {str(e)}")

# ============================================================
# API ENDPOINT TESTS
# ============================================================

def test_api_endpoints():
    print_section("API ENDPOINT TESTS")

    # Test health check
    try:
        response = requests.get(f"{BASE_URL}/health")
        passed = response.status_code == 200
        print_test("Health Check API", passed, f"Status: {response.status_code}")
    except Exception as e:
        print_test("Health Check API", False, f"Error: {str(e)}")

    # Test agent analytics health
    try:
        response = requests.get(f"{BASE_URL}/api/agent-analytics/health-check")
        data = response.json()
        passed = data.get('success', False)
        db_type = data.get('database_type', 'unknown')
        print_test("Agent Analytics Health", passed, f"Database: {db_type}")
    except Exception as e:
        print_test("Agent Analytics Health", False, f"Error: {str(e)}")

    # Test agent summary
    try:
        response = requests.get(f"{BASE_URL}/api/agent-analytics/agent-summary")
        data = response.json()
        passed = data.get('success', False)
        count = data.get('agent_count', 0)
        print_test("Agent Summary API", passed, f"Agents: {count}")
    except Exception as e:
        print_test("Agent Summary API", False, f"Error: {str(e)}")

    # Test agent list
    try:
        response = requests.get(f"{BASE_URL}/api/agents/list")
        data = response.json()
        passed = data.get('success', False)
        count = data.get('count', 0)
        print_test("Agent List API", passed, f"Agents: {count}")
    except Exception as e:
        print_test("Agent List API", False, f"Error: {str(e)}")

# ============================================================
# DATABASE TESTS
# ============================================================

def test_database():
    print_section("DATABASE TESTS")

    # Test metrics retrieval
    try:
        response = requests.get(f"{BASE_URL}/api/agent-analytics/metrics?limit=10")
        data = response.json()
        passed = data.get('success', False)
        count = data.get('count', 0)
        print_test("Database - Agent Metrics", passed, f"Records: {count}")
    except Exception as e:
        print_test("Database - Agent Metrics", False, f"Error: {str(e)}")

    # Test interactions
    try:
        response = requests.get(f"{BASE_URL}/api/agent-analytics/interactions?limit=10")
        data = response.json()
        passed = data.get('success', False)
        count = data.get('count', 0)
        print_test("Database - Interactions", passed, f"Records: {count}")
    except Exception as e:
        print_test("Database - Interactions", False, f"Error: {str(e)}")

    # Test tasks
    try:
        response = requests.get(f"{BASE_URL}/api/agent-analytics/tasks?limit=10")
        data = response.json()
        passed = data.get('success', False)
        count = data.get('count', 0)
        print_test("Database - Tasks", passed, f"Records: {count}")
    except Exception as e:
        print_test("Database - Tasks", False, f"Error: {str(e)}")

# ============================================================
# SECURITY TESTS
# ============================================================

def test_security():
    print_section("SECURITY TESTS")

    # Test rate limiting headers
    try:
        response = requests.get(f"{BASE_URL}/health")
        has_security_headers = 'X-Content-Type-Options' in response.headers
        print_test("Security Headers Present", has_security_headers,
                  f"X-Content-Type-Options: {response.headers.get('X-Content-Type-Options', 'Missing')}")
    except Exception as e:
        print_test("Security Headers", False, f"Error: {str(e)}")

    # Test HSTS
    try:
        response = requests.get(f"{BASE_URL}/health")
        has_hsts = 'Strict-Transport-Security' in response.headers
        print_test("HSTS Header", has_hsts,
                  f"Value: {response.headers.get('Strict-Transport-Security', 'Missing')}")
    except Exception as e:
        print_test("HSTS Header", False, f"Error: {str(e)}")

    # Test CSP
    try:
        response = requests.get(f"{BASE_URL}/health")
        has_csp = 'Content-Security-Policy' in response.headers
        print_test("Content Security Policy", has_csp,
                  f"Present: {has_csp}")
    except Exception as e:
        print_test("CSP Header", False, f"Error: {str(e)}")

# ============================================================
# PERFORMANCE TESTS
# ============================================================

def test_performance():
    print_section("PERFORMANCE TESTS")

    # Test response times
    endpoints = [
        "/health",
        "/",
        "/agent-steering",
        "/api/agent-analytics/health-check"
    ]

    for endpoint in endpoints:
        try:
            start = time.time()
            response = requests.get(f"{BASE_URL}{endpoint}", timeout=10)
            duration = (time.time() - start) * 1000
            passed = duration < 2000  # Should be under 2 seconds
            print_test(f"Response Time - {endpoint}", passed, f"{duration:.0f}ms")
        except Exception as e:
            print_test(f"Response Time - {endpoint}", False, f"Error: {str(e)}")

# ============================================================
# INTEGRATION TESTS
# ============================================================

def test_agent_steering_workflow():
    print_section("AGENT STEERING WORKFLOW TEST")

    # Step 1: Get agent list
    try:
        response = requests.get(f"{BASE_URL}/api/agents/list")
        data = response.json()
        agents = data.get('agents', [])
        passed = len(agents) > 0
        print_test("Step 1: Load Agents", passed, f"Found {len(agents)} agents")

        if not passed:
            print("Workflow test stopped - no agents available")
            return

        # Step 2: Send directive
        agent_id = agents[0]['id']
        directive_data = {
            'agent_id': agent_id,
            'directive': 'Test directive for production testing',
            'priority': 'low'
        }

        response = requests.post(
            f"{BASE_URL}/api/agents/directive",
            json=directive_data,
            headers={'Content-Type': 'application/json'}
        )
        data = response.json()
        passed = data.get('success', False)
        print_test("Step 2: Send Directive", passed, f"Response: {data.get('message', '')}")

    except Exception as e:
        print_test("Agent Steering Workflow", False, f"Error: {str(e)}")

# ============================================================
# RAILWAY DEPLOYMENT CHECKS
# ============================================================

def test_railway_readiness():
    print_section("RAILWAY DEPLOYMENT READINESS")

    import os

    # Check required files
    required_files = [
        ('Dockerfile', 'Docker configuration'),
        ('Procfile', 'Process definition'),
        ('railway.json', 'Railway config'),
        ('requirements.txt', 'Python dependencies'),
        ('app.py', 'Main application')
    ]

    for filename, description in required_files:
        file_path = filename
        exists = os.path.exists(file_path)
        print_test(f"File: {filename}", exists, description)

    # Check environment variables guidance
    print("\n" + Colors.YELLOW + "Required Environment Variables for Railway:" + Colors.RESET)
    env_vars = [
        "SECRET_KEY",
        "JWT_SECRET_KEY",
        "ANTHROPIC_API_KEY",
        "DATABASE_URL (auto-set by Railway PostgreSQL)",
        "ADMIN_USERNAME (optional)",
        "ADMIN_PASSWORD (optional)"
    ]
    for var in env_vars:
        print(f"  • {var}")

# ============================================================
# MAIN TEST RUNNER
# ============================================================

def run_all_tests():
    print(f"\n{Colors.GREEN}{'='*60}{Colors.RESET}")
    print(f"{Colors.GREEN}SINCOR PRODUCTION TEST SUITE{Colors.RESET}")
    print(f"{Colors.GREEN}{'='*60}{Colors.RESET}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Target: {BASE_URL}")

    # Run all test suites
    test_dashboards()
    test_api_endpoints()
    test_database()
    test_security()
    test_performance()
    test_agent_steering_workflow()
    test_railway_readiness()

    # Summary
    print_section("TEST SUMMARY")
    print(f"{Colors.GREEN}Production testing complete!{Colors.RESET}")
    print(f"\nNext steps:")
    print(f"  1. Review any failed tests above")
    print(f"  2. Fix issues if any")
    print(f"  3. Set environment variables in Railway")
    print(f"  4. Deploy to Railway")
    print(f"  5. Run this test suite against Railway URL")
    print(f"\n{Colors.BLUE}Deploy command:{Colors.RESET}")
    print(f"  git push railway main")

if __name__ == '__main__':
    try:
        run_all_tests()
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Testing interrupted by user{Colors.RESET}")
    except Exception as e:
        print(f"\n{Colors.RED}Fatal error: {str(e)}{Colors.RESET}")
