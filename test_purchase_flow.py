"""
Test script for end-to-end purchase → agent provisioning flow
Tests the complete customer journey from payment to dashboard access
"""

import requests
import json
import time
from datetime import datetime

BASE_URL = "http://localhost:5000"

def print_section(title):
    """Print a formatted section header"""
    print("\n" + "="*60)
    print(f"  {title}")
    print("="*60)

def test_purchase_flow(plan_type="professional", amount="597.00"):
    """
    Test the complete purchase flow for a specific plan

    Args:
        plan_type: starter, professional, or enterprise
        amount: payment amount as string
    """
    print_section(f"Testing {plan_type.upper()} Plan Purchase Flow")

    # Step 1: Simulate PayPal payment completion
    print("\n[1/5] Simulating PayPal payment completion...")

    # Use unique email with timestamp to avoid duplicate account errors
    timestamp = int(time.time())
    payment_data = {
        "email": f"test.{plan_type}.{timestamp}@example.com",
        "name": f"Test {plan_type.title()} User",
        "plan": plan_type,
        "paymentID": f"TEST_PAY_{timestamp}_{plan_type.upper()}",
        "amount": amount
    }

    print(f"   Payment Data: {json.dumps(payment_data, indent=6)}")

    try:
        response = requests.post(
            f"{BASE_URL}/api/purchase/complete",
            json=payment_data,
            timeout=10
        )

        print(f"   Response Status: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            print(f"   Response: {json.dumps(result, indent=6)}")

            if not result.get('success'):
                print(f"   [FAIL] Purchase failed: {result.get('error', 'Unknown error')}")
                return False

            customer_id = result.get('customer_id')
            agent_count = result.get('agent_count')
            dashboard_url = result.get('dashboard_url')

            print(f"   [OK] Purchase successful!")
            print(f"   Customer ID: {customer_id}")
            print(f"   Agents Provisioned: {agent_count}")
            print(f"   Dashboard URL: {dashboard_url}")

            # Verify agent count matches plan tier
            expected_agents = {
                'starter': 10,
                'professional': 30,
                'enterprise': 42
            }

            if agent_count == expected_agents.get(plan_type):
                print(f"   [OK] Agent count correct ({agent_count} agents)")
            else:
                print(f"   [FAIL] Agent count mismatch (expected {expected_agents.get(plan_type)}, got {agent_count})")
                return False

        else:
            print(f"   [FAIL] HTTP Error: {response.status_code}")
            print(f"   Response: {response.text}")
            return False

    except requests.exceptions.RequestException as e:
        print(f"   [FAIL] Request failed: {e}")
        return False

    # Step 2: Verify customer agents endpoint
    print("\n[2/5] Verifying customer agents endpoint...")

    try:
        response = requests.get(
            f"{BASE_URL}/api/customer/agents/{customer_id}",
            timeout=10
        )

        print(f"   Response Status: {response.status_code}")

        if response.status_code == 200:
            result = response.json()

            if result.get('success'):
                agents = result.get('agents', [])
                print(f"   [OK] Agents endpoint working")
                print(f"   Agents returned: {len(agents)}")
                print(f"   Customer name: {result.get('customer_name')}")

                # Display sample agent
                if agents:
                    sample_agent = agents[0]
                    print(f"   Sample Agent: {sample_agent.get('name')} - {sample_agent.get('status')}")
            else:
                print(f"   [FAIL] Agents endpoint failed: {result.get('error')}")
                return False
        else:
            print(f"   [FAIL] HTTP Error: {response.status_code}")
            return False

    except requests.exceptions.RequestException as e:
        print(f"   [FAIL] Request failed: {e}")
        return False

    # Step 3: Verify customer analytics endpoint
    print("\n[3/5] Verifying customer analytics endpoint...")

    try:
        response = requests.get(
            f"{BASE_URL}/api/customer/analytics/{customer_id}",
            timeout=10
        )

        print(f"   Response Status: {response.status_code}")

        if response.status_code == 200:
            result = response.json()

            if result.get('success'):
                analytics = result.get('analytics', {})
                print(f"   [OK] Analytics endpoint working")
                print(f"   Active Agents: {analytics.get('active_agents')}")
                print(f"   Plan Type: {analytics.get('plan_type')}")
                print(f"   Tasks Today: {analytics.get('tasks_today')}")
                print(f"   Success Rate: {analytics.get('success_rate')}%")
            else:
                print(f"   [FAIL] Analytics endpoint failed: {result.get('error')}")
                return False
        else:
            print(f"   [FAIL] HTTP Error: {response.status_code}")
            return False

    except requests.exceptions.RequestException as e:
        print(f"   [FAIL] Request failed: {e}")
        return False

    # Step 4: Verify dashboard page loads
    print("\n[4/5] Verifying dashboard page loads...")

    try:
        # Use the dashboard_url returned by the purchase API
        response = requests.get(
            f"{BASE_URL}{dashboard_url}",
            timeout=10
        )

        print(f"   Response Status: {response.status_code}")

        if response.status_code == 200:
            html_content = response.text

            # Check for key elements in dashboard HTML
            checks = [
                ("SINCOR branding", "SINCOR" in html_content),
                ("Customer name placeholder", "customer-name" in html_content),
                ("Agents grid", "agents-grid" in html_content),
                ("Statistics cards", "stat-active-agents" in html_content),
                ("Task modal", "createTaskModal" in html_content),
                ("Toast notifications", "showToast" in html_content),
            ]

            all_checks_passed = True
            for check_name, check_result in checks:
                if check_result:
                    print(f"   [OK] {check_name} found")
                else:
                    print(f"   [FAIL] {check_name} missing")
                    all_checks_passed = False

            if not all_checks_passed:
                return False

        else:
            print(f"   [FAIL] HTTP Error: {response.status_code}")
            return False

    except requests.exceptions.RequestException as e:
        print(f"   [FAIL] Request failed: {e}")
        return False

    # Step 5: Test task creation endpoint (optional)
    print("\n[5/5] Testing task creation endpoint...")

    try:
        task_data = {
            "customer_id": customer_id,
            "agent_id": f"agent_{customer_id}_001",
            "title": "Test Task - Lead Generation",
            "description": "Generate 100 B2B SaaS leads in the healthcare sector"
        }

        response = requests.post(
            f"{BASE_URL}/api/customer/task/create",
            json=task_data,
            timeout=10
        )

        print(f"   Response Status: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            print(f"   Response: {json.dumps(result, indent=6)}")

            if result.get('success'):
                print(f"   [OK] Task creation working")
                print(f"   Task ID: {result.get('task_id')}")
            else:
                # Task creation might not be fully implemented yet
                print(f"   [WARN] Task creation not yet implemented (expected)")
        else:
            print(f"   [WARN] Task endpoint not ready (status {response.status_code})")

    except requests.exceptions.RequestException as e:
        print(f"   [WARN] Task endpoint not accessible: {e}")

    print("\n" + "="*60)
    print(f"  [PASS] {plan_type.upper()} Plan Test PASSED")
    print("="*60)

    return True

def test_all_plans():
    """Test all three pricing tiers"""
    print_section("SINCOR Purchase Flow End-to-End Test")
    print(f"Test Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Base URL: {BASE_URL}")

    plans = [
        ("starter", "297.00"),
        ("professional", "597.00"),
        ("enterprise", "1497.00")
    ]

    results = {}

    for plan_type, amount in plans:
        try:
            results[plan_type] = test_purchase_flow(plan_type, amount)
            time.sleep(1)  # Brief pause between tests
        except Exception as e:
            print(f"\n[FAIL] Unexpected error testing {plan_type}: {e}")
            results[plan_type] = False

    # Final summary
    print_section("Test Summary")

    for plan_type, passed in results.items():
        status = "[PASS]" if passed else "[FAIL]"
        print(f"{plan_type.title():20} {status}")

    all_passed = all(results.values())

    print("\n" + "="*60)
    if all_passed:
        print("  [SUCCESS] ALL TESTS PASSED - System is working correctly!")
    else:
        print("  [WARNING] SOME TESTS FAILED - Please review errors above")
    print("="*60)

    return all_passed

if __name__ == "__main__":
    try:
        # Test if server is running
        print("Checking if server is running...")
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        print(f"[OK] Server is running (status: {response.status_code})\n")
    except requests.exceptions.RequestException:
        print(f"[FAIL] Server is not running at {BASE_URL}")
        print("Please start the Flask app first: python app.py")
        exit(1)

    # Run all tests
    success = test_all_plans()
    exit(0 if success else 1)
